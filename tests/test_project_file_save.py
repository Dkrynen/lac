# tests/test_project_file_save.py
from __future__ import annotations

import hashlib
import json


def _register(client, root, name="proj"):
    resp = client.post(
        "/api/workspaces/default/projects",
        json={"name": name, "root": str(root)},
    )
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()["id"]


def _save(client, project_id, path, content, base):
    return client.post(
        f"/api/projects/{project_id}/file/save",
        json={"path": path, "content": content, "base_sha256": base},
    )


def test_save_update_happy_path(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"original")
    pid = _register(client, root)
    base = hashlib.sha256(b"original").hexdigest()
    resp = _save(client, pid, "a.txt", "changed", base)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "applied"
    assert body["path"] == "a.txt"
    assert body["sha256"] == hashlib.sha256(b"changed").hexdigest()
    assert body["size"] == len(b"changed")
    assert f.read_bytes() == b"changed"


def test_save_create_happy_path(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    pid = _register(client, root)
    resp = _save(client, pid, "src/new.py", "print('hi')\n", None)
    assert resp.status_code == 200, resp.get_json()
    assert (root / "src" / "new.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_save_drift_conflict_409(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"v2-on-disk")
    pid = _register(client, root)
    stale = hashlib.sha256(b"v1-the-editor-loaded").hexdigest()
    resp = _save(client, pid, "a.txt", "mine", stale)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "save_conflict"
    assert body["disk_sha256"] == hashlib.sha256(b"v2-on-disk").hexdigest()
    assert f.read_bytes() == b"v2-on-disk"  # disk untouched


def test_save_create_collision_409(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"surprise")
    pid = _register(client, root)
    resp = _save(client, pid, "a.txt", "mine", None)
    assert resp.status_code == 409
    assert resp.get_json()["disk_sha256"] == hashlib.sha256(b"surprise").hexdigest()


def test_save_conflict_leaves_no_pending_row(flask_app, isolated_home, tmp_path):
    from backend.cookbook.persistence import get_or_create_manual_session, list_staged_changes

    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_bytes(b"v2")
    pid = _register(client, root)
    _save(client, pid, "a.txt", "mine", hashlib.sha256(b"v1").hexdigest())
    sid = get_or_create_manual_session(pid)
    assert list_staged_changes(sid, status="pending") == []


def test_save_too_large_413(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    pid = _register(client, root)
    resp = _save(client, pid, "big.txt", "x" * (2 * 1024 * 1024 + 1), None)
    assert resp.status_code == 413


def test_save_over_binary_target_415(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "blob.bin").write_bytes(b"\x00\xff\x00\xff")
    pid = _register(client, root)
    base = hashlib.sha256(b"\x00\xff\x00\xff").hexdigest()
    resp = _save(client, pid, "blob.bin", "text", base)
    assert resp.status_code == 415


def test_save_jail_escape_400(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    pid = _register(client, root)
    resp = _save(client, pid, "../outside.txt", "x", None)
    assert resp.status_code in (400, 403, 404)
    assert not (root.parent / "outside.txt").exists()


def test_save_invalid_body_400(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    pid = _register(client, root)
    assert client.post(f"/api/projects/{pid}/file/save", json=[1]).status_code == 400
    assert _save(client, pid, "a.txt", 42, None).status_code == 400
    assert _save(client, pid, 42, "x", None).status_code == 400
    assert _save(client, pid, "a.txt", "x", "not-hex").status_code == 400


def test_save_routes_through_manual_session_and_reuses_it(flask_app, isolated_home, tmp_path):
    from backend.cookbook.persistence import get_or_create_manual_session, list_staged_changes

    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    pid = _register(client, root)
    _save(client, pid, "one.txt", "1", None)
    _save(client, pid, "two.txt", "2", None)
    sid = get_or_create_manual_session(pid)
    rows = list_staged_changes(sid, status="applied")
    assert sorted(r["path"] for r in rows) == ["one.txt", "two.txt"]


def test_sessions_listing_hides_manual_session(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    pid = _register(client, root)
    _save(client, pid, "one.txt", "1", None)
    resp = client.get(f"/api/sessions?project_id={pid}")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_save_does_not_touch_pending_agent_row(flask_app, isolated_home, tmp_path):
    from backend.cookbook import persistence

    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"original")
    pid = _register(client, root)
    chat_sid = persistence.create_session(
        name="chat", workspace="default", project_id=pid
    )
    agent_row = persistence.stage_change(
        chat_sid, "run1", str(root), "a.txt", "agent-version"
    )
    base = hashlib.sha256(b"original").hexdigest()
    resp = _save(client, pid, "a.txt", "human-version", base)
    assert resp.status_code == 200
    # Agent row untouched and still pending with its original snapshot.
    fresh = persistence.get_staged_change(agent_row["id"])
    assert fresh["status"] == "pending"
    assert fresh["base_hash"] == base
    # Its apply now conflicts, correctly.
    result = persistence.apply_staged_change(agent_row["id"])
    assert result["status"] == "conflict"


def test_save_then_revert_restores_disk(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"original")
    pid = _register(client, root)
    base = hashlib.sha256(b"original").hexdigest()
    change_id = _save(client, pid, "a.txt", "changed", base).get_json()["change_id"]
    resp = client.post(f"/api/agent/changes/{change_id}/revert")
    assert resp.status_code == 200
    assert f.read_bytes() == b"original"


def test_save_rejected_off_machine(flask_app, isolated_home, tmp_path):
    client = flask_app.test_client()
    root = tmp_path / "proj"
    root.mkdir()
    pid = _register(client, root)
    resp = client.post(
        f"/api/projects/{pid}/file/save",
        json={"path": "a.txt", "content": "x", "base_sha256": None},
        headers={"Host": "evil.example.com"},
    )
    assert resp.status_code == 403
