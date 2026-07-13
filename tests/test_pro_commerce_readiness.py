from __future__ import annotations

import argparse
from email.message import Message
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


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
        "worker_env": "",
        "allow_worker_placeholders": False,
        "require_rate_limiter": False,
        "require_receipt_signing": False,
        "gate_url": "",
        "require_baked_gate": False,
        "live_gate": False,
        "valid_key_env": "LAC_PRO_TEST_KEY",
        "expected_deployment_commit": "",
        "skip_valid_key": False,
        "allow_missing_lac_pro": True,
        "min_artifact_bytes": 100_000,
        "timeout": 60,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _write_concrete_worker_config(args, artifact_filename="lac-pro.zip"):
    args.worker_config.write_text(
        f"""
[vars]
POLAR_ORG_ID = "org_123"
LOCAL_PRO_BENEFIT_ID = "benefit_local"
PRO_CLOUD_BENEFIT_ID = "benefit_cloud"
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/{artifact_filename}"
ARTIFACT_FILENAME = {json.dumps(artifact_filename)}
ARTIFACT_SHA256 = "{'a' * 64}"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )


def test_named_production_worker_config_requires_rate_limiter_and_can_validate_public_placeholders(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path, worker_env="production", allow_worker_placeholders=True)
    args.worker_config.write_text(
        """
[env.production.vars]
POLAR_ORG_ID = "replace-from-private-operator-notes"
LOCAL_PRO_BENEFIT_ID = "replace-from-private-operator-notes"
PRO_CLOUD_BENEFIT_ID = "replace-from-private-operator-notes"
ARTIFACT_KEY = "replace-from-private-operator-notes"
ARTIFACT_FILENAME = "lac-pro.zip"
ARTIFACT_SHA256 = "replace-from-private-operator-notes"
[[env.production.r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "replace-production-from-private-operator-notes"
[[env.production.ratelimits]]
name = "PRO_GATE_RATE_LIMITER"
namespace_id = "270201"
[env.production.ratelimits.simple]
limit = 20
period = 60
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is True
    assert row["data"]["worker_env"] == "production"

    args.worker_config.write_text(
        args.worker_config.read_text(encoding="utf-8").replace(
            'name = "PRO_GATE_RATE_LIMITER"', 'name = "WRONG_LIMITER"'
        ),
        encoding="utf-8",
    )
    row = gate.check_worker_config(args)
    assert row["ok"] is False
    assert "PRO_GATE_RATE_LIMITER.namespace_id" in row["data"]["missing"]


def test_commerce_gate_reports_placeholder_gate_and_worker_config(tmp_path):
    gate = _load_gate()

    report = gate.build_report(_args(tmp_path))

    failed = {row["name"] for row in report["failed"]}
    assert "pro_gate_url" in failed
    assert "worker_config" in failed


def test_worker_config_can_require_receipt_signing_key_id(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path, require_receipt_signing=True)
    _write_concrete_worker_config(args)
    row = gate.check_worker_config(args)
    assert row["ok"] is False
    assert "ENTITLEMENT_SIGNING_KID" in row["data"]["missing"]

    args.worker_config.write_text(
        args.worker_config.read_text(encoding="utf-8").replace(
            'PRO_CLOUD_BENEFIT_ID = "benefit_cloud"',
            'PRO_CLOUD_BENEFIT_ID = "benefit_cloud"\n'
            'ENTITLEMENT_SIGNING_KID = "2026-primary"\n'
            'ENTITLEMENT_SIGNING_PUBLIC_KEY = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"',  # pragma: allowlist secret -- test public key
        ),
        encoding="utf-8",
    )
    args.worker_config.write_text(
        args.worker_config.read_text(encoding="utf-8")
        + '\n[version_metadata]\nbinding = "CF_VERSION_METADATA"\n',
        encoding="utf-8",
    )
    assert gate.check_worker_config(args)["ok"] is True


def test_commerce_gate_accepts_env_gate_and_concrete_worker_config(tmp_path, monkeypatch):
    gate = _load_gate()
    args = _args(tmp_path)
    monkeypatch.setenv("LAC_PRO_GATE_URL", "https://gate.example.com/pro/download")
    args.worker_config.write_text(
        """
[vars]
POLAR_ORG_ID = "org_123"
LOCAL_PRO_BENEFIT_ID = "benefit_local"
PRO_CLOUD_BENEFIT_ID = "benefit_cloud"
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/lac-pro.zip"
ARTIFACT_FILENAME = "lac-pro.zip"
ARTIFACT_SHA256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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
LOCAL_PRO_BENEFIT_ID = "benefit_local"
PRO_CLOUD_BENEFIT_ID = "benefit_cloud"
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/lac-pro.zip"
ARTIFACT_FILENAME = "lac-pro.zip"
ARTIFACT_SHA256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
[[r2_buckets]]
binding = "R2_BUCKET"
# replace-from-private-operator-notes
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is True


def test_worker_config_requires_tier_benefits_and_artifact_integrity(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    args.worker_config.write_text(
        """
[vars]
POLAR_ORG_ID = "org_123"
ARTIFACT_KEY = "lac-pro/0.1.0/lac-pro.zip"
ARTIFACT_FILENAME = "lac-pro.zip"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is False
    assert row["data"]["missing"] == [
        "LOCAL_PRO_BENEFIT_ID",
        "PRO_CLOUD_BENEFIT_ID",
        "ARTIFACT_SHA256",
    ]


def test_worker_config_report_never_exposes_configuration_values(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    secretish_values = {
        "POLAR_ORG_ID": "org_private_value",
        "LOCAL_PRO_BENEFIT_ID": "benefit_local_private_value",
        "PRO_CLOUD_BENEFIT_ID": "benefit_cloud_private_value",
        "ARTIFACT_KEY": "private/0.1.0/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/private-artifact.zip",
        "ARTIFACT_FILENAME": "private-artifact.zip",
        "ARTIFACT_SHA256": "b" * 64,
        "bucket": "private-bucket-name",
    }
    args.worker_config.write_text(
        f"""
[vars]
POLAR_ORG_ID = "{secretish_values['POLAR_ORG_ID']}"
LOCAL_PRO_BENEFIT_ID = "{secretish_values['LOCAL_PRO_BENEFIT_ID']}"
PRO_CLOUD_BENEFIT_ID = "{secretish_values['PRO_CLOUD_BENEFIT_ID']}"
ARTIFACT_KEY = "{secretish_values['ARTIFACT_KEY']}"
ARTIFACT_FILENAME = "{secretish_values['ARTIFACT_FILENAME']}"
ARTIFACT_SHA256 = "{secretish_values['ARTIFACT_SHA256']}"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "{secretish_values['bucket']}"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is True
    rendered = repr(row)
    for value in secretish_values.values():
        assert value not in rendered


def test_worker_config_rejects_malformed_artifact_sha_without_echoing_it(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    malformed = "sha256:not-a-raw-digest"
    args.worker_config.write_text(
        f"""
[vars]
POLAR_ORG_ID = "org_123"
LOCAL_PRO_BENEFIT_ID = "benefit_local"
PRO_CLOUD_BENEFIT_ID = "benefit_cloud"
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/lac-pro.zip"
ARTIFACT_FILENAME = "lac-pro.zip"
ARTIFACT_SHA256 = "{malformed}"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is False
    assert row["data"]["invalid"] == ["ARTIFACT_SHA256"]
    assert malformed not in repr(row)


def test_worker_config_rejects_mutable_non_hash_bearing_artifact_key(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    args.worker_config.write_text(
        f"""
[vars]
POLAR_ORG_ID = "org_123"
LOCAL_PRO_BENEFIT_ID = "benefit_local"
PRO_CLOUD_BENEFIT_ID = "benefit_cloud"
ARTIFACT_KEY = "lac-pro/latest/lac-pro.zip"
ARTIFACT_FILENAME = "lac-pro.zip"
ARTIFACT_SHA256 = "{'a' * 64}"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is False
    assert row["data"]["invalid"] == ["ARTIFACT_KEY"]


def test_worker_config_rejects_duplicate_tier_benefits_without_echoing_them(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    duplicate = "benefit_private_duplicate"
    args.worker_config.write_text(
        f"""
[vars]
POLAR_ORG_ID = "org_123"
LOCAL_PRO_BENEFIT_ID = "{duplicate}"
PRO_CLOUD_BENEFIT_ID = "{duplicate}"
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/lac-pro.zip"
ARTIFACT_FILENAME = "lac-pro.zip"
ARTIFACT_SHA256 = "{'a' * 64}"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is False
    assert row["data"]["invalid"] == [
        "LOCAL_PRO_BENEFIT_ID",
        "PRO_CLOUD_BENEFIT_ID",
    ]
    assert duplicate not in repr(row)


@pytest.mark.parametrize(
    "unsafe_filename",
    [
        "../lac-pro.zip",
        r"folder\lac-pro.zip",
        'lac-pro.zip"; filename="leak.txt',
        "lac-pro.zip\r\nX-Leak: yes",
        "lác-pro.zip",
        ".lac-pro.zip",
        "lac-pro.tar.gz",
        "lac-pro.zip.exe",
        "a" * 125 + ".zip",
        "CON.zip",
        "nul.tar.zip",
        "COM1.zip",
        "lPt9.release.zip",
    ],
)
def test_worker_config_rejects_artifact_filename_the_worker_would_refuse(
    tmp_path, unsafe_filename
):
    gate = _load_gate()
    args = _args(tmp_path)
    args.worker_config.write_text(
        f"""
[vars]
POLAR_ORG_ID = "org_123"
LOCAL_PRO_BENEFIT_ID = "benefit_local"
PRO_CLOUD_BENEFIT_ID = "benefit_cloud"
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/lac-pro.zip"
ARTIFACT_FILENAME = {json.dumps(unsafe_filename)}
ARTIFACT_SHA256 = "{'a' * 64}"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is False
    assert row["data"]["invalid"] == ["ARTIFACT_FILENAME"]
    assert unsafe_filename not in repr(row)


def test_worker_config_reports_an_empty_artifact_filename_as_missing(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    _write_concrete_worker_config(args, artifact_filename="")

    row = gate.check_worker_config(args)

    assert row["ok"] is False
    assert "ARTIFACT_FILENAME" in row["data"]["missing"]
    assert "ARTIFACT_FILENAME" not in row["data"]["invalid"]


@pytest.mark.parametrize(
    "safe_filename",
    [
        "lac-pro.zip",
        "LAC_Pro-2.6.4.ZIP",
        "a" * 124 + ".zip",
    ],
)
def test_worker_config_accepts_the_same_safe_filename_boundary_as_the_worker(
    tmp_path, safe_filename
):
    gate = _load_gate()
    args = _args(tmp_path)
    args.worker_config.write_text(
        f"""
[vars]
POLAR_ORG_ID = "org_123"
LOCAL_PRO_BENEFIT_ID = "benefit_local"
PRO_CLOUD_BENEFIT_ID = "benefit_cloud"
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/{safe_filename}"
ARTIFACT_FILENAME = {json.dumps(safe_filename)}
ARTIFACT_SHA256 = "{'a' * 64}"
[[r2_buckets]]
binding = "R2_BUCKET"
bucket_name = "lac-pro-private"
""".strip(),
        encoding="utf-8",
    )

    row = gate.check_worker_config(args)

    assert row["ok"] is True
    assert safe_filename not in repr(row)


def test_worker_config_does_not_pass_from_commented_keys(tmp_path):
    gate = _load_gate()
    args = _args(tmp_path)
    args.worker_config.write_text(
        """
# POLAR_ORG_ID = "org_123"
[vars]
ARTIFACT_KEY = "lac-pro/0.1.0/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/lac-pro.zip"
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


@pytest.mark.parametrize(
    "url",
    [
        "http://gate.example/pro/download",
        "https:///pro/download",
        "https://user:pass@gate.example/pro/download",  # pragma: allowlist secret -- deliberately invalid fixture
        "https://gate.example/other",
        "https://gate.example/pro/download?key=leak",
        "https://gate.example/pro/download#fragment",
    ],
)
def test_gate_url_rejects_noncanonical_or_unsafe_endpoints(tmp_path, url):
    gate = _load_gate()
    row = gate.check_gate_url(_args(tmp_path, gate_url=url))
    assert row["ok"] is False
    assert url not in repr(row)


def test_valid_key_name_is_reported_without_secret_value(tmp_path, monkeypatch):
    gate = _load_gate()
    args = _args(tmp_path, skip_valid_key=True)
    monkeypatch.delenv("LAC_PRO_TEST_KEY", raising=False)

    row = gate.check_valid_key_artifact(args)

    assert row["ok"] is True
    assert row["data"]["env"] == "LAC_PRO_TEST_KEY"
    assert "LAC_PRO_TEST_KEY" in row["detail"]
    assert "key" not in row["data"]


def test_valid_key_artifact_requires_matching_integrity_header(tmp_path, monkeypatch):
    gate = _load_gate()
    args = _args(
        tmp_path,
        gate_url="https://gate.example.com/pro/download",
        min_artifact_bytes=1,
    )
    _write_concrete_worker_config(args)
    body = b"compiled-private-artifact"
    digest = hashlib.sha256(body).hexdigest()
    monkeypatch.setenv("LAC_PRO_TEST_KEY", "private-test-key")
    monkeypatch.setattr(
        gate,
        "_post_license",
        lambda *unused: (
            200,
            body,
            {
                "X-LAC-Artifact-SHA256": digest,
                "Content-Disposition": 'attachment; filename="lac-pro.zip"',
            },
        ),
    )

    row = gate.check_valid_key_artifact(args)

    assert row["ok"] is True
    assert row["data"]["integrity"] == "verified"
    assert row["data"]["content_disposition"] == "verified"
    assert digest not in repr(row)
    assert "private-test-key" not in repr(row)


def test_live_invalid_and_valid_smokes_bind_to_exact_deployment_commit(tmp_path, monkeypatch):
    gate = _load_gate()
    commit = "a" * 40
    args = _args(
        tmp_path,
        gate_url="https://gate.example.com/pro/download",
        min_artifact_bytes=1,
        expected_deployment_commit=commit,
    )
    _write_concrete_worker_config(args)

    monkeypatch.setattr(
        gate,
        "_post_license",
        lambda *unused: (
            403,
            b'{}',
            {"X-LAC-Deployment-Commit": commit},
        ),
    )
    invalid = gate.check_invalid_key(args)
    assert invalid["ok"] is True
    assert invalid["data"]["deployment_commit"] == "verified"
    assert commit not in repr(invalid)

    body = b"compiled-private-artifact"
    digest = hashlib.sha256(body).hexdigest()
    monkeypatch.setenv("LAC_PRO_TEST_KEY", "private-test-key")
    monkeypatch.setattr(
        gate,
        "_post_license",
        lambda *unused: (
            200,
            body,
            {
                "X-LAC-Deployment-Commit": commit,
                "X-LAC-Artifact-SHA256": digest,
                "Content-Disposition": 'attachment; filename="lac-pro.zip"',
            },
        ),
    )
    valid = gate.check_valid_key_artifact(args)
    assert valid["ok"] is True
    assert valid["data"]["deployment_commit"] == "verified"
    assert commit not in repr(valid)


@pytest.mark.parametrize("header", [None, "b" * 40, "v2.7.0"])
def test_live_smoke_rejects_missing_wrong_or_malformed_deployment_commit(
    tmp_path, monkeypatch, header
):
    gate = _load_gate()
    args = _args(
        tmp_path,
        gate_url="https://gate.example.com/pro/download",
        expected_deployment_commit="a" * 40,
    )
    headers = {} if header is None else {"X-LAC-Deployment-Commit": header}
    monkeypatch.setattr(gate, "_post_license", lambda *unused: (403, b'{}', headers))
    row = gate.check_invalid_key(args)
    assert row["ok"] is False
    assert row["data"]["deployment_commit"] != "verified"


def test_valid_key_artifact_accepts_real_list_shaped_transport_headers(
    tmp_path, monkeypatch
):
    gate = _load_gate()
    args = _args(
        tmp_path,
        gate_url="https://gate.example.com/pro/download",
        min_artifact_bytes=1,
    )
    _write_concrete_worker_config(args)
    body = b"compiled-private-artifact"
    digest = hashlib.sha256(body).hexdigest()
    headers = [
        ("X-LAC-Artifact-SHA256", digest),
        ("Content-Disposition", 'attachment; filename="lac-pro.zip"'),
        ("Content-Type", "application/octet-stream"),
    ]
    monkeypatch.setenv("LAC_PRO_TEST_KEY", "private-test-key")
    monkeypatch.setattr(
        gate,
        "_post_license",
        lambda *unused: (200, body, headers),
    )

    row = gate.check_valid_key_artifact(args)

    assert row["ok"] is True
    assert row["data"]["content_type"] == "application/octet-stream"
    assert "private-test-key" not in repr(row)


@pytest.mark.parametrize(
    ("headers", "integrity"),
    [
        ({}, "missing"),
        ({"X-LAC-Artifact-SHA256": "not-a-raw-digest"}, "malformed"),
        ({"X-LAC-Artifact-SHA256": "0" * 64}, "mismatch"),
    ],
)
def test_valid_key_artifact_rejects_missing_malformed_or_mismatched_integrity(
    tmp_path, monkeypatch, headers, integrity
):
    gate = _load_gate()
    args = _args(
        tmp_path,
        gate_url="https://gate.example.com/pro/download",
        min_artifact_bytes=1,
    )
    _write_concrete_worker_config(args)
    body = b"compiled-private-artifact"
    response_headers = {
        "Content-Disposition": 'attachment; filename="lac-pro.zip"',
        **headers,
    }
    monkeypatch.setenv("LAC_PRO_TEST_KEY", "private-test-key")
    monkeypatch.setattr(
        gate,
        "_post_license",
        lambda *unused: (200, body, response_headers),
    )

    row = gate.check_valid_key_artifact(args)

    assert row["ok"] is False
    assert row["data"]["integrity"] == integrity
    assert row["data"]["content_disposition"] == "verified"
    for value in headers.values():
        assert value not in repr(row)
    assert "private-test-key" not in repr(row)


@pytest.mark.parametrize(
    ("disposition_headers", "state"),
    [
        ({}, "missing"),
        ({"Content-Disposition": "inline"}, "mismatch"),
        (
            {"Content-Disposition": 'attachment; filename="../lac-pro.zip"'},
            "mismatch",
        ),
        (
            {"content-disposition": 'attachment; filename="other.zip"'},
            "mismatch",
        ),
        (
            {"Content-Disposition": 'attachment; filename="lác-pro.zip"'},
            "mismatch",
        ),
        (
            {
                "Content-Disposition": 'attachment; filename="lac-pro.zip"',
                "content-disposition": 'attachment; filename="lac-pro.zip"',
            },
            "malformed",
        ),
    ],
)
def test_valid_key_artifact_rejects_missing_or_wrong_content_disposition(
    tmp_path, monkeypatch, disposition_headers, state
):
    gate = _load_gate()
    args = _args(
        tmp_path,
        gate_url="https://gate.example.com/pro/download",
        min_artifact_bytes=1,
    )
    _write_concrete_worker_config(args)
    body = b"compiled-private-artifact"
    digest = hashlib.sha256(body).hexdigest()
    response_headers = {
        "X-LAC-Artifact-SHA256": digest,
        **disposition_headers,
    }
    monkeypatch.setenv("LAC_PRO_TEST_KEY", "private-test-key")
    monkeypatch.setattr(
        gate,
        "_post_license",
        lambda *unused: (200, body, response_headers),
    )

    row = gate.check_valid_key_artifact(args)

    assert row["ok"] is False
    assert row["data"]["integrity"] == "verified"
    assert row["data"]["content_disposition"] == state
    for value in disposition_headers.values():
        assert value not in repr(row)
    assert "private-test-key" not in repr(row)


def test_live_transport_preserves_same_case_duplicate_artifact_headers(monkeypatch):
    gate = _load_gate()
    digest = "a" * 64
    headers = Message()
    headers.add_header("X-LAC-Artifact-SHA256", digest)
    headers.add_header("X-LAC-Artifact-SHA256", digest)
    headers.add_header("Content-Disposition", 'attachment; filename="lac-pro.zip"')
    headers.add_header("Content-Disposition", 'attachment; filename="lac-pro.zip"')

    class _Response:
        def getcode(self):
            return 200

        def read(self):
            return b"artifact"

        def __enter__(self):
            self.headers = headers
            return self

        def __exit__(self, *unused):
            return False

    monkeypatch.setattr(gate.urllib.request, "urlopen", lambda *unused, **kwargs: _Response())

    _, body, transported = gate._post_license("https://gate.example/download", "key", 1)

    assert gate._artifact_integrity(body, transported) == "malformed"
    assert gate._content_disposition_state(transported, "lac-pro.zip") == "malformed"


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
