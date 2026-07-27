from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli
from backend.first_win.doctor import DoctorCheck, DoctorReport
from backend.first_win.inspect_repo import RepositoryReceipt


def _receipt(root):
    return RepositoryReceipt(
        schema_version=1,
        receipt_id="abc123",
        created_at="2026-07-25T12:00:00Z",
        repository=str(root),
        repository_fingerprint="f" * 64,
        privacy={
            "local_only": True,
            "network_access": False,
            "commands_executed": [],
            "repository_modified": False,
        },
        stack=("python",),
        entry_points=("cli.py",),
        instruction_files=("AGENTS.md",),
        check_candidates=("python -m pytest -q",),
        findings=(),
        limits={"truncated": False},
    )


def test_parser_exposes_doctor_and_repository_json_mode():
    parser = cli.build_parser()

    doctor = parser.parse_args(["doctor", "."])
    inspect = parser.parse_args(["inspect", ".", "--json"])

    assert doctor.command == "doctor"
    assert doctor.dir == "."
    assert inspect.command == "inspect"
    assert inspect.model == "."
    assert inspect.json is True


def test_inspect_existing_directory_routes_to_repository_inspection(
    tmp_path, monkeypatch, capsys
):
    receipt_path = tmp_path / "receipts" / "receipt.json"
    seen = []
    monkeypatch.setattr(
        "backend.first_win.inspect_repository",
        lambda root: (seen.append(Path(root)) or _receipt(root), receipt_path),
    )
    monkeypatch.setattr(
        cli,
        "ollama",
        lambda *args, **kwargs: pytest.fail("model API must not be called"),
    )

    cli.cmd_inspect(SimpleNamespace(model=str(tmp_path), json=False))

    output = capsys.readouterr().out
    assert seen == [tmp_path]
    assert "python -m pytest -q" in output
    assert "discovered, not executed" in output.lower()
    assert str(receipt_path) in output


def test_model_inspection_remains_backward_compatible(monkeypatch, capsys):
    calls = []

    def fake_ollama(method, path, body=None, timeout=30):
        calls.append((method, path))
        if path == "/api/show":
            return {"details": {}, "modified_at": "now"}
        return {"models": [{"name": "qwen3:8b", "size": 1024**3}]}

    monkeypatch.setattr(cli, "ollama", fake_ollama)

    cli.cmd_inspect(SimpleNamespace(model="qwen3:8b", json=False))

    assert calls == [("POST", "/api/show"), ("GET", "/api/tags")]
    assert "Model: qwen3:8b" in capsys.readouterr().out


def test_repository_json_mode_is_valid_json_without_banner(
    tmp_path, monkeypatch, capsys
):
    receipt_path = tmp_path / "receipt.json"
    monkeypatch.setattr(
        "backend.first_win.inspect_repository",
        lambda root: (_receipt(root), receipt_path),
    )
    monkeypatch.setattr(cli.sys, "argv", ["lac", "inspect", str(tmp_path), "--json"])

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["receipt_id"] == "abc123"
    assert payload["receipt_path"] == str(receipt_path)


def test_doctor_failure_prints_remediation_and_exits_nonzero(
    tmp_path, monkeypatch, capsys
):
    report = DoctorReport(
        ready=False,
        checks=(
            DoctorCheck(
                "ollama",
                "fail",
                "Ollama unavailable.",
                {"error": "connection refused"},
                "Start Ollama, then retry.",
            ),
        ),
    )
    monkeypatch.setattr("backend.first_win.run_doctor", lambda **kwargs: report)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_doctor(
            SimpleNamespace(dir=str(tmp_path), json=False)
        )

    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "Start Ollama, then retry." in output
    assert "connection refused" in output


def test_doctor_json_is_machine_readable(tmp_path, monkeypatch, capsys):
    report = DoctorReport(
        ready=True,
        checks=(
            DoctorCheck(
                "hardware",
                "pass",
                "Hardware detected.",
                {"ram_gb": 32.0},
            ),
        ),
    )
    monkeypatch.setattr("backend.first_win.run_doctor", lambda **kwargs: report)

    cli.cmd_doctor(SimpleNamespace(dir=str(tmp_path), json=True))

    assert json.loads(capsys.readouterr().out) == {
        "checks": [
            {
                "evidence": {"ram_gb": 32.0},
                "name": "hardware",
                "remediation": "",
                "status": "pass",
                "summary": "Hardware detected.",
            }
        ],
        "ready": True,
    }


def test_agent_unsupported_opencode_exits_with_clean_remediation(
    tmp_path, monkeypatch, capsys
):
    from backend.agent_launch.opencode_bin import OpenCodeUnsupportedVersion

    monkeypatch.setattr(
        "backend.agent_launch.launcher.launch_agent",
        lambda project: (_ for _ in ()).throw(
            OpenCodeUnsupportedVersion(
                "OpenCode 1.19.0 is installed; use 1.18.7."
            )
        ),
    )

    with pytest.raises(SystemExit) as exc:
        cli.cmd_agent(SimpleNamespace(dir=str(tmp_path)))

    assert exc.value.code == 1
    assert "use 1.18.7" in capsys.readouterr().err
