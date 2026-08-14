import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_DIR = Path.home() / ".model-hub"
DB_PATH = DB_DIR / "cookbook.db"

_conn_lock = threading.RLock()
_conn = None


def _get_conn(init=True):
    global _conn
    with _conn_lock:
        if _conn is not None:
            return _conn
        import sqlite3

        DB_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        if init:
            _init_db()
        return _conn


def _init_db():
    conn = _get_conn(init=False)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL DEFAULT '',
            system_prompt TEXT DEFAULT '',
            context     TEXT DEFAULT '{}',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL CHECK(role IN ('system','user','assistant')),
            content     TEXT NOT NULL,
            timestamp   REAL NOT NULL,
            metadata    TEXT DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);

        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  REAL NOT NULL
        );
    """)
    conn.commit()


@dataclass
class Session:
    id: str
    name: str
    model: str
    system_prompt: str = ""
    context: dict = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    message_count: int = 0


@dataclass
class Message:
    id: int = 0
    session_id: str = ""
    role: str = ""
    content: str = ""
    timestamp: float = 0.0
    metadata: dict = field(default_factory=dict)


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


def create_session(name: str = "", model: str = "", system_prompt: str = "") -> Session:
    conn = _get_conn()
    now = time.time()
    sid = _new_id()
    conn.execute(
        "INSERT INTO sessions (id, name, model, system_prompt, context, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, name, model, system_prompt, "{}", now, now),
    )
    conn.commit()
    return Session(id=sid, name=name, model=model, system_prompt=system_prompt, created_at=now, updated_at=now)


def get_session(session_id: str) -> Optional[Session]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    try:
        ctx = json.loads(row["context"]) if row["context"] else {}
    except (json.JSONDecodeError, TypeError):
        ctx = {}
    cnt = conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)).fetchone()[0]
    return Session(
        id=row["id"], name=row["name"], model=row["model"],
        system_prompt=row["system_prompt"], context=ctx,
        created_at=row["created_at"], updated_at=row["updated_at"],
        message_count=cnt,
    )


def list_sessions(limit: int = 50) -> list[Session]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM messages WHERE session_id = s.id) AS msg_count "
        "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        try:
            ctx = json.loads(row["context"]) if row["context"] else {}
        except (json.JSONDecodeError, TypeError):
            ctx = {}
        result.append(Session(
            id=row["id"], name=row["name"], model=row["model"],
            system_prompt=row["system_prompt"], context=ctx,
            created_at=row["created_at"], updated_at=row["updated_at"],
            message_count=row["msg_count"],
        ))
    return result


def update_session(session_id: str, **kwargs):
    conn = _get_conn()
    allowed = {"name", "model", "system_prompt", "context"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    if "context" in updates:
        updates["context"] = json.dumps(updates["context"] if isinstance(updates["context"], dict) else {})
    updates["updated_at"] = time.time()
    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [session_id]
    conn.execute(f"UPDATE sessions SET {sets} WHERE id = ?", vals)
    conn.commit()


def delete_session(session_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def add_message(session_id: str, role: str, content: str, metadata: dict = None) -> Message:
    conn = _get_conn()
    now = time.time()
    cur = conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, now, json.dumps(metadata or {})),
    )
    conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    conn.commit()
    return Message(id=cur.lastrowid, session_id=session_id, role=role, content=content, timestamp=now, metadata=metadata or {})


def get_messages(session_id: str, limit: int = 200) -> list[Message]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [
        Message(id=r["id"], session_id=r["session_id"], role=r["role"],
                content=r["content"], timestamp=r["timestamp"],
                metadata=json.loads(r["metadata"]) if r["metadata"] else {})
        for r in rows
    ]


def get_settings() -> dict:
    conn = _get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = {}
    for r in rows:
        try:
            result[r["key"]] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            result[r["key"]] = r["value"]
    return result


def get_setting(key: str, default=None):
    conn = _get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_setting(key: str, value):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value), time.time()),
    )
    conn.commit()


def set_settings(updates: dict):
    conn = _get_conn()
    now = time.time()
    for key, value in updates.items():
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), now),
        )
    conn.commit()


def get_defaults() -> dict:
    return {
        "ollama_host": "http://localhost:11434",
        "theme": "dark",
        "default_model": "",
        "default_use_case": "coding",
        "max_tokens": 4096,
        "auto_save_sessions": True,
        "stream_responses": True,
    }


def get_settings_with_defaults() -> dict:
    merged = get_defaults()
    merged.update(get_settings())
    return merged


def export_session_json(session_id: str) -> Optional[dict]:
    sess = get_session(session_id)
    if not sess:
        return None
    msgs = get_messages(session_id)
    return {
        "version": "2.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "session": {
            "id": sess.id,
            "name": sess.name,
            "model": sess.model,
            "system_prompt": sess.system_prompt,
            "context": sess.context,
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
        },
        "messages": [
            {"role": m.role, "content": m.content, "timestamp": m.timestamp}
            for m in msgs
        ],
    }


def import_session_json(data: dict) -> Optional[Session]:
    sess_data = data.get("session", {})
    msgs_data = data.get("messages", [])
    sess = create_session(
        name=sess_data.get("name", "Imported"),
        model=sess_data.get("model", ""),
        system_prompt=sess_data.get("system_prompt", ""),
    )
    ctx = sess_data.get("context", {})
    if ctx:
        update_session(sess.id, context=ctx)
    for m in msgs_data:
        add_message(sess.id, m["role"], m["content"])
    return sess
