from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INVALID_KEY = "LAC-PRO-INVALID-COMMERCE-SMOKE"
PLACEHOLDER_MARKERS = (
    "replace-",
    "example.invalid",
    "private-operator-notes",
    "operator-notes",
)


def _result(
    name: str,
    ok: bool,
    *,
    lane: str = "commerce",
    detail: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "lane": lane,
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "data": data or {},
    }


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _is_windows_reserved_filename(filename: str) -> bool:
    stem = filename.split(".", 1)[0]
    return re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", stem, re.IGNORECASE) is not None


def _is_immutable_artifact_key(key: str, sha256: str, filename: str) -> bool:
    parts = key.split("/")
    return (
        len(parts) >= 4
        and all(part and part not in {".", ".."} for part in parts)
        and any(
            re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", part)
            for part in parts
        )
        and sha256.lower() in parts
        and parts[-1] == filename
    )


def _gate_constant(repo_root: Path) -> str | None:
    text = _read_text(repo_root / "backend" / "pro_install.py")
    match = re.search(r'PRO_GATE_URL\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _configured_gate_url(args: argparse.Namespace) -> tuple[str | None, str]:
    if args.gate_url:
        return args.gate_url, "argument"
    env_url = os.environ.get("LAC_PRO_GATE_URL")
    if env_url:
        return env_url, "environment"
    return _gate_constant(Path(args.repo_root)), "source"


def check_gate_url(args: argparse.Namespace) -> dict[str, Any]:
    url, source = _configured_gate_url(args)
    source_constant = _gate_constant(Path(args.repo_root))
    try:
        parsed = urlsplit(str(url or ""))
        valid_url = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443}
            and parsed.path == "/pro/download"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid_url = False
    ok = not _is_placeholder(url) and valid_url
    if args.require_baked_gate:
        ok = ok and source == "source" and not _is_placeholder(source_constant)
    detail = "gate URL is configured" if ok else "gate URL is still a placeholder or missing"
    if args.require_baked_gate and source != "source":
        detail = "public commerce build must bake PRO_GATE_URL, not rely on an env override"
    return _result(
        "pro_gate_url",
        ok,
        detail=detail,
        data={
            "source": source,
            "configured": bool(url),
            "requires_baked_gate": bool(args.require_baked_gate),
        },
    )


def check_worker_config(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.worker_config)
    if not path.exists():
        return _result("worker_config", False, detail="worker config file is missing", data={"path": str(path)})
    text = _read_text(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return _result("worker_config", False, detail=f"worker config is invalid TOML: {exc}", data={"path": str(path)})

    config_scope: object = data
    worker_env = str(getattr(args, "worker_env", "") or "").strip()
    if worker_env:
        envs = data.get("env") if isinstance(data, dict) else None
        config_scope = envs.get(worker_env) if isinstance(envs, dict) else None
        if not isinstance(config_scope, dict):
            return _result(
                "worker_config",
                False,
                detail=f"worker environment {worker_env!r} is missing",
                data={"path": str(path), "worker_env": worker_env},
            )

    vars_table = config_scope.get("vars") if isinstance(config_scope, dict) else {}
    buckets = config_scope.get("r2_buckets") if isinstance(config_scope, dict) else []
    rate_limits = config_scope.get("ratelimits") if isinstance(config_scope, dict) else []
    if not isinstance(vars_table, dict):
        vars_table = {}
    if not isinstance(buckets, list):
        buckets = []
    if not isinstance(rate_limits, list):
        rate_limits = []

    limiter = next(
        (
            item
            for item in rate_limits
            if isinstance(item, dict) and item.get("name") == "PRO_GATE_RATE_LIMITER"
        ),
        {},
    )
    limiter_simple = limiter.get("simple") if isinstance(limiter, dict) else {}
    version_metadata = (
        config_scope.get("version_metadata") if isinstance(config_scope, dict) else {}
    )

    values: dict[str, object] = {
        "POLAR_ORG_ID": vars_table.get("POLAR_ORG_ID"),
        "LOCAL_PRO_BENEFIT_ID": vars_table.get("LOCAL_PRO_BENEFIT_ID"),
        "PRO_CLOUD_BENEFIT_ID": vars_table.get("PRO_CLOUD_BENEFIT_ID"),
        "ARTIFACT_KEY": vars_table.get("ARTIFACT_KEY"),
        "ARTIFACT_FILENAME": vars_table.get("ARTIFACT_FILENAME"),
        "ARTIFACT_SHA256": vars_table.get("ARTIFACT_SHA256"),
        "R2_BUCKET.bucket_name": next(
            (
                bucket.get("bucket_name")
                for bucket in buckets
                if isinstance(bucket, dict) and bucket.get("binding") == "R2_BUCKET"
            ),
            None,
        ),
    }
    if bool(getattr(args, "require_receipt_signing", False)) or (
        "ENTITLEMENT_SIGNING_KID" in vars_table
    ):
        values["ENTITLEMENT_SIGNING_KID"] = vars_table.get(
            "ENTITLEMENT_SIGNING_KID"
        )
        values["ENTITLEMENT_SIGNING_PUBLIC_KEY"] = vars_table.get(
            "ENTITLEMENT_SIGNING_PUBLIC_KEY"
        )
    if bool(getattr(args, "require_receipt_signing", False)) or isinstance(
        version_metadata, dict
    ) and bool(version_metadata):
        values["CF_VERSION_METADATA.binding"] = (
            version_metadata.get("binding")
            if isinstance(version_metadata, dict)
            else None
        )
    if "POLAR_API_BASE_URL" in vars_table:
        values["POLAR_API_BASE_URL"] = vars_table.get("POLAR_API_BASE_URL")
    if worker_env or bool(getattr(args, "require_rate_limiter", False)):
        values.update({
            "PRO_GATE_RATE_LIMITER.namespace_id": limiter.get("namespace_id") if isinstance(limiter, dict) else None,
            "PRO_GATE_RATE_LIMITER.limit": limiter_simple.get("limit") if isinstance(limiter_simple, dict) else None,
            "PRO_GATE_RATE_LIMITER.period": limiter_simple.get("period") if isinstance(limiter_simple, dict) else None,
        })
    missing = [
        name
        for name, value in values.items()
        if (
            (name.endswith(".limit") or name.endswith(".period"))
            and (not isinstance(value, int) or isinstance(value, bool) or value <= 0)
        )
        or (
            not (name.endswith(".limit") or name.endswith(".period"))
            and (not isinstance(value, str) or not value.strip())
        )
    ]
    placeholders = sorted(name for name, value in values.items() if isinstance(value, str) and _is_placeholder(value))
    artifact_sha = values.get("ARTIFACT_SHA256")
    invalid: list[str] = []
    local_benefit = values.get("LOCAL_PRO_BENEFIT_ID")
    cloud_benefit = values.get("PRO_CLOUD_BENEFIT_ID")
    if (
        isinstance(local_benefit, str)
        and isinstance(cloud_benefit, str)
        and local_benefit
        and not _is_placeholder(local_benefit)
        and not _is_placeholder(cloud_benefit)
        and local_benefit == cloud_benefit
    ):
        invalid.extend(["LOCAL_PRO_BENEFIT_ID", "PRO_CLOUD_BENEFIT_ID"])

    artifact_filename = values.get("ARTIFACT_FILENAME")
    if (
        isinstance(artifact_filename, str)
        and artifact_filename.strip()
        and not _is_placeholder(artifact_filename)
        and (
            len(artifact_filename) > 128
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", artifact_filename) is None
            or not artifact_filename.lower().endswith(".zip")
            or _is_windows_reserved_filename(artifact_filename)
        )
    ):
        invalid.append("ARTIFACT_FILENAME")

    if (
        isinstance(artifact_sha, str)
        and artifact_sha.strip()
        and not _is_placeholder(artifact_sha)
        and re.fullmatch(r"[0-9A-Fa-f]{64}", artifact_sha) is None
    ):
        invalid.append("ARTIFACT_SHA256")
    artifact_key = values.get("ARTIFACT_KEY")
    if (
        isinstance(artifact_key, str)
        and artifact_key.strip()
        and not _is_placeholder(artifact_key)
        and isinstance(artifact_sha, str)
        and re.fullmatch(r"[0-9A-Fa-f]{64}", artifact_sha) is not None
        and isinstance(artifact_filename, str)
        and artifact_filename.strip()
        and not _is_placeholder(artifact_filename)
        and "ARTIFACT_FILENAME" not in invalid
        and not _is_immutable_artifact_key(
            artifact_key,
            artifact_sha,
            artifact_filename,
        )
    ):
        invalid.append("ARTIFACT_KEY")
    signing_kid = values.get("ENTITLEMENT_SIGNING_KID")
    if (
        isinstance(signing_kid, str)
        and signing_kid.strip()
        and not _is_placeholder(signing_kid)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", signing_kid) is None
    ):
        invalid.append("ENTITLEMENT_SIGNING_KID")
    signing_public_key = values.get("ENTITLEMENT_SIGNING_PUBLIC_KEY")
    if (
        isinstance(signing_public_key, str)
        and signing_public_key.strip()
        and not _is_placeholder(signing_public_key)
    ):
        try:
            public_key_bytes = base64.urlsafe_b64decode(
                signing_public_key + "=" * (-len(signing_public_key) % 4)
            )
            valid_public_key = (
                len(public_key_bytes) == 32
                and base64.urlsafe_b64encode(public_key_bytes)
                .rstrip(b"=")
                .decode("ascii")
                == signing_public_key
            )
        except Exception:  # noqa: BLE001 - protected config validation
            valid_public_key = False
        if not valid_public_key:
            invalid.append("ENTITLEMENT_SIGNING_PUBLIC_KEY")
    version_binding = values.get("CF_VERSION_METADATA.binding")
    if (
        isinstance(version_binding, str)
        and version_binding.strip()
        and version_binding != "CF_VERSION_METADATA"
    ):
        invalid.append("CF_VERSION_METADATA.binding")
    polar_api_base = values.get("POLAR_API_BASE_URL")
    expected_polar_base = {
        "staging": "https://sandbox-api.polar.sh/v1",
        "production": "https://api.polar.sh/v1",
    }.get(worker_env)
    if (
        isinstance(polar_api_base, str)
        and polar_api_base.strip()
        and not _is_placeholder(polar_api_base)
        and (
            polar_api_base not in {
                "https://sandbox-api.polar.sh/v1",
                "https://api.polar.sh/v1",
            }
            or (
                expected_polar_base is not None
                and polar_api_base != expected_polar_base
            )
        )
    ):
        invalid.append("POLAR_API_BASE_URL")
    if bool(getattr(args, "allow_worker_placeholders", False)):
        placeholders = []
    ok = not placeholders and not missing and not invalid
    detail = "worker config has concrete Polar/R2 settings" if ok else "worker config is not deploy-ready"
    return _result(
        "worker_config",
        ok,
        detail=detail,
        data={
            "path": str(path),
            "worker_env": worker_env or "top-level",
            "placeholders": placeholders,
            "missing": missing,
            "invalid": invalid,
        },
    )


def check_open_core_boundary(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root)
    offenders: list[str] = []
    skipped_dirs = {".git", ".venv", "__pycache__", "node_modules", "dist", "build"}
    for path in root.rglob("*.py"):
        if any(part in skipped_dirs for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "lac_pro" or alias.name.startswith("lac_pro."):
                        offenders.append(str(path.relative_to(root)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "lac_pro" or module.startswith("lac_pro."):
                    offenders.append(str(path.relative_to(root)))
    return _result(
        "open_core_boundary",
        not offenders,
        detail="model-hub does not import lac_pro" if not offenders else "model-hub imports lac_pro",
        data={"offenders": sorted(set(offenders))},
    )


def check_lac_pro_remote(args: argparse.Namespace) -> dict[str, Any]:
    pro_root = Path(args.lac_pro_root)
    if not pro_root.exists():
        return _result(
            "lac_pro_remote_guard",
            bool(args.allow_missing_lac_pro),
            lane="guards",
            detail="lac-pro repo not found",
            data={"path": str(pro_root)},
        )
    proc = subprocess.run(
        ["git", "remote", "-v"],
        cwd=str(pro_root),
        capture_output=True,
        text=True,
        timeout=args.timeout,
        check=False,
    )
    remote_output = (proc.stdout or "").strip()
    ok = proc.returncode == 0 and not remote_output
    return _result(
        "lac_pro_remote_guard",
        ok,
        lane="guards",
        detail="lac-pro has no git remote" if ok else "lac-pro must stay local-only with no git remote",
        data={"path": str(pro_root), "remote_lines": len(remote_output.splitlines()) if remote_output else 0},
    )


def _post_license(url: str, key: str, timeout: int) -> tuple[int, bytes, list[tuple[str, str]]]:
    body = json.dumps({"license_key": key}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "LAC-Pro-Commerce-Readiness/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.getcode(), response.read(), list(response.headers.items())
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read()
        except Exception:  # noqa: BLE001
            data = b""
        return exc.code, data, list(exc.headers.items()) if exc.headers else []


def _header_values(headers: object, target_name: str) -> list[object] | None:
    """Preserve repeated headers; return None for malformed metadata."""
    if headers is None:
        return []
    try:
        items = headers.items() if hasattr(headers, "items") else headers
        pairs = list(items)
    except Exception:  # noqa: BLE001 - untrusted transport metadata
        return None
    values: list[object] = []
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            return None
        name, value = pair
        if str(name).lower() == target_name.lower():
            values.append(value)
    return values


def _single_header_value(headers: object, target_name: str) -> str | None:
    values = _header_values(headers, target_name)
    if values is None or len(values) != 1 or not isinstance(values[0], str):
        return None
    return values[0]


def _deployment_commit_state(headers: object, expected_commit: str) -> str:
    if not expected_commit:
        return "not_required"
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit) is None:
        return "expected_invalid"
    values = _header_values(headers, "x-lac-deployment-commit")
    if values is None or len(values) != 1 or not isinstance(values[0], str):
        return "missing_or_malformed"
    return "verified" if hmac.compare_digest(values[0], expected_commit) else "mismatch"


def check_invalid_key(args: argparse.Namespace) -> dict[str, Any]:
    url, source = _configured_gate_url(args)
    if _is_placeholder(url):
        return _result("invalid_key_gate", False, detail="cannot smoke invalid key without a real gate URL")
    start = time.perf_counter()
    try:
        status, body, headers = _post_license(str(url), DEFAULT_INVALID_KEY, args.timeout)
    except Exception as exc:  # noqa: BLE001
        return _result("invalid_key_gate", False, detail=f"gate request failed: {exc.__class__.__name__}")
    deployment = _deployment_commit_state(
        headers, str(getattr(args, "expected_deployment_commit", "") or "")
    )
    ok = status in {401, 403} and deployment in {"not_required", "verified"}
    return _result(
        "invalid_key_gate",
        ok,
        detail="invalid key was rejected" if ok else "invalid key was not rejected cleanly",
        data={
            "gate_source": source,
            "status": status,
            "bytes": len(body),
            "content_type": (
                content_types[0]
                if (content_types := _header_values(headers, "content-type"))
                and len(content_types) == 1
                and isinstance(content_types[0], str)
                else None
            ),
            "deployment_commit": deployment,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
        },
    )


def _artifact_integrity(body: bytes, headers: object) -> str:
    """Return a value-free integrity state for the live artifact response."""
    values = _header_values(headers, "x-lac-artifact-sha256")
    if values is None:
        return "malformed"
    if not values:
        return "missing"
    if (
        len(values) != 1
        or not isinstance(values[0], str)
        or re.fullmatch(r"[0-9A-Fa-f]{64}", values[0]) is None
    ):
        return "malformed"
    actual = hashlib.sha256(body).hexdigest()
    return "verified" if hmac.compare_digest(values[0].lower(), actual) else "mismatch"


def _configured_artifact_filename(args: argparse.Namespace) -> str | None:
    try:
        data = tomllib.loads(_read_text(Path(args.worker_config)))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    vars_table = data.get("vars") if isinstance(data, dict) else None
    if not isinstance(vars_table, dict):
        return None
    value = vars_table.get("ARTIFACT_FILENAME")
    return value if isinstance(value, str) and value else None


def _content_disposition_state(
    headers: object, expected_filename: str | None
) -> str:
    values = _header_values(headers, "content-disposition")
    if values is None:
        return "malformed"
    if not values:
        return "missing"
    if expected_filename is None:
        return "config_unavailable"
    if len(values) != 1 or not isinstance(values[0], str):
        return "malformed"
    expected = f'attachment; filename="{expected_filename}"'
    # This is public filename metadata, not a secret; ordinary comparison also
    # handles untrusted non-ASCII header values without compare_digest raising.
    return "verified" if values[0] == expected else "mismatch"


def check_valid_key_artifact(args: argparse.Namespace) -> dict[str, Any]:
    url, source = _configured_gate_url(args)
    key = os.environ.get(args.valid_key_env)
    if not key:
        detail = (
            f"{args.valid_key_env} is not set; valid-key smoke skipped"
            if args.skip_valid_key
            else f"{args.valid_key_env} is not set"
        )
        return _result(
            "valid_key_artifact",
            bool(args.skip_valid_key),
            detail=detail,
            data={"env": args.valid_key_env, "skipped": bool(args.skip_valid_key)},
        )
    if _is_placeholder(url):
        return _result("valid_key_artifact", False, detail="cannot smoke valid key without a real gate URL")
    start = time.perf_counter()
    try:
        status, body, headers = _post_license(str(url), key, args.timeout)
    except Exception as exc:  # noqa: BLE001
        return _result("valid_key_artifact", False, detail=f"gate request failed: {exc.__class__.__name__}")
    integrity = _artifact_integrity(body, headers)
    content_disposition = _content_disposition_state(
        headers,
        _configured_artifact_filename(args),
    )
    deployment = _deployment_commit_state(
        headers, str(getattr(args, "expected_deployment_commit", "") or "")
    )
    ok = (
        status == 200
        and len(body) >= args.min_artifact_bytes
        and integrity == "verified"
        and content_disposition == "verified"
        and deployment in {"not_required", "verified"}
    )
    return _result(
        "valid_key_artifact",
        ok,
        detail=(
            "valid key returned an integrity-verified artifact"
            if ok
            else "valid key did not return a usable integrity-verified artifact"
        ),
        data={
            "gate_source": source,
            "status": status,
            "bytes": len(body),
            "min_artifact_bytes": args.min_artifact_bytes,
            "content_type": _single_header_value(headers, "content-type"),
            "integrity": integrity,
            "content_disposition": content_disposition,
            "deployment_commit": deployment,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
        },
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    rows = [
        check_gate_url(args),
        check_worker_config(args),
        check_open_core_boundary(args),
        check_lac_pro_remote(args),
    ]
    if args.live_gate:
        rows.append(check_invalid_key(args))
        rows.append(check_valid_key_artifact(args))
    failed = [row for row in rows if not row["ok"]]
    return {
        "ok": not failed,
        "live_gate": bool(args.live_gate),
        "failed": failed,
        "checks": rows,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only LAC Pro commerce readiness verifier.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--lac-pro-root", type=Path, default=ROOT.parent / "lac-pro")
    parser.add_argument("--worker-config", type=Path, default=ROOT / "worker" / "wrangler.toml")
    parser.add_argument("--worker-env", default="", help="Validate one named Wrangler environment instead of the top-level table.")
    parser.add_argument("--allow-worker-placeholders", action="store_true", help="Validate public config shape while allowing private deployment placeholders.")
    parser.add_argument("--require-rate-limiter", action="store_true", help="Require the in-code abuse rate-limiter binding in top-level config too.")
    parser.add_argument("--require-receipt-signing", action="store_true", help="Require the public Ed25519 receipt signing-key identifier.")
    parser.add_argument("--gate-url", default="", help="Override the Pro gate URL for this check.")
    parser.add_argument("--require-baked-gate", action="store_true", help="Require backend/pro_install.py to contain the approved production gate URL.")
    parser.add_argument("--live-gate", action="store_true", help="Perform read-only HTTP smokes against the configured gate.")
    parser.add_argument("--valid-key-env", default="LAC_PRO_TEST_KEY", help="Environment variable holding a valid test license key; value is never printed.")
    parser.add_argument("--expected-deployment-commit", default="", help="Require the gate response to identify this exact public commit.")
    parser.add_argument("--skip-valid-key", action="store_true", help="Allow live-gate mode to skip valid-key artifact proof when no test key is present.")
    parser.add_argument("--allow-missing-lac-pro", action="store_true")
    parser.add_argument("--min-artifact-bytes", type=int, default=100_000)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
