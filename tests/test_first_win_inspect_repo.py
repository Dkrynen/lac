from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.first_win import inspect_repo
from backend.first_win.inspect_repo import inspect_repository


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _inspect(root, receipt_root, **kwargs):
    return inspect_repository(
        root,
        now_fn=lambda: NOW,
        receipt_root=receipt_root,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("files", "expected_stack", "expected_check"),
    [
        (
            {
                "pyproject.toml": "[tool.pytest.ini_options]\n",
                "tests/test_app.py": "def test_ok(): assert True\n",
            },
            "python",
            "python -m pytest -q",
        ),
        (
            {
                "package.json": '{"scripts":{"test":"vitest run"}}',
                "src/index.ts": "export const ok = true\n",
            },
            "node",
            "npm test",
        ),
        (
            {"Cargo.toml": "[package]\nname='demo'\n", "src/main.rs": "fn main() {}"},
            "rust",
            "cargo test",
        ),
        (
            {"go.mod": "module example.test/demo\n", "main.go": "package main\n"},
            "go",
            "go test ./...",
        ),
    ],
)
def test_detects_stack_and_literal_check_candidates(
    tmp_path, files, expected_stack, expected_check
):
    root = tmp_path / "repo"
    root.mkdir()
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    receipt, _path = _inspect(root, tmp_path / "receipts")

    assert expected_stack in receipt.stack
    assert expected_check in receipt.check_candidates
    assert receipt.privacy["commands_executed"] == []
    assert receipt.privacy["network_access"] is False


def test_reports_instructions_and_entry_points(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("Inspect carefully.\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("Use tests.\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (root / "cli.py").write_text("def main(): pass\n", encoding="utf-8")

    receipt, _path = _inspect(root, tmp_path / "receipts")

    assert receipt.instruction_files == ("AGENTS.md", "CLAUDE.md", "README.md")
    assert receipt.entry_points == ("cli.py",)


def test_source_extension_identifies_stack_without_manifest(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "service.py").write_text("VALUE = 1\n", encoding="utf-8")

    receipt, _path = _inspect(root, tmp_path / "receipts")

    assert receipt.stack == ("python",)


def test_secrets_dependencies_vcs_and_build_outputs_do_not_affect_receipt(
    tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    excluded = {
        ".env": "SECRET=one",
        "credentials.json": '{"token":"one"}',
        "token.json": '{"token":"one"}',
        "auth.json": '{"token":"one"}',
        "secrets.json": '{"token":"one"}',
        ".npmrc": "//registry/:_authToken=one",
        ".pypirc": "password=one",
        ".netrc": "password one",
        ".aws/credentials": "secret_access_key=one",
        "private.pem": "one",
        ".git/config": "one",
        "node_modules/pkg/index.js": "one",
        ".venv/lib/site.py": "one",
        "dist/bundle.js": "one",
        "build/result.bin": "one",
        ".model-hub/private.db": "one",
        ".tmp/scratch.txt": "one",
        ".cache/cache.bin": "one",
    }
    for relative, content in excluded.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    first, _ = _inspect(root, tmp_path / "receipts-a")
    for relative in excluded:
        (root / relative).write_text("changed-secret-or-generated-data", encoding="utf-8")
    second, path = _inspect(root, tmp_path / "receipts-b")
    serialized = path.read_text(encoding="utf-8")

    assert first.repository_fingerprint == second.repository_fingerprint
    for relative in excluded:
        assert relative.replace("\\", "/") not in serialized
    assert "changed-secret-or-generated-data" not in serialized


def test_symlink_is_not_read_or_fingerprinted(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private-one", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")

    first, _ = _inspect(root, tmp_path / "receipts-a")
    outside.write_text("private-two", encoding="utf-8")
    second, path = _inspect(root, tmp_path / "receipts-b")

    assert first.repository_fingerprint == second.repository_fingerprint
    assert "linked.txt" not in path.read_text(encoding="utf-8")


def test_windows_reparse_point_is_treated_as_indirection(monkeypatch, tmp_path):
    candidate = tmp_path / "junction"
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(
        inspect_repo.os,
        "lstat",
        lambda path: type("Stat", (), {"st_file_attributes": 0x400})(),
    )

    assert inspect_repo._is_indirection(candidate) is True


def test_fingerprint_is_stable_and_changes_with_safe_content(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    first, _ = _inspect(root, tmp_path / "receipts-a")
    second, _ = _inspect(root, tmp_path / "receipts-b")
    source.write_text("VALUE = 2\n", encoding="utf-8")
    changed, _ = _inspect(root, tmp_path / "receipts-c")

    assert first.repository_fingerprint == second.repository_fingerprint
    assert first.repository_fingerprint != changed.repository_fingerprint


def test_receipt_is_atomic_and_repository_is_unchanged(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    replacements = []
    real_replace = __import__("os").replace

    def recording_replace(source, target):
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr("backend.first_win.inspect_repo.os.replace", recording_replace)

    receipt, receipt_path = _inspect(root, tmp_path / "receipts")
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    assert before == after
    assert replacements and replacements[0][1] == receipt_path
    assert replacements[0][0] != receipt_path
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["receipt_id"] == (
        receipt.receipt_id
    )


def test_limits_stop_truthfully(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(5):
        (root / f"{index}.py").write_text("x" * 30, encoding="utf-8")

    receipt, _ = _inspect(
        root,
        tmp_path / "receipts",
        max_files=2,
        max_bytes=40,
    )

    assert receipt.limits["truncated"] is True
    assert receipt.limits["safe_files_discovered"] == 3
    assert receipt.limits["files_fingerprinted"] <= 2
    assert receipt.limits["bytes_read"] <= 40
    assert any(finding.kind == "inspection_limit" for finding in receipt.findings)


def test_project_boundary_rejects_non_directory_and_home_root(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        inspect_repository(
            file_path,
            now_fn=lambda: NOW,
            receipt_root=tmp_path / "receipts",
            home=tmp_path / "different-home",
            data_root=tmp_path / "different-data",
        )

    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(ValueError, match="home"):
        inspect_repository(
            home,
            now_fn=lambda: NOW,
            receipt_root=tmp_path / "receipts",
            home=home,
            data_root=tmp_path / "different-data",
        )


def test_receipt_store_cannot_modify_the_inspected_repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the inspected repository"):
        _inspect(root, root / ".lac-receipts")

    assert not (root / ".lac-receipts").exists()
