from __future__ import annotations

import concurrent.futures
import os
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from backend.cookbook import persistence


def _make_project_root(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def test_create_list_get_and_revalidate_project_without_touching_directory(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path)
    marker = root / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")

    project = persistence.create_project(
        "default", "  Client Portal  ", str(root), "  Delivery workspace  "
    )

    assert project["workspace"] == "default"
    assert project["name"] == "Client Portal"
    assert project["description"] == "Delivery workspace"
    assert project["root"] == str(root.resolve())
    assert project["root_key"]
    assert project["root_dev"]
    assert project["root_ino"]
    assert project["status"] == "active"
    assert persistence.get_project(project["id"]) == project
    assert persistence.list_projects("default") == [project]
    assert persistence.revalidate_project_root(project) == root.resolve()
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in root.iterdir()) == ["keep.txt"]


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("", ""),
        ("   ", ""),
        ("bad\x00name", ""),
        ("x" * 121, ""),
        ("valid", "bad\ncontrol"),
        ("valid", "\ntrimmed-control"),
        ("valid", "x" * 1001),
    ],
)
def test_project_registration_rejects_malformed_metadata(
    isolated_home, tmp_path, name, description
):
    root = _make_project_root(tmp_path)
    with pytest.raises(ValueError):
        persistence.create_project("default", name, str(root), description)
    assert persistence.list_projects("default") == []


def test_project_registration_rejects_unknown_workspace(isolated_home, tmp_path):
    root = _make_project_root(tmp_path)
    with pytest.raises(ValueError, match="workspace"):
        persistence.create_project("missing", "Project", str(root))
    assert persistence.list_projects("default") == []


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "volume_root"])
def test_project_registration_rejects_invalid_root(
    isolated_home, tmp_path, kind
):
    if kind == "relative":
        value = "relative/project"
    elif kind == "missing":
        value = str(tmp_path / "missing")
    elif kind == "file":
        file_path = tmp_path / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        value = str(file_path)
    else:
        value = str(Path(tmp_path.anchor))

    with pytest.raises(ValueError):
        persistence.create_project("default", "Project", value)
    assert persistence.list_projects("default") == []


def test_project_registration_rejects_home_data_root_and_their_ancestors(
    isolated_home, tmp_path
):
    data_root = Path(isolated_home) / ".model-hub"
    child_of_data_root = data_root / "private-child"
    child_of_data_root.mkdir()

    for candidate in (Path(isolated_home), data_root, child_of_data_root, tmp_path):
        with pytest.raises(ValueError):
            persistence.create_project(
                "default", f"Rejected {candidate.name}", str(candidate)
            )
    assert persistence.list_projects("default") == []


def test_project_registration_rejects_symlink_or_reparse_root(
    isolated_home, tmp_path
):
    target = _make_project_root(tmp_path, "target")
    link = tmp_path / "linked-project"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode:
            pytest.skip(f"directory junction unavailable: {created.stderr}")
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks unavailable: {exc}")

    try:
        with pytest.raises(ValueError, match="symlink|reparse|indirection"):
            persistence.create_project("default", "Linked", str(link))
        assert persistence.list_projects("default") == []
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_duplicate_project_root_is_a_conflict_even_across_workspaces(
    isolated_home, tmp_path
):
    from backend.cookbook.config import create_workspace, ensure_workspace

    ensure_workspace()
    create_workspace("Other")
    root = _make_project_root(tmp_path)
    persistence.create_project("default", "First", str(root))

    with pytest.raises(persistence.ProjectConflictError):
        persistence.create_project("default", "Duplicate", str(root))
    with pytest.raises(persistence.ProjectConflictError):
        persistence.create_project("other", "Cross workspace", str(root))


def test_duplicate_physical_identity_is_enforced_independently_of_root_key(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path)
    project = persistence.create_project("default", "First", str(root))
    conn = persistence._ensure_db()
    conn.execute(
        "UPDATE projects SET root_key = ? WHERE id = ?",
        ("tampered-key", project["id"]),
    )
    conn.commit()
    conn.close()

    with pytest.raises(persistence.ProjectConflictError):
        persistence.create_project("default", "Same identity", str(root))


def test_concurrent_duplicate_registration_creates_exactly_one_record(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path)

    def register(index: int):
        try:
            return persistence.create_project(
                "default", f"Project {index}", str(root)
            )["id"]
        except persistence.ProjectConflictError:
            return "conflict"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, range(2)))

    assert results.count("conflict") == 1
    assert len(persistence.list_projects("default")) == 1


def test_project_registration_and_workspace_delete_are_serialized(
    isolated_home, monkeypatch, tmp_path
):
    from backend.cookbook.config import create_workspace, delete_workspace, get_workspace

    workspace = create_workspace("Race Client")
    root = _make_project_root(tmp_path)
    inspected = threading.Event()
    release_inspection = threading.Event()
    real_inspect = persistence.inspect_project_root

    def paused_inspect(*args, **kwargs):
        identity = real_inspect(*args, **kwargs)
        inspected.set()
        assert release_inspection.wait(timeout=5)
        return identity

    monkeypatch.setattr(persistence, "inspect_project_root", paused_inspect)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        registration = pool.submit(
            persistence.create_project,
            workspace.id,
            "Project",
            str(root),
        )
        assert inspected.wait(timeout=5)
        deletion = pool.submit(delete_workspace, workspace.id)
        assert not deletion.done()
        release_inspection.set()
        project = registration.result(timeout=5)
        deleted = deletion.result(timeout=5)

    assert deleted is False
    assert get_workspace(workspace.id) is not None
    assert persistence.get_project(project["id"])["workspace"] == workspace.id


def test_project_root_inspection_fails_closed_when_a_component_is_uninspectable(
    monkeypatch, tmp_path
):
    from backend.project_paths import inspect_project_root

    root = _make_project_root(tmp_path)
    blocked = root.parent.absolute()
    real_lstat = Path.lstat

    def guarded_lstat(self):
        if self.absolute() == blocked:
            raise PermissionError("blocked for test")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", guarded_lstat)
    with pytest.raises(ValueError, match="inspect|indirection"):
        inspect_project_root(str(root), home=tmp_path / "home", data_root=tmp_path / "data")


def test_project_root_rejects_ancestor_mount_indirection(monkeypatch, tmp_path):
    import backend.project_paths as project_paths

    root = _make_project_root(tmp_path)
    real_ismount = project_paths.os.path.ismount
    monkeypatch.setattr(
        project_paths.os.path,
        "ismount",
        lambda value: Path(value) == root.parent or real_ismount(value),
    )

    with pytest.raises(ValueError, match="mount|indirection"):
        project_paths.inspect_project_root(
            str(root), home=tmp_path / "home", data_root=tmp_path / "data"
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows network-path boundary")
def test_project_root_rejects_unc_and_mapped_network_paths(monkeypatch, tmp_path):
    import backend.project_paths as project_paths

    with pytest.raises(ValueError, match="local|network"):
        project_paths.inspect_project_root(r"\\server\share\project")

    root = _make_project_root(tmp_path)
    monkeypatch.setattr(project_paths, "_windows_drive_type", lambda _path: 4)
    with pytest.raises(ValueError, match="local|network"):
        project_paths.inspect_project_root(
            str(root), home=tmp_path / "home", data_root=tmp_path / "data"
        )


def test_revalidate_project_root_fails_closed_on_missing_or_replaced_identity(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path)
    project = persistence.create_project("default", "Project", str(root))

    displaced_root = tmp_path / "displaced-project"
    root.rename(displaced_root)
    with pytest.raises(ValueError, match="missing|invalid|drift"):
        persistence.revalidate_project_root(project["id"])

    root.mkdir()
    with pytest.raises(ValueError, match="identity|drift"):
        persistence.revalidate_project_root(project["id"])


def test_project_bound_staged_rows_enforce_registered_root(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path, "project")
    other = _make_project_root(tmp_path, "other")
    project = persistence.create_project("default", "Project", str(root))
    sid = persistence.create_session(model="m", project_id=project["id"])

    allowed = persistence.stage_change(
        sid, "run", str(root), "allowed.txt", "allowed"
    )
    with pytest.raises(ValueError, match="project root"):
        persistence.stage_change(sid, "run", str(other), "blocked.txt", "blocked")
    with pytest.raises(ValueError, match="project root"):
        persistence.list_staged_changes_for_root_bounded(
            sid,
            str(other.resolve()),
            max_rows=10,
            max_content_bytes=1000,
        )

    conn = persistence._ensure_db()
    now = 123.0
    conn.execute(
        """
        INSERT INTO staged_changes
            (id, session_id, run_id, root, path, base_hash, old_content,
             new_content, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 'pending', ?, ?)
        """,
        ("corrupt", sid, "run", str(other.resolve()), "escape.txt", "unsafe", now, now),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="project root"):
        persistence.list_staged_changes(sid)
    with pytest.raises(ValueError, match="project root"):
        persistence.get_staged_change("corrupt")
    with pytest.raises(ValueError, match="project root"):
        persistence.apply_staged_change("corrupt")
    with pytest.raises(ValueError, match="project root"):
        persistence.revert_applied_change("corrupt")
    with pytest.raises(ValueError, match="project root"):
        persistence.set_staged_status("corrupt", "rejected")
    conn = persistence._ensure_db()
    assert conn.execute(
        "SELECT status FROM staged_changes WHERE id = 'corrupt'"
    ).fetchone()[0] == "pending"
    conn.close()
    assert not (other / "escape.txt").exists()


def test_project_bound_staging_hard_denies_sensitive_paths_even_if_database_is_tampered(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path, "project")
    project = persistence.create_project("default", "Project", str(root))
    sid = persistence.create_session(model="m", project_id=project["id"])

    with pytest.raises(ValueError, match="sensitive|unsafe"):
        persistence.stage_change(sid, "run", str(root), ".env", "SECRET=nope")
    assert persistence.list_staged_changes(sid) == []

    conn = persistence._ensure_db()
    conn.execute(
        """
        INSERT INTO staged_changes
            (id, session_id, run_id, root, path, base_hash, old_content,
             new_content, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 'pending', 1, 1)
        """,
        ("sensitive", sid, "run", str(root.resolve()), ".env", "SECRET=nope"),
    )
    conn.commit()
    conn.close()

    operations = (
        lambda: persistence.list_staged_changes(sid),
        lambda: persistence.get_staged_change("sensitive"),
        lambda: persistence.set_staged_status("sensitive", "rejected"),
        lambda: persistence.apply_staged_change("sensitive"),
        lambda: persistence.revert_applied_change("sensitive"),
        lambda: persistence.list_staged_changes_for_root_bounded(
            sid,
            str(root.resolve()),
            max_rows=10,
            max_content_bytes=1000,
        ),
    )
    for operation in operations:
        with pytest.raises(ValueError, match="sensitive|unsafe"):
            operation()

    assert not (root / ".env").exists()
    conn = persistence._ensure_db()
    assert conn.execute(
        "SELECT status FROM staged_changes WHERE id = 'sensitive'"
    ).fetchone()[0] == "pending"
    conn.close()


def test_project_bound_stage_rechecks_existing_file_before_snapshot_read(
    isolated_home, tmp_path, monkeypatch
):
    root = _make_project_root(tmp_path, "project")
    target = root / "safe.txt"
    target.write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    project = persistence.create_project("default", "Project", str(root))
    sid = persistence.create_session(model="m", project_id=project["id"])
    original_resolve = persistence.resolve_project_path
    swapped = False

    def swap_after_resolve(*args, **kwargs):
        nonlocal swapped
        resolved, relative = original_resolve(*args, **kwargs)
        if relative == "safe.txt" and not swapped:
            swapped = True
            resolved.unlink()
            os.link(outside, resolved)
        return resolved, relative

    monkeypatch.setattr(persistence, "resolve_project_path", swap_after_resolve)

    with pytest.raises(ValueError, match="hard link|unsafe"):
        persistence.stage_change(
            sid, "run", str(root), "safe.txt", "replacement"
        )

    assert persistence.list_staged_changes(sid) == []


def test_bounded_project_overlay_counts_before_per_row_path_validation(
    isolated_home, tmp_path, monkeypatch
):
    root = _make_project_root(tmp_path, "project")
    project = persistence.create_project("default", "Project", str(root))
    sid = persistence.create_session(model="m", project_id=project["id"])
    for index in range(4):
        persistence.stage_change(
            sid,
            "run",
            str(root),
            f"file-{index}.txt",
            "content",
        )

    calls = 0
    original_resolve = persistence.resolve_project_path

    def tracked_resolve(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_resolve(*args, **kwargs)

    monkeypatch.setattr(persistence, "resolve_project_path", tracked_resolve)

    rows, count, content_bytes = persistence.list_staged_changes_for_root_bounded(
        sid,
        str(root.resolve()),
        max_rows=2,
        max_content_bytes=1000,
    )

    assert rows == []
    assert count == 4
    assert content_bytes == len(b"content") * 4
    assert calls == 0


def test_project_bound_apply_rechecks_linked_parent_before_disk_write(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path, "project")
    outside = _make_project_root(tmp_path, "outside")
    safe_parent = root / "safe"
    safe_parent.mkdir()
    project = persistence.create_project("default", "Project", str(root))
    sid = persistence.create_session(model="m", project_id=project["id"])
    row = persistence.stage_change(
        sid, "run", str(root), "safe/result.txt", "must stay inside"
    )
    safe_parent.rmdir()
    if os.name == "nt":
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(safe_parent), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode:
            pytest.skip(f"directory junction unavailable: {created.stderr}")
    else:
        try:
            safe_parent.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks unavailable: {exc}")

    try:
        with pytest.raises(ValueError, match="linked|unsafe"):
            persistence.apply_staged_change(row["id"])

        assert not (outside / "result.txt").exists()
    finally:
        if safe_parent.exists() or safe_parent.is_symlink():
            if os.name == "nt":
                safe_parent.rmdir()
            else:
                safe_parent.unlink()


def test_bound_staged_listing_revalidates_root_even_when_no_rows_exist(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path, "project")
    project = persistence.create_project("default", "Project", str(root))
    sid = persistence.create_session(model="m", project_id=project["id"])
    root.rename(tmp_path / "moved-project")
    root.mkdir()

    with pytest.raises(ValueError, match="changed|drift"):
        persistence.list_staged_changes(sid)


def test_bound_staged_operations_reject_corrupted_session_workspace(
    isolated_home, tmp_path
):
    root = _make_project_root(tmp_path, "project")
    project = persistence.create_project("default", "Project", str(root))
    sid = persistence.create_session(model="m", project_id=project["id"])
    row = persistence.stage_change(sid, "run", str(root), "blocked.txt", "blocked")
    conn = persistence._ensure_db()
    conn.execute("UPDATE sessions SET workspace = 'wrong' WHERE id = ?", (sid,))
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="workspace"):
        persistence.list_staged_changes(sid)
    with pytest.raises(ValueError, match="workspace"):
        persistence.get_staged_change(row["id"])
    with pytest.raises(ValueError, match="workspace"):
        persistence.apply_staged_change(row["id"])
    assert not (root / "blocked.txt").exists()


def test_legacy_unassigned_session_keeps_multi_root_staging(
    isolated_home, tmp_path
):
    first = _make_project_root(tmp_path, "first")
    second = _make_project_root(tmp_path, "second")
    sid = persistence.create_session(model="m", workspace="default")

    persistence.stage_change(sid, "a", str(first), "same.txt", "a")
    persistence.stage_change(sid, "b", str(second), "same.txt", "b")

    assert {row["root"] for row in persistence.list_staged_changes(sid)} == {
        str(first.resolve()),
        str(second.resolve()),
    }


def test_sessions_project_foreign_key_rejects_unknown_project(
    isolated_home,
):
    conn = persistence._ensure_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO sessions
                (id, name, model, system_prompt, context, workspace, project_id,
                 created_at, updated_at)
            VALUES ('bad-fk', '', '', '', '{}', 'default', 'missing', 1, 1)
            """
        )
    conn.close()
