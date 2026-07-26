from __future__ import annotations

import os
from pathlib import Path

import pytest

import backend.agent_eval.fixture as fixture_module
from backend.agent_eval.fixture import (
    FixtureEntry,
    FixtureManifest,
    FixtureSealError,
    FixtureSealResult,
    build_fixture_manifest,
    materialize_fixture,
    mark_fixture_read_only,
    verify_materialized_fixture,
)
from backend.agent_eval.task import EvalScorer, EvalTask
from backend.agent_eval.task import task_contract_sha256


@pytest.fixture
def task(tmp_path: Path) -> EvalTask:
    fixture = tmp_path / "fixture"
    (fixture / "nested").mkdir(parents=True)
    (fixture / "stats_service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (fixture / "nested" / "notes.txt").write_text("notes\n", encoding="utf-8")
    return EvalTask(
        schema_version=1,
        id="fixture-task",
        prompt="Inspect the fixture.",
        fixture_root=fixture,
        timeout_seconds=30,
        scorer=EvalScorer(type="exact_text", expected="ValueError"),
    )


def test_materialized_fixture_matches_file_and_aggregate_hashes(task, monkeypatch):
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(True, True),
    )
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"

    materialize_fixture(manifest, task.fixture_root, destination)
    verification = verify_materialized_fixture(manifest, destination)

    assert [entry.path for entry in manifest.entries] == [
        "nested/notes.txt",
        "stats_service.py",
    ]
    assert verification.ok is True
    assert verification.aggregate_sha256 == manifest.aggregate_sha256
    assert manifest.task_contract_sha256 == task_contract_sha256(task)


@pytest.mark.parametrize("change", ["mutation", "addition", "deletion"])
def test_verification_detects_each_destination_drift(task, change, monkeypatch):
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(True, True),
    )
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    materialize_fixture(manifest, task.fixture_root, destination)

    target = destination / "stats_service.py"
    def make_writable(path: Path) -> None:
        path.parent.chmod(0o777)
        path.chmod(0o666)
        if os.name == "nt":
            import ctypes
            assert ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW(
                str(path), 0x80
            )

    if change == "mutation":
        make_writable(target)
        target.write_text("mutated\n", encoding="utf-8")
    elif change == "addition":
        destination.chmod(0o777)
        (destination / "unexpected.txt").write_text("added\n", encoding="utf-8")
    else:
        make_writable(target)
        target.unlink()

    assert verify_materialized_fixture(manifest, destination).ok is False


def test_verification_rejects_unexpected_empty_directory_and_records_it(
    task, monkeypatch
):
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(True, True),
    )
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    materialize_fixture(manifest, task.fixture_root, destination)

    (destination / "unexpected-empty").mkdir()

    verification = verify_materialized_fixture(manifest, destination)

    assert verification.ok is False
    assert verification.directories == ("nested", "unexpected-empty")
    assert verification.entries == manifest.entries
    assert verification.aggregate_sha256 == manifest.aggregate_sha256


def test_manifest_rejects_unrepresented_source_empty_directory(task):
    (task.fixture_root / "empty").mkdir()

    with pytest.raises(FixtureSealError, match="empty director"):
        build_fixture_manifest(task)


def test_materialization_refuses_existing_destination(task):
    destination = task.fixture_root.parent / "materialized"
    destination.mkdir()

    with pytest.raises(FileExistsError):
        materialize_fixture(build_fixture_manifest(task), task.fixture_root, destination)


def test_materialization_refuses_source_drift_after_manifest(task):
    manifest = build_fixture_manifest(task)
    (task.fixture_root / "stats_service.py").write_text("changed\n", encoding="utf-8")

    with pytest.raises(FixtureSealError, match="source.*drift"):
        materialize_fixture(
            manifest, task.fixture_root, task.fixture_root.parent / "materialized"
        )


@pytest.mark.parametrize("unsafe_path", ["name.py:stream", "../escape.py"])
def test_materialization_rejects_unsafe_manifest_path(task, unsafe_path):
    manifest = build_fixture_manifest(task)
    unsafe = FixtureManifest(
        (FixtureEntry(unsafe_path, 1, "a" * 64),),
        manifest.aggregate_sha256,
        manifest.task_contract_sha256,
    )

    with pytest.raises(FixtureSealError, match="unsafe"):
        materialize_fixture(unsafe, task.fixture_root, task.fixture_root.parent / "materialized")


def test_manifest_rejects_case_colliding_paths(task):
    manifest = build_fixture_manifest(task)
    colliding = FixtureManifest(
        (
            manifest.entries[1],
            FixtureEntry("STATS_SERVICE.PY", 8, "a" * 64),
        ),
        manifest.aggregate_sha256,
        manifest.task_contract_sha256,
    )

    with pytest.raises(FixtureSealError, match="case"):
        materialize_fixture(colliding, task.fixture_root, task.fixture_root.parent / "materialized")


def test_manifest_rejects_hardlinked_source_file(task):
    source = task.fixture_root / "stats_service.py"
    try:
        os.link(source, task.fixture_root / "duplicate.py")
    except OSError:
        pytest.skip("hardlink creation unavailable")

    with pytest.raises(FixtureSealError, match="hardlink"):
        build_fixture_manifest(task)


def test_manifest_rejects_linked_source_file(task, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    link = task.fixture_root / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(FixtureSealError, match="link|reparse"):
        build_fixture_manifest(task)


def test_manifest_rejects_linked_source_directory(task, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.py").write_text("outside\n", encoding="utf-8")
    link = task.fixture_root / "linked-directory"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")

    with pytest.raises(FixtureSealError, match="link|reparse"):
        build_fixture_manifest(task)


def test_verification_rejects_linked_destination(task, tmp_path, monkeypatch):
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(True, True),
    )
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    materialize_fixture(manifest, task.fixture_root, destination)
    target = destination / "stats_service.py"
    target.parent.chmod(0o777)
    target.chmod(0o666)
    if os.name == "nt":
        import ctypes
        assert ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW(
            str(target), 0x80
        )
    target.unlink()
    try:
        target.symlink_to(tmp_path / "outside.py")
    except OSError:
        pytest.skip("symlink creation unavailable")

    assert verify_materialized_fixture(manifest, destination).ok is False


def test_readonly_marking_reports_failure_without_raising(task, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("filesystem denied readonly marking")

    monkeypatch.setattr(fixture_module.os, "chmod", fail)

    seal = mark_fixture_read_only(task.fixture_root)

    assert seal.ok is False
    assert "read-only marking failed" in seal.reason


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_windows_handle_link_count_is_stable_across_acl_hardening(task):
    target = task.fixture_root / "stats_service.py"
    before = fixture_module._windows_file_link_count(target)
    fixture_module._apply_windows_acl(task.fixture_root)
    try:
        after = fixture_module._windows_file_link_count(target)
        assert (before, after) == (1, 1)
    finally:
        fixture_module._restore_fixture_access(task.fixture_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_windows_handle_link_count_rejects_true_hardlink_after_acl(task):
    target = task.fixture_root / "stats_service.py"
    duplicate = task.fixture_root / "duplicate.py"
    try:
        os.link(target, duplicate)
    except OSError:
        pytest.skip("hardlink creation unavailable")

    assert fixture_module._windows_file_link_count(target) == 2
    fixture_module._apply_windows_acl(task.fixture_root)
    try:
        assert fixture_module._windows_file_link_count(target) == 2
        with pytest.raises(FixtureSealError, match="hardlink"):
            fixture_module._collect_entries(task.fixture_root)
    finally:
        fixture_module._restore_fixture_access(task.fixture_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_windows_acl_seal_empirically_denies_write_and_creation(task):
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    try:
        seal = materialize_fixture(manifest, task.fixture_root, destination)
        target = destination / "stats_service.py"

        assert seal == FixtureSealResult(True, True, None)
        assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
        with pytest.raises(OSError):
            descriptor = os.open(target, os.O_WRONLY | os.O_APPEND)
            os.close(descriptor)
        with pytest.raises(OSError):
            descriptor = os.open(
                destination / "unexpected.txt",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(descriptor)
    finally:
        if destination.exists():
            fixture_module._restore_fixture_access(destination)


def test_readonly_marking_requires_successful_acl_apply_and_verification(
    task, monkeypatch
):
    calls = []

    def apply_acl(root):
        calls.append(("apply", Path(root)))

    def verify_acl(root):
        calls.append(("verify", Path(root)))

    seal = mark_fixture_read_only(
        task.fixture_root,
        acl_apply_fn=apply_acl,
        acl_verify_fn=verify_acl,
    )

    assert seal == FixtureSealResult(True, True, None)
    assert calls == [
        ("apply", task.fixture_root),
        ("verify", task.fixture_root),
    ]


@pytest.mark.parametrize("stage", ["apply", "verify"])
def test_readonly_marking_fails_closed_on_acl_stage_failure(
    task, stage
):
    def apply_acl(_root):
        if stage == "apply":
            raise FixtureSealError("injected ACL apply failure")

    def verify_acl(_root):
        if stage == "verify":
            raise FixtureSealError("injected ACL verification failure")

    seal = mark_fixture_read_only(
        task.fixture_root,
        acl_apply_fn=apply_acl,
        acl_verify_fn=verify_acl,
    )

    assert seal.ok is False
    assert seal.acl_hardened is False
    assert f"ACL {stage}" in seal.reason


def test_materialization_cleans_partial_destination_after_mid_copy_failure(
    task, monkeypatch
):
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    original_copy = fixture_module._copy_entry
    calls = 0

    def fail_second_copy(source, target, entry):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected copy failure")
        return original_copy(source, target, entry)

    monkeypatch.setattr(fixture_module, "_copy_entry", fail_second_copy)

    with pytest.raises(OSError, match="injected copy failure"):
        materialize_fixture(manifest, task.fixture_root, destination)

    assert destination.exists() is False
    monkeypatch.setattr(fixture_module, "_copy_entry", original_copy)
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(True, True),
    )
    assert materialize_fixture(manifest, task.fixture_root, destination).ok is True


def test_materialization_cleans_and_is_retryable_after_seal_failure(
    task, monkeypatch
):
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    original_mark = fixture_module.mark_fixture_read_only
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(
            False, False, "injected ACL verification failure"
        ),
    )

    with pytest.raises(FixtureSealError, match="ACL verification failure"):
        materialize_fixture(manifest, task.fixture_root, destination)

    assert destination.exists() is False
    monkeypatch.setattr(fixture_module, "mark_fixture_read_only", original_mark)
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(True, True),
    )
    assert materialize_fixture(manifest, task.fixture_root, destination).ok is True


def test_materialization_reports_original_and_cleanup_failures(
    task, monkeypatch
):
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"

    monkeypatch.setattr(
        fixture_module,
        "_copy_entry",
        lambda *_args: (_ for _ in ()).throw(OSError("copy exploded")),
    )
    monkeypatch.setattr(
        fixture_module,
        "_cleanup_partial_fixture",
        lambda _destination: (_ for _ in ()).throw(OSError("cleanup exploded")),
        raising=False,
    )

    with pytest.raises(
        FixtureSealError,
        match=r"copy exploded.*cleanup failed.*cleanup exploded",
    ):
        materialize_fixture(manifest, task.fixture_root, destination)
