#!/usr/bin/env python3
"""Fail-closed local integration audit for LAC, Local Pro, and LAC Cloud."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

CANONICAL_VECTOR_SHA256 = "36c5060b3e429fa8c52271004effcfa6eca4e7b4da0a9e4c1661786ed3ea29a7"
_CLOUD_EXECUTION_GATES = {
    "provider_broker",
    "provider_metering",
    "infrastructure_metering",
    "hosted_workspace_execution",
}

_INACTIVE_ENTITLEMENT = {
    "state": "inactive",
    "plan": None,
    "expires_human": None,
    "checked": None,
}


class AuditError(RuntimeError):
    pass


def canonical_vector_digest(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"Invalid contract vector: {path}") from exc
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_root(label: str, root: Path, marker: str) -> Path:
    resolved = root.resolve(strict=False)
    if not (resolved / marker).is_file():
        raise AuditError(f"{label} root is missing or invalid: {resolved}")
    return resolved


def enabled_cloud_execution_gates(capabilities: dict) -> list[str]:
    return sorted(key for key in _CLOUD_EXECUTION_GATES if capabilities.get(key) is not False)


def _probe_local_pro(*, host_root: Path, pro_root: Path) -> dict:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "lac_pro.unified_probe",
                "--host-root",
                str(host_root),
            ],
            cwd=pro_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuditError("The Local Pro host-contract probe could not execute") from exc
    if completed.returncode != 0:
        raise AuditError("The Local Pro host-contract probe failed closed")
    try:
        result = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuditError("The Local Pro host-contract probe returned invalid JSON") from exc
    if (
        not isinstance(result, dict)
        or set(result) != {
            "state", "product_id", "plugin_version", "host_api_version", "entitlement"
        }
        or result["state"] != "ready"
        or result["product_id"] != "local_pro"
        or not isinstance(result["plugin_version"], str)
        or not result["plugin_version"]
        or not isinstance(result["host_api_version"], int)
        or isinstance(result["host_api_version"], bool)
        or result["host_api_version"] < 1
        or result["entitlement"] != _INACTIVE_ENTITLEMENT
    ):
        raise AuditError("The real Local Pro plugin did not satisfy the host discovery contract")
    return result


def audit_unified_product(*, host_root: Path, pro_root: Path, cloud_root: Path) -> dict:
    host_root = _require_root("LAC host", host_root, "backend/plugins.py")
    pro_root = _require_root("Local Pro", pro_root, "lac_pro/unified_probe.py")
    cloud_root = _require_root("LAC Cloud", cloud_root, "config/product-readiness.v1.json")

    host_vector = host_root / "tests" / "fixtures" / "public-desktop-bootstrap.v1.json"
    cloud_vector = (
        cloud_root
        / "packages"
        / "contracts"
        / "test-vectors"
        / "public-desktop-bootstrap.v1.json"
    )
    for label, vector in (("desktop", host_vector), ("cloud", cloud_vector)):
        digest = canonical_vector_digest(vector)
        if digest != CANONICAL_VECTOR_SHA256:
            raise AuditError(f"{label} bootstrap contract drifted: {digest}")

    pro_state = _probe_local_pro(host_root=host_root, pro_root=pro_root)

    try:
        readiness = json.loads(
            (cloud_root / "config" / "product-readiness.v1.json").read_text(encoding="utf-8")
        )
        capabilities = readiness["capabilities"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AuditError("The LAC Cloud product-readiness manifest is invalid") from exc
    enabled_execution = enabled_cloud_execution_gates(capabilities)
    if enabled_execution:
        raise AuditError(f"Cloud execution gates are not fail-closed: {', '.join(enabled_execution)}")

    return {
        "ready": True,
        "execution_default": "local",
        "local_pro": {
            "state": "ready",
            "plugin_version": pro_state["plugin_version"],
            "host_api_version": pro_state["host_api_version"],
        },
        "cloud": {
            "contract_sha256": CANONICAL_VECTOR_SHA256,
            "execution_available": False,
            "readiness": readiness.get("status"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pro-root", required=True, type=Path)
    parser.add_argument("--cloud-root", required=True, type=Path)
    parser.add_argument(
        "--host-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)
    try:
        result = audit_unified_product(
            host_root=args.host_root,
            pro_root=args.pro_root,
            cloud_root=args.cloud_root,
        )
    except AuditError as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
