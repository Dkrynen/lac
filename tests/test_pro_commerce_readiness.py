from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "pro_commerce_readiness.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("pro_commerce_readiness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(tmp_path: Path, **overrides):
    repo = tmp_path / "model-hub"
    worker = repo / "worker"
    backend = repo / "backend"
    worker.mkdir(parents=True)
    backend.mkdir(parents=True)
    (backend / "pro_install.py").write_text(
        'PRO_GATE_URL = "https://replace-with-approved-pro-gate.example.invalid/pro/download"\n',
        encoding="utf-8",
    )
    (worker / "wrangler.toml").write_text(
        """
[vars]
POLAR_ORG_ID = "replace-from-private-operator-notes"
ARTIFACT_KEY = "replace-from-private-operator-notes"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "replace-from-private-operator-notes"
""".strip(),
        encoding="utf-8",
    )
    pro = tmp_path / "lac-pro"
    defaults = {
        "repo_root": repo,
        "lac_pro_root": pro,
        "worker_config": worker / "wrangler.toml",
        "gate_url": "",
        "require_baked_gate": False,
        "live_gate": False,
        "valid_key_env": "LAC_PRO_TEST_KEY",
        "skip_valid_key": False,
        "allow_missing_lac_pro": True,
        "min_artifact_bytes": 100_000,
        "timeout": 60,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_commerce_gate_reports_placeholder_gate_and_worker_config(tmp_path):
    gate = _load_gate()

    report = gate.build_report(_args(tmp_path))

    failed = {row["name"] for row in report["failed"]}
    assert "pro_gate_url" in failed
    assert "worker_config" in failed


def test_commerce_gate_accepts_env_gate_and_concrete_worker_config(tmp_path, monkeypatch):
    gate = _load_gate()
    args = _args(tmp_path)
    monkeypatch.setenv("LAC_PRO_GATE_URL", "https://gate.example.com/pro/download")
    args.worker_config.write_text(
        """
[vars]
POLAR_ORG_ID = "org_123"
ARTIFACT_KEY = "lac-pro/0.1.0/lac-pro.zip"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    rows = {row["name"]: row for row in gate.build_report(args)["checks"]}

    assert rows["pro_gate_url"]["ok"] is True
    assert rows["worker_config"]["ok"] is True


def test_worker_config_ignores_placeholder_words_in_comments(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    args.worker_config.write_text(
        """
# Replace from private operator notes before deploy.
[vars]
POLAR_ORG_ID = "org_123"
ARTIFACT_KEY = "lac-pro/0.1.0/lac-pro.zip"
[[r2_buckets]]
binding = "R2_BUCKET"
# replace-from-private-operator-notes
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is True


def test_worker_config_does_not_pass_from_commented_keys(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    args.worker_config.write_text(
        """
# POLAR_ORG_ID = "org_123"
[vars]
ARTIFACT_KEY = "lac-pro/0.1.0/lac-pro.zip"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is False
    assert "POLAR_ORG_ID" in row["data"]["missing"]


def test_require_baked_gate_rejects_env_only_gate(tmp_path, monkeypatch):
    gate = _load_gate()
    args = _args(tmp_path, require_baked_gate=True)
    monkeypatch.setenv("LAC_PRO_GATE_URL", "https://gate.example.com/pro/download")

    row = gate.check_gate_url(args)

    assert row["ok"] is False
    assert "bake" in row["detail"]


def test_valid_key_name_is_reported_without_secret_value(tmp_path, monkeypatch):
    gate = _load_gate()
    args = _args(tmp_path, skip_valid_key=True)
    monkeypatch.delenv("LAC_PRO_TEST_KEY", raising=False)

    row = gate.check_valid_key_artifact(args)

    assert row["ok"] is True
    assert row["data"]["env"] == "LAC_PRO_TEST_KEY"
    assert "LAC_PRO_TEST_KEY" in row["detail"]
    assert "key" not in row["data"]


def test_open_core_boundary_uses_ast_imports_only(tmp_path):
    gate = _load_gate()
    repo = tmp_path / "model-hub"
    repo.mkdir()
    (repo / "safe.py").write_text('text = "from lac_pro import plugin"\n', encoding="utf-8")
    args = _args(tmp_path, repo_root=repo)

    row = gate.check_open_core_boundary(args)

    assert row["ok"] is True

    (repo / "bad.py").write_text("from lac_pro import plugin\n", encoding="utf-8")
    row = gate.check_open_core_boundary(args)

    assert row["ok"] is False
    assert row["data"]["offenders"] == ["bad.py"]
