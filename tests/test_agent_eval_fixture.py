from __future__ import annotations

import os
from pathlib import Path

import pytest

import backend.agent_eval.fixture as fixture_module
from backend.agent_eval.fixture import (
    FixtureEntry,
    FixtureManifest,
    FixtureSealError,
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


def test_materialized_fixture_matches_file_and_aggregate_hashes(task):
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
def test_verification_detects_each_destination_drift(task, change):
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


def test_verification_rejects_linked_destination(task, tmp_path):
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
