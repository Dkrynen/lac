from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
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
    ok = not _is_placeholder(url)
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

    vars_table = data.get("vars") if isinstance(data, dict) else {}
    buckets = data.get("r2_buckets") if isinstance(data, dict) else []
    if not isinstance(vars_table, dict):
        vars_table = {}
    if not isinstance(buckets, list):
        buckets = []

    values = {
        "POLAR_ORG_ID": vars_table.get("POLAR_ORG_ID"),
        "ARTIFACT_KEY": vars_table.get("ARTIFACT_KEY"),
        "R2_BUCKET.bucket_name": next(
            (
                bucket.get("bucket_name")
                for bucket in buckets
                if isinstance(bucket, dict) and bucket.get("binding") == "R2_BUCKET"
            ),
            None,
        ),
    }
    missing = [name for name, value in values.items() if not isinstance(value, str) or not value.strip()]
    placeholders = sorted(name for name, value in values.items() if isinstance(value, str) and _is_placeholder(value))
    ok = not placeholders and not missing
    detail = "worker config has concrete Polar/R2 settings" if ok else "worker config is not deploy-ready"
    return _result(
        "worker_config",
        ok,
        detail=detail,
        data={
            "path": str(path),
            "placeholders": placeholders,
            "missing": missing,
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


def _post_license(url: str, key: str, timeout: int) -> tuple[int, bytes, dict[str, str]]:
    body = json.dumps({"license_key": key}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "LAC-Pro-Commerce-Readiness/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.getcode(), response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read()
        except Exception:  # noqa: BLE001
            data = b""
        return exc.code, data, dict(exc.headers.items())


def check_invalid_key(args: argparse.Namespace) -> dict[str, Any]:
    url, source = _configured_gate_url(args)
    if _is_placeholder(url):
        return _result("invalid_key_gate", False, detail="cannot smoke invalid key without a real gate URL")
    start = time.perf_counter()
    try:
        status, body, headers = _post_license(str(url), DEFAULT_INVALID_KEY, args.timeout)
    except Exception as exc:  # noqa: BLE001
        return _result("invalid_key_gate", False, detail=f"gate request failed: {exc.__class__.__name__}")
    ok = status in {401, 403}
    return _result(
        "invalid_key_gate",
        ok,
        detail="invalid key was rejected" if ok else "invalid key was not rejected cleanly",
        data={
            "gate_source": source,
            "status": status,
            "bytes": len(body),
            "content_type": headers.get("content-type") or headers.get("Content-Type"),
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
        },
    )


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
    ok = status == 200 and len(body) >= args.min_artifact_bytes
    return _result(
        "valid_key_artifact",
        ok,
        detail="valid key returned an artifact" if ok else "valid key did not return a usable artifact",
        data={
            "gate_source": source,
            "status": status,
            "bytes": len(body),
            "min_artifact_bytes": args.min_artifact_bytes,
            "content_type": headers.get("content-type") or headers.get("Content-Type"),
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
    parser.add_argument("--gate-url", default="", help="Override the Pro gate URL for this check.")
    parser.add_argument("--require-baked-gate", action="store_true", help="Require backend/pro_install.py to contain the approved production gate URL.")
    parser.add_argument("--live-gate", action="store_true", help="Perform read-only HTTP smokes against the configured gate.")
    parser.add_argument("--valid-key-env", default="LAC_PRO_TEST_KEY", help="Environment variable holding a valid test license key; value is never printed.")
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
