# M2 Plan 1: Staged-Change Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist agent file-writes as reviewable staged changes (no disk write until the user applies), with browse/apply/reject/revert HTTP endpoints — fully inert until build mode wires it in Plan 3.

**Architecture:** New `staged_changes` SQLite table in the existing `cookbook.db` (WAL, `_ensure_db()` idiom). One pending row max per `(session_id, path)` — latest write wins, original disk snapshot preserved. A new `backend/agent/staging.py` builds tool-handler overlays (staged write, read-through, listing union) as closures over a session; nothing imports it yet. New read-only file-browse and change-management endpoints in `backend/api.py`.

**Tech Stack:** Python 3.11, sqlite3 (stdlib), Flask, pytest. No new dependencies.

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub`, branch `master`, base commit `9112047`.
- Run tests with the repo venv: `.venv\Scripts\python.exe -m pytest <file> -v` (Windows). Full suite must stay green after every task.
- All ids: `uuid.uuid4().hex[:14]` (matches `sessions` convention).
- Timestamps: `time.time()` REAL columns.
- Path jail posture everywhere: resolve against root, `relative_to` or fail — never trust a stored path (same posture as `_resolve_within_workspaces` in `backend/cookbook/config.py:136-152`).
- Size caps: staged write ≤ 2 MB (tool error string above it); file read endpoint ≤ 1 MB (HTTP 413).
- Change statuses: `pending | applied | rejected | conflict | reverted`. Applied/rejected/conflict/reverted rows are history — never reused; a later write to the same path opens a fresh pending row.
- Nothing in this plan may be called from `agent_chat` or the runner — the store ships dark. `backend/api.py` gets new routes only.
- API error convention (matches workspaces block): `jsonify({"error": ...})` with 400/404/409/413.
- model-hub never imports `lac_pro` (open-core guard test exists).

---

### Task 1: Persistence layer — table + stage/list/get/set functions

**Files:**
- Modify: `backend/cookbook/persistence.py` (table in `_ensure_db()` at the end of the CREATE block, ~line 52; new functions at end of file; `delete_session` at line 185)
- Test: `tests/test_staged_changes.py` (new)

**Interfaces:**
- Produces: `stage_change(session_id: str, run_id: str, root: str, path: str, new_content: str) -> dict` (upsert-pending; raises `ValueError` on jail escape), `list_staged_changes(session_id: str, run_id: str | None = None, status: str | None = None) -> list[dict]` (created_at ASC), `get_staged_change(change_id: str) -> dict | None`, `set_staged_status(change_id: str, status: str) -> None`. Row dict keys: `id, session_id, run_id, root, path, base_hash, old_content, new_content, status, created_at, updated_at`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_staged_changes.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path


def _mk_session():
    from backend.cookbook.persistence import create_session

    return create_session(name="t", model="mock:1b", workspace="default")


def test_stage_change_new_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "src/new.py", "print('hi')\n")
    assert row["status"] == "pending"
    assert row["path"] == "src/new.py"
    assert row["base_hash"] is None
    assert row["old_content"] is None
    assert row["new_content"] == "print('hi')\n"
    assert row["run_id"] == "run1"
    assert len(row["id"]) == 14
    assert not (tmp_path / "src" / "new.py").exists()  # nothing touched disk


def test_stage_change_existing_file_snapshots_disk(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    assert row["base_hash"] == hashlib.sha256(b"original").hexdigest()
    assert row["old_content"] == "original"
    assert f.read_bytes() == b"original"  # disk untouched


def test_stage_change_upsert_keeps_original_snapshot(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    first = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "v1")
    second = persistence.stage_change(sid, "run2", str(tmp_path), "a.txt", "v2")
    assert second["id"] == first["id"]  # same pending row
    assert second["new_content"] == "v2"
    assert second["run_id"] == "run2"  # provenance stamped to latest run
    assert second["base_hash"] == first["base_hash"]  # ORIGINAL snapshot preserved
    assert second["old_content"] == "original"
    pending = persistence.list_staged_changes(sid, status="pending")
    assert len(pending) == 1


def test_stage_change_jail_escape_raises(isolated_home, tmp_path):
    import pytest

    from backend.cookbook import persistence

    sid = _mk_session()
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), "../outside.txt", "x")
    with pytest.raises(ValueError):
        persistence.stage_change(sid, "run1", str(tmp_path), ".", "x")


def test_list_staged_changes_filters(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    persistence.stage_change(sid, "runA", str(tmp_path), "one.txt", "1")
    row_b = persistence.stage_change(sid, "runB", str(tmp_path), "two.txt", "2")
    persistence.set_staged_status(row_b["id"], "rejected")

    assert [r["path"] for r in persistence.list_staged_changes(sid)] == ["one.txt", "two.txt"]
    assert [r["path"] for r in persistence.list_staged_changes(sid, status="pending")] == ["one.txt"]
    assert [r["path"] for r in persistence.list_staged_changes(sid, run_id="runB")] == ["two.txt"]
    assert persistence.list_staged_changes("nosuchsession") == []


def test_get_and_set_status(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    assert persistence.get_staged_change("nope") is None
    persistence.set_staged_status(row["id"], "rejected")
    assert persistence.get_staged_change(row["id"])["status"] == "rejected"


def test_delete_session_removes_staged_rows(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    persistence.delete_session(sid)
    assert persistence.get_staged_change(row["id"]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_staged_changes.py -v`
Expected: FAIL with `AttributeError: module 'backend.cookbook.persistence' has no attribute 'stage_change'`

- [ ] **Step 3: Implement**

In `backend/cookbook/persistence.py`:

Add to the imports at the top (`import hashlib` after `import json`):

```python
import hashlib
```

In `_ensure_db()`, after the `session_events` CREATE TABLE block (line ~52), add:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staged_changes (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            run_id      TEXT NOT NULL,
            root        TEXT NOT NULL,
            path        TEXT NOT NULL,
            base_hash   TEXT,
            old_content TEXT,
            new_content TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_staged_pending_unique ON staged_changes(session_id, path) WHERE status = 'pending'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_staged_session ON staged_changes(session_id, status)"
    )
```

In `delete_session()` (line ~185), add one line before the messages delete:

```python
    conn.execute("DELETE FROM staged_changes WHERE session_id = ?", (session_id,))
```

At the end of the file, add:

```python
_STAGED_COLUMNS = "id, session_id, run_id, root, path, base_hash, old_content, new_content, status, created_at, updated_at"


def _staged_row_to_dict(r: tuple) -> dict:
    return {
        "id": r[0],
        "session_id": r[1],
        "run_id": r[2],
        "root": r[3],
        "path": r[4],
        "base_hash": r[5],
        "old_content": r[6],
        "new_content": r[7],
        "status": r[8],
        "created_at": r[9],
        "updated_at": r[10],
    }


def _resolve_staged_target(root: str, path: str) -> Path:
    """Re-jail a staged path under its recorded root. Raises ValueError on escape."""
    base = Path(root).resolve()
    target = (base / path).resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        raise ValueError(f"path escapes project root: {path!r}")
    if str(rel) == ".":
        raise ValueError("path is the project root itself")
    return target


def stage_change(session_id: str, run_id: str, root: str, path: str, new_content: str) -> dict:
    """Upsert the session's pending row for this path (latest-wins, original snapshot kept)."""
    base = Path(root).resolve()
    target = _resolve_staged_target(root, path)
    rel = target.relative_to(base).as_posix()
    conn = _ensure_db()
    now = time.time()
    row = conn.execute(
        "SELECT id FROM staged_changes WHERE session_id = ? AND path = ? AND status = 'pending'",
        (session_id, rel),
    ).fetchone()
    if row:
        change_id = row[0]
        conn.execute(
            "UPDATE staged_changes SET new_content = ?, run_id = ?, updated_at = ? WHERE id = ?",
            (new_content, run_id, now, change_id),
        )
    else:
        change_id = uuid.uuid4().hex[:14]
        if target.exists() and target.is_file():
            data = target.read_bytes()
            base_hash = hashlib.sha256(data).hexdigest()
            old_content = data.decode("utf-8", errors="replace")
        else:
            base_hash = None
            old_content = None
        conn.execute(
            f"INSERT INTO staged_changes ({_STAGED_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (change_id, session_id, run_id, str(base), rel, base_hash, old_content, new_content, "pending", now, now),
        )
    conn.commit()
    conn.close()
    return get_staged_change(change_id)


def list_staged_changes(session_id: str, run_id: str | None = None, status: str | None = None) -> list[dict]:
    conn = _ensure_db()
    sql = f"SELECT {_STAGED_COLUMNS} FROM staged_changes WHERE session_id = ?"
    params: list = [session_id]
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    # rowid tie-break: time.time() can collide on Windows; insertion order must hold
    sql += " ORDER BY created_at ASC, rowid ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_staged_row_to_dict(r) for r in rows]


def get_staged_change(change_id: str) -> Optional[dict]:
    conn = _ensure_db()
    row = conn.execute(
        f"SELECT {_STAGED_COLUMNS} FROM staged_changes WHERE id = ?", (change_id,)
    ).fetchone()
    conn.close()
    return _staged_row_to_dict(row) if row else None


def set_staged_status(change_id: str, status: str) -> None:
    conn = _ensure_db()
    conn.execute(
        "UPDATE staged_changes SET status = ?, updated_at = ? WHERE id = ?",
        (status, time.time(), change_id),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_staged_changes.py -v`
Expected: 7 PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all green (no regressions — `test_persistence.py` exercises the migration for free via `isolated_home`).

- [ ] **Step 6: Commit**

```bash
git add tests/test_staged_changes.py backend/cookbook/persistence.py
git commit -m "feat(staging): staged_changes table + stage/list/get/set persistence"
```

---

### Task 2: Apply and revert — the only disk-write paths

**Files:**
- Modify: `backend/cookbook/persistence.py` (append after Task 1's functions; add `import os` at top)
- Test: `tests/test_staged_changes.py` (append)

**Interfaces:**
- Consumes: Task 1's `stage_change`, `get_staged_change`, `set_staged_status`, `_resolve_staged_target`.
- Produces: `apply_staged_change(change_id: str) -> dict` — returns `{"status": "applied", "path": ...}` on success; `{"status": "conflict", "disk_hash": ..., "base_hash": ...}` (row flipped to `conflict`) on hash mismatch; `{"status": "not_found"}` / `{"status": "not_pending", "current": ...}` / `{"status": "error", "error": ...}` otherwise. `revert_applied_change(change_id: str) -> dict` — `{"status": "reverted", "path": ...}` / `{"status": "conflict", ...}` / `{"status": "not_found"}` / `{"status": "not_applied", "current": ...}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_staged_changes.py`:

```python
def test_apply_happy_path_new_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "src/new.py", "print('hi')\n")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "applied"
    assert (tmp_path / "src" / "new.py").read_bytes() == b"print('hi')\n"
    assert persistence.get_staged_change(row["id"])["status"] == "applied"


def test_apply_happy_path_existing_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "applied"
    assert f.read_bytes() == b"changed"


def test_apply_conflict_when_disk_changed(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    f.write_bytes(b"hand-edited meanwhile")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "conflict"
    assert result["base_hash"] == row["base_hash"]
    assert result["disk_hash"] != row["base_hash"]
    assert f.read_bytes() == b"hand-edited meanwhile"  # no partial write
    assert persistence.get_staged_change(row["id"])["status"] == "conflict"


def test_apply_conflict_when_new_file_now_exists(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "new.txt", "staged")
    (tmp_path / "new.txt").write_bytes(b"someone else created this")
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "conflict"
    assert result["base_hash"] is None


def test_apply_rejects_non_pending_and_unknown(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    persistence.set_staged_status(row["id"], "rejected")
    assert persistence.apply_staged_change(row["id"])["status"] == "not_pending"
    assert persistence.apply_staged_change("nope")["status"] == "not_found"


def test_apply_rejail_blocks_tampered_path(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    conn = persistence._ensure_db()
    conn.execute("UPDATE staged_changes SET path = '../evil.txt' WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    result = persistence.apply_staged_change(row["id"])
    assert result["status"] == "error"
    assert not (tmp_path.parent / "evil.txt").exists()


def test_revert_restores_original(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    persistence.apply_staged_change(row["id"])
    result = persistence.revert_applied_change(row["id"])
    assert result["status"] == "reverted"
    assert f.read_bytes() == b"original"
    assert persistence.get_staged_change(row["id"])["status"] == "reverted"


def test_revert_deletes_applied_new_file(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "new.txt", "staged")
    persistence.apply_staged_change(row["id"])
    assert (tmp_path / "new.txt").exists()
    result = persistence.revert_applied_change(row["id"])
    assert result["status"] == "reverted"
    assert not (tmp_path / "new.txt").exists()


def test_revert_conflict_when_disk_hand_edited_after_apply(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    f = tmp_path / "a.txt"
    f.write_bytes(b"original")
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "changed")
    persistence.apply_staged_change(row["id"])
    f.write_bytes(b"hand-edited after apply")
    result = persistence.revert_applied_change(row["id"])
    assert result["status"] == "conflict"
    assert f.read_bytes() == b"hand-edited after apply"


def test_revert_rejects_non_applied(isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid = _mk_session()
    row = persistence.stage_change(sid, "run1", str(tmp_path), "a.txt", "x")
    assert persistence.revert_applied_change(row["id"])["status"] == "not_applied"
    assert persistence.revert_applied_change("nope")["status"] == "not_found"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_staged_changes.py -v`
Expected: new tests FAIL with `AttributeError: ... no attribute 'apply_staged_change'`; Task 1 tests still PASS.

- [ ] **Step 3: Implement**

In `backend/cookbook/persistence.py`, add `import os` to the top imports, then append:

```python
def _atomic_write(target: Path, data: bytes) -> None:
    """Write via a sibling tmp file + os.replace so a crash never leaves a partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.lac-tmp"
    tmp.write_bytes(data)
    os.replace(tmp, target)


def _disk_hash(target: Path) -> Optional[str]:
    if target.exists() and target.is_file():
        return hashlib.sha256(target.read_bytes()).hexdigest()
    return None


def apply_staged_change(change_id: str) -> dict:
    """The only disk-write path for staged changes: re-jail, conflict-check, atomic write."""
    row = get_staged_change(change_id)
    if row is None:
        return {"status": "not_found"}
    if row["status"] != "pending":
        return {"status": "not_pending", "current": row["status"]}
    try:
        target = _resolve_staged_target(row["root"], row["path"])
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    disk_hash = _disk_hash(target)
    if disk_hash != row["base_hash"]:
        set_staged_status(change_id, "conflict")
        return {"status": "conflict", "disk_hash": disk_hash, "base_hash": row["base_hash"]}
    _atomic_write(target, row["new_content"].encode("utf-8"))
    set_staged_status(change_id, "applied")
    return {"status": "applied", "path": row["path"]}


def revert_applied_change(change_id: str) -> dict:
    """Undo an applied change from the retained snapshot, guarded by a hash of what apply wrote."""
    row = get_staged_change(change_id)
    if row is None:
        return {"status": "not_found"}
    if row["status"] != "applied":
        return {"status": "not_applied", "current": row["status"]}
    try:
        target = _resolve_staged_target(row["root"], row["path"])
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    expected = hashlib.sha256(row["new_content"].encode("utf-8")).hexdigest()
    disk_hash = _disk_hash(target)
    if disk_hash != expected:
        return {"status": "conflict", "disk_hash": disk_hash, "expected_hash": expected}
    if row["base_hash"] is None:
        target.unlink(missing_ok=True)
    else:
        _atomic_write(target, (row["old_content"] or "").encode("utf-8"))
    set_staged_status(change_id, "reverted")
    return {"status": "reverted", "path": row["path"]}
```

Note the conflict check collapses both spec cases into one comparison: `base_hash NULL` ⇒ `_disk_hash` must also be `None` (file still absent); any mismatch is a conflict.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_staged_changes.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add tests/test_staged_changes.py backend/cookbook/persistence.py
git commit -m "feat(staging): apply + revert with re-jail, hash conflict check, atomic writes"
```

---

### Task 3: Staging tool-handler overlays (`backend/agent/staging.py`)

**Files:**
- Create: `backend/agent/staging.py`
- Test: `tests/test_staged_changes.py` (append)

**Interfaces:**
- Consumes: Task 1/2 persistence functions; `TOOL_HANDLERS` shape from `backend/plugin/builtins/tools.py` (`ToolHandler = Callable[[dict, dict], str]`, handlers read `ctx["cwd"]`).
- Produces: `build_staged_handlers(base_handlers: dict, *, session_id: str, run_id: str, event_queue=None) -> dict` — a shallow copy of `base_handlers` with `write_file`, `read_file`, `list_files` swapped for staging closures. `event_queue` is any object with `.put(dict)` (a `queue.Queue` in Plan 3); when given, each staged write pushes `{"type": "staged_change", "change_id": ..., "path": ...}`. Nothing else in the codebase calls this until Plan 3 — keep it unimported from `api.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_staged_changes.py`:

```python
def _build_handlers(sid, run_id="run1", event_queue=None):
    from backend.agent.staging import build_staged_handlers
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    return build_staged_handlers(
        TOOL_HANDLERS, session_id=sid, run_id=run_id, event_queue=event_queue
    )


def test_staged_write_stages_instead_of_writing(isolated_home, tmp_path):
    import queue

    from backend.cookbook import persistence

    sid = _mk_session()
    q = queue.Queue()
    handlers = _build_handlers(sid, event_queue=q)
    ctx = {"cwd": str(tmp_path)}

    result = handlers["write_file"]({"path": "src/x.py", "content": "pass\n"}, ctx)
    assert "staged" in result and "not yet applied" in result
    assert not (tmp_path / "src" / "x.py").exists()
    rows = persistence.list_staged_changes(sid, status="pending")
    assert [r["path"] for r in rows] == ["src/x.py"]
    ev = q.get_nowait()
    assert ev["type"] == "staged_change"
    assert ev["change_id"] == rows[0]["id"]
    assert ev["path"] == "src/x.py"


def test_staged_write_jail_and_size_cap(isolated_home, tmp_path):
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}

    assert handlers["write_file"]({"path": "../evil.txt", "content": "x"}, ctx).startswith("error:")
    big = "x" * (2 * 1024 * 1024 + 1)
    assert "2 MB" in handlers["write_file"]({"path": "big.txt", "content": big}, ctx)


def test_read_overlay_returns_staged_content(isolated_home, tmp_path):
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}
    (tmp_path / "a.txt").write_text("disk version", encoding="utf-8")

    handlers["write_file"]({"path": "a.txt", "content": "staged version"}, ctx)
    assert handlers["read_file"]({"path": "a.txt"}, ctx) == "staged version"
    # un-staged file falls through to disk
    (tmp_path / "b.txt").write_text("plain", encoding="utf-8")
    assert handlers["read_file"]({"path": "b.txt"}, ctx) == "plain"


def test_list_overlay_shows_staged_new_files(isolated_home, tmp_path):
    sid = _mk_session()
    handlers = _build_handlers(sid)
    ctx = {"cwd": str(tmp_path)}
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")

    handlers["write_file"]({"path": "ghost.txt", "content": "staged only"}, ctx)
    listing = handlers["list_files"]({"path": "."}, ctx)
    assert "real.txt" in listing
    assert "ghost.txt" in listing
    assert "(staged)" in listing
    # staged file in a directory that only exists via staging
    handlers["write_file"]({"path": "newdir/inner.txt", "content": "y"}, ctx)
    inner = handlers["list_files"]({"path": "newdir"}, ctx)
    assert "inner.txt" in inner


def test_other_handlers_pass_through_untouched(isolated_home):
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    sid = _mk_session()
    handlers = _build_handlers(sid)
    assert handlers["run_bash"] is TOOL_HANDLERS["run_bash"]
    assert handlers["web_search"] is TOOL_HANDLERS["web_search"]
    assert TOOL_HANDLERS["write_file"] is not handlers["write_file"]  # original untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_staged_changes.py -v`
Expected: new tests FAIL with `ModuleNotFoundError: No module named 'backend.agent.staging'`

- [ ] **Step 3: Implement**

Create `backend/agent/staging.py`:

```python
"""Staged tool-handler overlays for build mode.

Swaps write_file/read_file/list_files with session-scoped staging closures:
writes go to the staged_changes store (never disk), reads and listings see
pending staged state so the agent never chases stale disk. Built per run in
the web build branch (Plan 3); CLI/TUI keep the untouched builtins.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.cookbook import persistence

ToolHandler = Callable[[dict, dict], str]

MAX_STAGED_BYTES = 2 * 1024 * 1024


def _jail(args: dict, ctx: dict, default_path: str = "") -> tuple[Path, str] | str:
    """Replicate the builtin tools' path jail. Returns (base, rel_posix) or an error string."""
    path = Path(args.get("path", default_path))
    base = Path(ctx.get("cwd", ".")).resolve()
    target = (base / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        return f"error: path outside workspace: {target}"
    return base, rel.as_posix()


def build_staged_handlers(
    base_handlers: dict[str, ToolHandler],
    *,
    session_id: str,
    run_id: str,
    event_queue: Any | None = None,
) -> dict[str, ToolHandler]:
    handlers = dict(base_handlers)
    base_read = base_handlers["read_file"]
    base_list = base_handlers["list_files"]

    def _pending() -> list[dict]:
        return persistence.list_staged_changes(session_id, status="pending")

    def staged_write(args: dict, ctx: dict) -> str:
        jailed = _jail(args, ctx)
        if isinstance(jailed, str):
            return jailed
        base, rel = jailed
        content = args.get("content", "")
        n_bytes = len(content.encode("utf-8"))
        if n_bytes > MAX_STAGED_BYTES:
            return "error: content exceeds the 2 MB staging limit"
        try:
            row = persistence.stage_change(session_id, run_id, str(base), rel, content)
        except ValueError as e:
            return f"error: {e}"
        if event_queue is not None:
            event_queue.put(
                {"type": "staged_change", "change_id": row["id"], "path": row["path"]}
            )
        return f"staged {n_bytes} bytes to {rel} (change {row['id']}) - not yet applied"

    def read_overlay(args: dict, ctx: dict) -> str:
        jailed = _jail(args, ctx)
        if isinstance(jailed, str):
            return jailed
        _base, rel = jailed
        for row in _pending():
            if row["path"] == rel:
                return row["new_content"]
        return base_read(args, ctx)

    def list_overlay(args: dict, ctx: dict) -> str:
        jailed = _jail(args, ctx, default_path=".")
        if isinstance(jailed, str):
            return jailed
        base, rel = jailed
        dir_rel = "" if rel == "." else rel
        extra = []
        for row in _pending():
            p = Path(row["path"])
            parent = p.parent.as_posix()
            if parent == ".":
                parent = ""
            if parent != dir_rel:
                continue
            if (base / row["path"]).exists():
                continue  # exists on disk; base listing already shows it
            size = len(row["new_content"].encode("utf-8"))
            extra.append(f"f {size:>10} {p.name} (staged)")
        listing = base_list(args, ctx)
        if not extra:
            return listing
        if listing.startswith("error: not found") or listing == "(empty)":
            return "\n".join(extra)
        return listing + "\n" + "\n".join(extra)

    handlers["write_file"] = staged_write
    handlers["read_file"] = read_overlay
    handlers["list_files"] = list_overlay
    return handlers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_staged_changes.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add backend/agent/staging.py tests/test_staged_changes.py
git commit -m "feat(staging): staged write + read/list overlay tool handlers (dark until build mode)"
```

---

### Task 4: File-browse endpoints (`/api/agent/files`, `/api/agent/file`)

**Files:**
- Modify: `backend/api.py` (add `import hashlib` at top; new helpers + routes directly after `_resolve_agent_cwd`, ~line 987)
- Test: `tests/test_api_agent_files.py` (new)

**Interfaces:**
- Consumes: `_resolve_agent_cwd` (`backend/api.py:977-986`).
- Produces: `_resolve_in_root(root: Path, rel: str) -> Path` (raises `ValueError` on escape — Plan 4's UI and Task 5 reuse the routes, nothing else imports the helper). `GET /api/agent/files?cwd=<abs>&path=<rel>` → `{"path": rel, "entries": [{"name", "type": "dir"|"file", "size"}]}` (one level, dirs first, skips dotfiles + `node_modules` + `__pycache__`). `GET /api/agent/file?cwd=<abs>&path=<rel>` → `{"path", "content", "sha256", "size"}`, 413 over 1 MB.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_agent_files.py`:

```python
from __future__ import annotations

import hashlib


def test_agent_files_lists_one_level(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "deep.py").write_text("x", encoding="utf-8")
    (project / "a.txt").write_text("hello", encoding="utf-8")
    (project / ".git").mkdir()
    (project / ".env").write_text("SECRET=1", encoding="utf-8")
    (project / "node_modules").mkdir()

    client = flask_app.test_client()
    resp = client.get("/api/agent/files", query_string={"cwd": str(project)})
    assert resp.status_code == 200
    body = resp.get_json()
    names = [e["name"] for e in body["entries"]]
    assert names == ["src", "a.txt"]  # dirs first; dotfiles + node_modules skipped
    assert body["entries"][0]["type"] == "dir"
    assert body["entries"][1] == {"name": "a.txt", "type": "file", "size": 5}

    sub = client.get("/api/agent/files", query_string={"cwd": str(project), "path": "src"})
    assert [e["name"] for e in sub.get_json()["entries"]] == ["deep.py"]


def test_agent_files_rejects_escape_and_missing(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    client = flask_app.test_client()
    assert client.get("/api/agent/files", query_string={"cwd": str(project), "path": ".."}).status_code == 400
    assert client.get("/api/agent/files", query_string={"cwd": str(project), "path": "nope"}).status_code == 404
    assert client.get("/api/agent/files", query_string={"cwd": str(project / "missing")}).status_code == 400


def test_agent_file_returns_content_and_hash(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_bytes(b"hello")
    client = flask_app.test_client()
    resp = client.get("/api/agent/file", query_string={"cwd": str(project), "path": "a.txt"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["content"] == "hello"
    assert body["size"] == 5
    assert body["sha256"] == hashlib.sha256(b"hello").hexdigest()


def test_agent_file_413_over_1mb_and_404_and_400(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "big.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    client = flask_app.test_client()
    assert client.get("/api/agent/file", query_string={"cwd": str(project), "path": "big.bin"}).status_code == 413
    assert client.get("/api/agent/file", query_string={"cwd": str(project), "path": "nope.txt"}).status_code == 404
    assert client.get("/api/agent/file", query_string={"cwd": str(project), "path": "../etc"}).status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_files.py -v`
Expected: FAIL with 404s (routes don't exist).

- [ ] **Step 3: Implement**

In `backend/api.py`: add `import hashlib` to the stdlib imports at the top. After `_resolve_agent_cwd` (~line 987), add:

```python
_AGENT_SKIP_ENTRIES = {"node_modules", "__pycache__"}
AGENT_FILE_MAX_BYTES = 1024 * 1024


def _resolve_in_root(root: Path, rel: str) -> Path:
    """Resolve rel under root, refusing any path that escapes it."""
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes project root: {rel!r}")
    return target


@app.route("/api/agent/files")
def agent_files():
    try:
        root = _resolve_agent_cwd(request.args.get("cwd"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    rel = str(request.args.get("path") or "").strip()
    try:
        target = _resolve_in_root(root, rel)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not target.exists() or not target.is_dir():
        return jsonify({"error": f"Directory not found: {rel or '.'}"}), 404
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if p.name.startswith(".") or p.name in _AGENT_SKIP_ENTRIES:
            continue
        entries.append({
            "name": p.name,
            "type": "file" if p.is_file() else "dir",
            "size": p.stat().st_size if p.is_file() else 0,
        })
    return jsonify({"path": rel, "entries": entries})


@app.route("/api/agent/file")
def agent_file():
    try:
        root = _resolve_agent_cwd(request.args.get("cwd"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    rel = str(request.args.get("path") or "").strip()
    try:
        target = _resolve_in_root(root, rel)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not target.exists() or not target.is_file():
        return jsonify({"error": f"File not found: {rel}"}), 404
    size = target.stat().st_size
    if size > AGENT_FILE_MAX_BYTES:
        return jsonify({"error": f"File exceeds {AGENT_FILE_MAX_BYTES} bytes: {size}"}), 413
    data = target.read_bytes()
    return jsonify({
        "path": rel,
        "content": data.decode("utf-8", errors="replace"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    })
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_files.py -v` — expected PASS.
Run: `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add tests/test_api_agent_files.py backend/api.py
git commit -m "feat(workbench): jailed file-browse endpoints /api/agent/files + /api/agent/file"
```

---

### Task 5: Change-management endpoints

**Files:**
- Modify: `backend/api.py` (routes directly after Task 4's routes)
- Test: `tests/test_api_agent_files.py` (append)

**Interfaces:**
- Consumes: Task 1/2 persistence functions (imported inside the routes, matching `agent_chat`'s local-import style).
- Produces (all shapes consumed by Plan 4's UI):
  - `GET /api/agent/sessions/<session_id>/changes?run_id=&status=` → `{"changes": [row-without-bodies + "new_size"]}`; 404 unknown session.
  - `GET /api/agent/changes/<change_id>` → full row; 404.
  - `POST /api/agent/changes/<change_id>/apply` → 200 `{"status": "applied", ...}` | 409 (conflict/not-pending/error body passthrough) | 404.
  - `POST /api/agent/changes/<change_id>/reject` → 200 `{"status": "rejected"}` | 409 | 404.
  - `POST /api/agent/changes/<change_id>/revert` → 200 `{"status": "reverted", ...}` | 409 | 404.
  - `POST /api/agent/sessions/<session_id>/changes/apply` body `{"ids": [...]}` (optional; default all pending) → 200 `{"applied": [...], "conflicts": [...], "errors": [{"id", "error"}]}` — created_at order, continue past conflicts, never abort the batch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_agent_files.py`:

```python
def _stage(tmp_path, path="a.txt", content="staged", session_id=None):
    from backend.cookbook import persistence

    sid = session_id or persistence.create_session(name="t", model="m", workspace="default")
    row = persistence.stage_change(sid, "run1", str(tmp_path), path, content)
    return sid, row


def test_session_changes_listing_omits_bodies(flask_app, isolated_home, tmp_path):
    sid, row = _stage(tmp_path)
    client = flask_app.test_client()
    resp = client.get(f"/api/agent/sessions/{sid}/changes")
    assert resp.status_code == 200
    changes = resp.get_json()["changes"]
    assert len(changes) == 1
    assert changes[0]["id"] == row["id"]
    assert "new_content" not in changes[0] and "old_content" not in changes[0]
    assert changes[0]["new_size"] == len("staged")
    assert client.get("/api/agent/sessions/nosuch/changes").status_code == 404
    filtered = client.get(f"/api/agent/sessions/{sid}/changes", query_string={"status": "applied"})
    assert filtered.get_json()["changes"] == []


def test_change_detail_and_apply_reject_revert(flask_app, isolated_home, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"original")
    sid, row = _stage(tmp_path, content="changed")
    client = flask_app.test_client()

    detail = client.get(f"/api/agent/changes/{row['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["old_content"] == "original"
    assert detail.get_json()["new_content"] == "changed"
    assert client.get("/api/agent/changes/nosuch").status_code == 404

    applied = client.post(f"/api/agent/changes/{row['id']}/apply")
    assert applied.status_code == 200
    assert (tmp_path / "a.txt").read_bytes() == b"changed"
    # re-apply a non-pending row -> 409
    assert client.post(f"/api/agent/changes/{row['id']}/apply").status_code == 409

    reverted = client.post(f"/api/agent/changes/{row['id']}/revert")
    assert reverted.status_code == 200
    assert (tmp_path / "a.txt").read_bytes() == b"original"
    # revert again -> 409 (row now 'reverted')
    assert client.post(f"/api/agent/changes/{row['id']}/revert").status_code == 409

    sid2, row2 = _stage(tmp_path, path="b.txt", session_id=sid)
    rejected = client.post(f"/api/agent/changes/{row2['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.get_json()["status"] == "rejected"
    assert client.post(f"/api/agent/changes/{row2['id']}/reject").status_code == 409
    assert client.post("/api/agent/changes/nosuch/apply").status_code == 404
    assert client.post("/api/agent/changes/nosuch/reject").status_code == 404
    assert client.post("/api/agent/changes/nosuch/revert").status_code == 404


def test_apply_conflict_maps_to_409(flask_app, isolated_home, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"original")
    sid, row = _stage(tmp_path, content="changed")
    (tmp_path / "a.txt").write_bytes(b"hand-edited")
    client = flask_app.test_client()
    resp = client.post(f"/api/agent/changes/{row['id']}/apply")
    assert resp.status_code == 409
    assert resp.get_json()["status"] == "conflict"


def test_batch_apply_continues_past_conflicts(flask_app, isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid, row1 = _stage(tmp_path, path="one.txt", content="1")
    (tmp_path / "two.txt").write_bytes(b"original")
    row2 = persistence.stage_change(sid, "run1", str(tmp_path), "two.txt", "2")
    row3 = persistence.stage_change(sid, "run1", str(tmp_path), "three.txt", "3")
    (tmp_path / "two.txt").write_bytes(b"conflicted meanwhile")

    client = flask_app.test_client()
    resp = client.post(f"/api/agent/sessions/{sid}/changes/apply", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["applied"] == [row1["id"], row3["id"]]  # created_at order, conflict skipped not aborted
    assert body["conflicts"] == [row2["id"]]
    assert body["errors"] == []
    assert (tmp_path / "three.txt").read_bytes() == b"3"

    # ids subset + unknown id lands in errors
    sid2, row4 = _stage(tmp_path, path="four.txt", content="4")
    resp2 = client.post(
        f"/api/agent/sessions/{sid2}/changes/apply", json={"ids": [row4["id"], "nosuch"]}
    )
    body2 = resp2.get_json()
    assert body2["applied"] == [row4["id"]]
    assert body2["errors"] == [{"id": "nosuch", "error": "not pending"}]
    assert client.post("/api/agent/sessions/nosuch/changes/apply", json={}).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_files.py -v`
Expected: new tests FAIL with 404s (routes don't exist).

- [ ] **Step 3: Implement**

In `backend/api.py`, directly after Task 4's `agent_file` route, add:

```python
def _staged_summary(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in ("old_content", "new_content")}
    out["new_size"] = len((row.get("new_content") or "").encode("utf-8"))
    return out


@app.route("/api/agent/sessions/<session_id>/changes")
def agent_session_changes(session_id):
    from .cookbook.persistence import get_session, list_staged_changes

    if get_session(session_id) is None:
        return jsonify({"error": "Session not found"}), 404
    rows = list_staged_changes(
        session_id,
        run_id=request.args.get("run_id") or None,
        status=request.args.get("status") or None,
    )
    return jsonify({"changes": [_staged_summary(r) for r in rows]})


@app.route("/api/agent/changes/<change_id>")
def agent_change_detail(change_id):
    from .cookbook.persistence import get_staged_change

    row = get_staged_change(change_id)
    if row is None:
        return jsonify({"error": "Change not found"}), 404
    return jsonify(row)


@app.route("/api/agent/changes/<change_id>/apply", methods=["POST"])
def agent_change_apply(change_id):
    from .cookbook.persistence import apply_staged_change

    result = apply_staged_change(change_id)
    if result["status"] == "not_found":
        return jsonify({"error": "Change not found"}), 404
    if result["status"] == "applied":
        return jsonify(result)
    return jsonify(result), 409


@app.route("/api/agent/changes/<change_id>/reject", methods=["POST"])
def agent_change_reject(change_id):
    from .cookbook.persistence import get_staged_change, set_staged_status

    row = get_staged_change(change_id)
    if row is None:
        return jsonify({"error": "Change not found"}), 404
    if row["status"] != "pending":
        return jsonify({"status": "not_pending", "current": row["status"]}), 409
    set_staged_status(change_id, "rejected")
    return jsonify({"status": "rejected", "path": row["path"]})


@app.route("/api/agent/changes/<change_id>/revert", methods=["POST"])
def agent_change_revert(change_id):
    from .cookbook.persistence import revert_applied_change

    result = revert_applied_change(change_id)
    if result["status"] == "not_found":
        return jsonify({"error": "Change not found"}), 404
    if result["status"] == "reverted":
        return jsonify(result)
    return jsonify(result), 409


@app.route("/api/agent/sessions/<session_id>/changes/apply", methods=["POST"])
def agent_session_changes_apply(session_id):
    from .cookbook.persistence import apply_staged_change, get_session, list_staged_changes

    if get_session(session_id) is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    pending = list_staged_changes(session_id, status="pending")  # created_at ASC
    if isinstance(ids, list):
        wanted = [str(i) for i in ids]
        pending = [r for r in pending if r["id"] in wanted]
        known = {r["id"] for r in pending}
        unknown = [i for i in wanted if i not in known]
    else:
        unknown = []
    applied, conflicts, errors = [], [], []
    for row in pending:
        result = apply_staged_change(row["id"])
        if result["status"] == "applied":
            applied.append(row["id"])
        elif result["status"] == "conflict":
            conflicts.append(row["id"])
        else:
            errors.append({"id": row["id"], "error": result.get("error") or result["status"]})
    for i in unknown:
        errors.append({"id": i, "error": "not pending"})
    return jsonify({"applied": applied, "conflicts": conflicts, "errors": errors})
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_files.py tests/test_staged_changes.py -v` — expected PASS.
Run: `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add tests/test_api_agent_files.py backend/api.py
git commit -m "feat(workbench): staged-change review endpoints (list/detail/apply/reject/revert/batch)"
```

---

## Self-review checklist (run after all tasks)

- Spec §3.1 coverage: table ✓ (Task 1), 6 functions ✓ (Tasks 1-2), upsert/latest-wins with original snapshot ✓, re-jail at stage AND apply ✓, hash-only conflict check ✓, atomic tmp+replace ✓, undo with hash guard ✓, 2 MB cap ✓ (Task 3), overlays ✓ (Task 3).
- Spec §3.4 coverage: files/file/changes endpoints ✓ (Tasks 4-5), batch semantics (created_at order, continue-past-conflicts) ✓.
- Inertness: nothing imports `backend/agent/staging.py` outside tests; `agent_chat` untouched; the runner untouched.
- Statuses consistent across tasks: `pending|applied|rejected|conflict|reverted`.
