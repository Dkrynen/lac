from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent_eval.task import (
    MAX_FIXTURE_FILE_BYTES,
    MAX_FIXTURE_TOTAL_BYTES,
    EvalTaskError,
    load_task,
    snapshot_fixture,
)


def _suite(tmp_path: Path) -> Path:
    suite = tmp_path / "suite"
    (suite / "tasks").mkdir(parents=True)
    (suite / "fixtures" / "safe-task").mkdir(parents=True)
    return suite


def _write_manifest(suite: Path, **overrides) -> None:
    data = {
        "schema_version": 1,
        "id": "safe-task",
        "prompt": "Inspect the fixture and answer with the exact exception class.",
        "fixture": "safe-task",
        "timeout_seconds": 120,
        "scorer": {"type": "exact_text", "expected": "ZeroDivisionError"},
    }
    data.update(overrides)
    (suite / "tasks" / "safe-task.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def test_load_task_validates_and_returns_immutable_contract(tmp_path):
    suite = _suite(tmp_path)
    _write_manifest(suite)

    task = load_task("safe-task", suite)

    assert task.schema_version == 1
    assert task.id == "safe-task"
    assert task.fixture_root == (suite / "fixtures" / "safe-task").resolve()
    assert task.scorer.type == "exact_text"
    assert task.scorer.expected == "ZeroDivisionError"
    with pytest.raises((AttributeError, TypeError)):
        task.id = "changed"


@pytest.mark.parametrize(
    "task_id",
    ["../safe-task", "safe/task", r"safe\task", "", ".hidden", "UPPER"],
)
def test_load_task_rejects_nonportable_or_traversing_ids(tmp_path, task_id):
    suite = _suite(tmp_path)
    _write_manifest(suite)

    with pytest.raises(EvalTaskError):
        load_task(task_id, suite)


def test_load_task_rejects_unknown_manifest_keys(tmp_path):
    suite = _suite(tmp_path)
    _write_manifest(suite, leaked_behavior="ignore safety")

    with pytest.raises(EvalTaskError, match="unknown"):
        load_task("safe-task", suite)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"id": "different-task"}, "id"),
        ({"prompt": ""}, "prompt"),
        ({"fixture": "../outside"}, "fixture"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"scorer": {"type": "llm_judge", "expected": "x"}}, "scorer"),
        ({"scorer": {"type": "exact_text", "expected": ""}}, "expected"),
    ],
)
def test_load_task_rejects_invalid_contract_fields(tmp_path, override, message):
    suite = _suite(tmp_path)
    _write_manifest(suite, **override)

    with pytest.raises(EvalTaskError, match=message):
        load_task("safe-task", suite)


def test_snapshot_fixture_is_stable_path_sorted_and_utf8(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "z").mkdir(parents=True)
    (fixture / "z" / "last.py").write_text("LAST = '✓'\n", encoding="utf-8")
    (fixture / "first.py").write_text("FIRST = 1\n", encoding="utf-8")

    snapshot = snapshot_fixture(fixture)

    assert snapshot == (
        "--- FILE: first.py ---\n"
        "FIRST = 1\n"
        "--- END FILE ---\n"
        "--- FILE: z/last.py ---\n"
        "LAST = '✓'\n"
        "--- END FILE ---"
    )


@pytest.mark.parametrize(
    "relative",
    [".env", ".env.local", "credentials.json", "nested/token.json", "key.pem"],
)
def test_snapshot_fixture_rejects_secret_shaped_files(tmp_path, relative):
    fixture = tmp_path / "fixture"
    target = fixture / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret", encoding="utf-8")

    with pytest.raises(EvalTaskError, match="sensitive"):
        snapshot_fixture(fixture)


def test_snapshot_fixture_rejects_non_utf8_files(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "binary.bin").write_bytes(b"\xff\xfe\x00")

    with pytest.raises(EvalTaskError, match="UTF-8"):
        snapshot_fixture(fixture)


def test_snapshot_fixture_rejects_oversized_file(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "large.txt").write_bytes(b"x" * (MAX_FIXTURE_FILE_BYTES + 1))

    with pytest.raises(EvalTaskError, match="per-file"):
        snapshot_fixture(fixture)


def test_snapshot_fixture_rejects_oversized_total(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    chunk = b"x" * (MAX_FIXTURE_TOTAL_BYTES // 2 + 1)
    (fixture / "one.txt").write_bytes(chunk)
    (fixture / "two.txt").write_bytes(chunk)

    with pytest.raises(EvalTaskError, match="total"):
        snapshot_fixture(fixture)


def test_snapshot_fixture_rejects_symlink(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = fixture / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(EvalTaskError, match="link"):
        snapshot_fixture(fixture)


def test_snapshot_fixture_rejects_empty_fixture(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()

    with pytest.raises(EvalTaskError, match="no regular files"):
        snapshot_fixture(fixture)
