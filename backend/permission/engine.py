from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import resolve_config

PERMISSION_KEYS = {
    "read", "edit", "glob", "grep", "list", "bash", "task", "skill",
    "webfetch", "websearch", "external_directory", "doom_loop",
    "todowrite", "question", "lsp",
}


class Decision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    @classmethod
    def parse(cls, value: Any) -> "Decision":
        if isinstance(value, Decision):
            return value
        s = str(value).strip().lower()
        if s in ("allow", "yes", "true", "1"):
            return cls.ALLOW
        if s in ("deny", "no", "false", "0"):
            return cls.DENY
        return cls.ASK


@dataclass
class PermissionRule:
    key: str
    decision: Decision
    pattern: str | None = None
    order: int = 0


def _normalize_path(p: str | None) -> str:
    if not p:
        return ""
    return str(p).replace("\\", "/").strip("/")


def _literal_len(pattern: str) -> int:
    return len(pattern.replace("*", "").replace("?", ""))


def _match(pattern: str, target: str | None) -> bool:
    t = _normalize_path(target)
    p = pattern.replace("\\", "/")
    if p.endswith("/**"):
        prefix = p[:-3]
        return t == prefix or t.startswith(prefix + "/") or fnmatch.fnmatchcase(t, p)
    return fnmatch.fnmatchcase(t, p)


class AlwaysAllowStore:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            from ..cookbook import config as _cfg

            db_path = _cfg.CONFIG_DIR / "permissions.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS always_allow (
                project_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                key TEXT NOT NULL,
                pattern TEXT NOT NULL,
                decided_at REAL NOT NULL,
                owner_token TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (project_id, agent, key, pattern)
            )"""
        )
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(always_allow)").fetchall()
        }
        if "owner_token" not in columns:
            try:
                conn.execute(
                    "ALTER TABLE always_allow ADD COLUMN owner_token TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError:
                refreshed = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(always_allow)").fetchall()
                }
                if "owner_token" not in refreshed:
                    raise
        conn.execute(
            """CREATE TABLE IF NOT EXISTS always_allow_claims (
                project_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                key TEXT NOT NULL,
                pattern TEXT NOT NULL,
                owner_token TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (project_id, agent, key, pattern, owner_token)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS remembered_grant_audits (
                grant_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                key TEXT NOT NULL,
                tool_name TEXT NOT NULL DEFAULT '',
                pattern TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                committed_at REAL NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS permission_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        conn.commit()
        return conn

    def _init(self) -> None:
        conn = self._conn()
        try:
            # Serialize contract upgrades so a second first-run constructor
            # cannot erase a grant committed after another upgrader finished.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM permission_meta WHERE key=?",
                (_ALLOW_CONTRACT_META_KEY,),
            ).fetchone()
            if row is not None and row[0] == _ALLOW_CONTRACT_META_VALUE:
                conn.commit()
                return
            if row is not None and row[0] not in _ALLOW_LEGACY_CONTRACT_VALUES:
                raise RuntimeError(
                    f"Unsupported permission contract version: {row[0]}"
                )
            # Older builds silently persisted every ALLOW, including the UI's
            # "Allow Once" choice. Those rows cannot be distinguished from
            # intentional permanent approvals, so fail closed once when
            # upgrading to the explicit-remember contract.
            conn.execute("DELETE FROM always_allow")
            conn.execute("DELETE FROM always_allow_claims")
            conn.execute("DELETE FROM remembered_grant_audits")
            conn.execute(
                "INSERT OR REPLACE INTO permission_meta (key, value) VALUES (?, ?)",
                (_ALLOW_CONTRACT_META_KEY, _ALLOW_CONTRACT_META_VALUE),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def is_allowed(self, project_id: str, agent: str, key: str, target: str | None) -> bool:
        if key == "doom_loop":
            return False
        if target is None:
            return False
        expected = f"{_ALLOW_EXACT_PREFIX}{str(target)}"
        expected_tool = key.split("@", 1)[1] if "@" in key else ""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT 1
                   FROM always_allow_claims AS claim
                   JOIN remembered_grant_audits AS audit
                     ON audit.grant_id = claim.owner_token
                    AND audit.project_id = claim.project_id
                    AND audit.agent = claim.agent
                    AND audit.key = claim.key
                    AND audit.pattern = claim.pattern
                   WHERE claim.project_id=? AND claim.agent=?
                     AND claim.key=? AND claim.pattern=?
                     AND audit.tool_name=? AND audit.schema_version=?
                   LIMIT 1""",
                (
                    project_id,
                    agent,
                    key,
                    expected,
                    expected_tool,
                    _GRANT_AUDIT_SCHEMA_VERSION,
                ),
            ).fetchone()
        return row is not None

    def remember(
        self,
        project_id: str,
        agent: str,
        key: str,
        target: str | None,
        *,
        owner_token: str | None = None,
    ) -> str | None:
        if key == "doom_loop" or target is None:
            return None
        pattern = f"{_ALLOW_EXACT_PREFIX}{str(target)}"
        token = owner_token or uuid.uuid4().hex
        tool_name = key.split("@", 1)[1] if "@" in key else ""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO remembered_grant_audits
                    (grant_id, project_id, agent, key, tool_name, pattern,
                     schema_version, committed_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                (
                    token,
                    project_id,
                    agent,
                    key,
                    tool_name,
                    pattern,
                    _GRANT_AUDIT_SCHEMA_VERSION,
                    time.time(),
                ),
            )
            audit_scope = conn.execute(
                """SELECT project_id, agent, key, tool_name, pattern, schema_version
                   FROM remembered_grant_audits WHERE grant_id=?""",
                (token,),
            ).fetchone()
            if audit_scope != (
                project_id,
                agent,
                key,
                tool_name,
                pattern,
                _GRANT_AUDIT_SCHEMA_VERSION,
            ):
                raise ValueError("grant id is already bound to a different permission scope")
            conn.execute(
                """INSERT OR IGNORE INTO always_allow_claims
                    (project_id, agent, key, pattern, owner_token, created_at)
                    VALUES (?,?,?,?,?,?)""",
                (project_id, agent, key, pattern, token, time.time()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO always_allow (project_id, agent, key, pattern, decided_at, owner_token) VALUES (?,?,?,?,?,?)",
                (project_id, agent, key, pattern, time.time(), token),
            )
            conn.commit()
        return token

    def forget_exact(
        self,
        project_id: str,
        agent: str,
        key: str,
        target: str | None,
        *,
        owner_token: str | None = None,
    ) -> None:
        """Remove one remembered approval without widening the revocation scope."""

        if target is None:
            return
        pattern = f"{_ALLOW_EXACT_PREFIX}{str(target)}"
        with self._conn() as conn:
            if owner_token is None:
                conn.execute(
                    "DELETE FROM always_allow_claims WHERE project_id=? AND agent=? AND key=? AND pattern=?",
                    (project_id, agent, key, pattern),
                )
                conn.execute(
                    "DELETE FROM always_allow WHERE project_id=? AND agent=? AND key=? AND pattern=?",
                    (project_id, agent, key, pattern),
                )
            else:
                conn.execute(
                    "DELETE FROM always_allow_claims WHERE project_id=? AND agent=? AND key=? AND pattern=? AND owner_token=?",
                    (project_id, agent, key, pattern, owner_token),
                )
                remaining = conn.execute(
                    "SELECT 1 FROM always_allow_claims WHERE project_id=? AND agent=? AND key=? AND pattern=? LIMIT 1",
                    (project_id, agent, key, pattern),
                ).fetchone()
                if remaining is None:
                    # Empty-owner rows predate claim ownership and must never be
                    # deleted by a failed modern approval attempt.
                    conn.execute(
                        "DELETE FROM always_allow WHERE project_id=? AND agent=? AND key=? AND pattern=? AND owner_token <> ''",
                        (project_id, agent, key, pattern),
                    )
            conn.commit()

    def forget(self, project_id: str, agent: str | None = None) -> None:
        with self._conn() as conn:
            if agent is None:
                conn.execute(
                    "DELETE FROM always_allow_claims WHERE project_id=?",
                    (project_id,),
                )
                conn.execute("DELETE FROM always_allow WHERE project_id=?", (project_id,))
            else:
                conn.execute(
                    "DELETE FROM always_allow_claims WHERE project_id=? AND agent=?",
                    (project_id, agent),
                )
                conn.execute(
                    "DELETE FROM always_allow WHERE project_id=? AND agent=?",
                    (project_id, agent),
                )
            conn.commit()


def project_id_for(root: str | Path | None) -> str:
    if not root:
        return "default"
    return hashlib.sha1(str(root).encode()).hexdigest()[:16]


DANGER_PATTERNS = (
    "rm -rf /", "rm -rf ~", "rm -rf /*", "rm -rf $HOME",
    ":(){:|:&};:",
    "mkfs", "dd if=/dev/zero",
    "git push --force", "git push -f",
    "chmod -R 000",
)

_SENSITIVE_EXACT_PATH_NAMES = {
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".netrc",
    ".git-credentials",
    ".ssh",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "credentials",
    ".env",
}
_SENSITIVE_PREFIX_PATH_NAMES = (
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".netrc",
    ".git-credentials",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "credentials",
    ".env",
    ".ssh",
)
_SENSITIVE_SUFFIX_SEPARATORS = (".", "_", "-")
_PATH_TOKEN_SPLIT = re.compile(r"[\s/\\'\"`=;&|><():,\[\]{}]+")

_ALLOW_CONTRACT_META_KEY = "always_allow_contract"
_ALLOW_CONTRACT_META_VALUE = "explicit_raw_exact_claim_audit_v4"
_ALLOW_LEGACY_CONTRACT_VALUES = frozenset(
    {"explicit_remember_v2", "explicit_raw_exact_v3"}
)
_ALLOW_EXACT_PREFIX = "exact:"
_GRANT_AUDIT_SCHEMA_VERSION = 1


def _contains_sensitive_path(value: str | None) -> bool:
    if value is not None and not isinstance(value, str):
        return True
    normalized = str(value or "").lower().replace("\\", "/")
    for token in _PATH_TOKEN_SPLIT.split(normalized):
        if not token:
            continue
        if token in _SENSITIVE_EXACT_PATH_NAMES:
            return True
        if any(
            token.startswith(prefix + separator)
            for prefix in _SENSITIVE_PREFIX_PATH_NAMES
            for separator in _SENSITIVE_SUFFIX_SEPARATORS
        ):
            return True
    return False


def is_dangerous(tool: str, target: str | None) -> bool:
    if tool == "run_bash":
        if target is not None and not isinstance(target, str):
            return True
        cmd = str(target or "").lower().replace("\\", "/")
        return any(p.lower() in cmd for p in DANGER_PATTERNS) or _contains_sensitive_path(cmd)
    if tool in ("write_file", "edit", "apply_patch"):
        return _contains_sensitive_path(target)
    return False


def parse_rules(raw: dict[str, Any] | None) -> dict[str, list[PermissionRule]]:
    rules: dict[str, list[PermissionRule]] = {}
    if not raw:
        return rules
    for agent, body in raw.items():
        if not isinstance(body, dict):
            continue
        agent_rules: list[PermissionRule] = []
        for order, (key, val) in enumerate(body.items()):
            if val is None:
                continue
            if isinstance(val, dict):
                for pattern, dec in val.items():
                    agent_rules.append(PermissionRule(key=key, decision=Decision.parse(dec), pattern=pattern, order=order))
            else:
                agent_rules.append(PermissionRule(key=key, decision=Decision.parse(val), pattern=None, order=order))
        rules[agent] = agent_rules
    return rules


class PermissionEngine:
    def __init__(
        self,
        rules: dict[str, list[PermissionRule]] | None = None,
        project_id: str = "default",
        store: AlwaysAllowStore | None = None,
        doom_window: int = 3,
    ):
        self.rules = rules or {}
        self.project_id = project_id
        self.store = store or AlwaysAllowStore()
        self.doom_window = doom_window
        self._history: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=doom_window))

    @classmethod
    def from_config(cls, start_dir: str | Path | None = None, store: AlwaysAllowStore | None = None) -> "PermissionEngine":
        cfg = resolve_config(start_dir)
        rules = parse_rules(cfg.project.permission)
        root = cfg.project_root
        return cls(rules=rules, project_id=project_id_for(root), store=store)

    def evaluate(
        self,
        agent: str,
        key: str,
        target: str | None = None,
        *,
        tool_name: str | None = None,
    ) -> Decision:
        candidates = [r for r in self.rules.get(agent, []) if (r.key == key or r.key == "*") and (r.pattern is None or _match(r.pattern, target))]
        best = max(
            candidates,
            key=lambda r: (
                1 if r.key != "*" else 0,
                _literal_len(r.pattern or "") if r.pattern else -1,
                r.order,
            ),
        ) if candidates else None
        # DENY is a hard policy boundary. A remembered approval from an older
        # or looser configuration may never override it.
        if best is not None and best.decision is Decision.DENY:
            return Decision.DENY
        tool = tool_name or self._tool_for_key(key)
        if tool and is_dangerous(tool, target):
            return Decision.ASK
        remember_scope = f"{key}@{tool_name}" if tool_name else key
        if self.store.is_allowed(self.project_id, agent, remember_scope, target):
            return Decision.ALLOW
        if best is None:
            return Decision.ASK
        if best.decision is Decision.ALLOW:
            return Decision.ALLOW
        return best.decision

    @staticmethod
    def _tool_for_key(key: str) -> str:
        mapping = {"edit": "write_file", "bash": "run_bash", "read": "read_file", "list": "list_files", "webfetch": "web_search"}
        return mapping.get(key, "")

    def remember(
        self,
        agent: str,
        key: str,
        target: str | None,
        *,
        tool_name: str | None = None,
        owner_token: str | None = None,
    ) -> str | None:
        tool = tool_name or self._tool_for_key(key)
        if key == "doom_loop" or target is None or (tool and is_dangerous(tool, target)):
            return None
        remember_scope = f"{key}@{tool_name}" if tool_name else key
        return self.store.remember(
            self.project_id,
            agent,
            remember_scope,
            target,
            owner_token=owner_token,
        )

    @staticmethod
    def new_remember_token() -> str:
        return uuid.uuid4().hex

    def forget_remembered(
        self,
        agent: str,
        key: str,
        target: str | None,
        *,
        tool_name: str | None = None,
        owner_token: str | None = None,
    ) -> None:
        remember_scope = f"{key}@{tool_name}" if tool_name else key
        self.store.forget_exact(
            self.project_id,
            agent,
            remember_scope,
            target,
            owner_token=owner_token,
        )

    def forget(self, agent: str | None = None) -> None:
        self.store.forget(self.project_id, agent)

    def record_tool_call(self, agent: str, tool: str, args: dict) -> bool:
        sig = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
        key = (agent, tool)
        hist = self._history[key]
        prev = list(hist)
        hist.append(sig)
        if len(hist) >= self.doom_window and len(set(hist)) == 1:
            return True
        return False

    def clear_history(self, agent: str | None = None) -> None:
        if agent is None:
            self._history.clear()
        else:
            self._history = defaultdict(lambda: deque(maxlen=self.doom_window), {k: v for k, v in self._history.items() if k[0] != agent})
