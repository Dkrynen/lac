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
