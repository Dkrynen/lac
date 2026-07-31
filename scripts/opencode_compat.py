"""OpenCode compatibility probe.

Verifies that the installed OpenCode still exposes the surface LAC wraps:
the pinned version (optional), the ``opencode run`` flags the evidence
pipeline shells out with, and the surface contract over freshly emitted
LAC artifacts. Exit 0 = compatible, 1 = drift. Used by
.github/workflows/opencode-compat.yml (pinned = hard gate, latest = warning).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent_launch import surface_contract
from backend.agent_launch.config_writer import (
    _FAIL_CLOSED_PERMISSIONS,
    build_opencode_config,
    write_agent_commands,
    write_agent_plugin,
    write_agent_profiles,
)

REQUIRED_RUN_FLAGS = ("--format", "--pure", "--auto", "--model", "--dir")
PROBE_MODEL = "gpt-oss:20b-agent"
PROBE_HOST = "http://localhost:11434"
PROBE_PREFIX = [r"C:\Program Files\LAC\lac.exe"]

results: list[dict] = []


def record(check: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": check, "ok": ok, "detail": detail})
    return ok


def probe_version(binary: Path, require: str | None) -> bool:
    try:
        out = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return record("version", False, f"probe failed: {exc}")
    match = re.search(r"\b\d+\.\d+\.\d+\b", out.stdout or "")
    if out.returncode != 0 or not match:
        detail = (out.stderr or out.stdout or "no version").strip()[:200]
        return record("version", False, detail)
    version = match.group(0)
    if require and version != require:
        return record("version", False, f"installed {version}, required {require}")
    return record("version", True, version)


def probe_run_flags(binary: Path) -> bool:
    try:
        out = subprocess.run(
            [str(binary), "run", "--help"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return record("run_flags", False, f"probe failed: {exc}")
    text = (out.stdout or "") + (out.stderr or "")
    missing = [flag for flag in REQUIRED_RUN_FLAGS if flag not in text]
    if missing:
        return record("run_flags", False, f"missing flags: {missing}")
    return record("run_flags", True, "all evidence-pipeline flags present")


def probe_artifacts() -> bool:
    try:
        config = build_opencode_config(
            PROBE_MODEL, PROBE_HOST, permission=_FAIL_CLOSED_PERMISSIONS
        )
        surface_contract.validate_opencode_config(config)
        with tempfile.TemporaryDirectory() as td:
            for path in write_agent_commands(td, pro_available=True, cli_prefix=PROBE_PREFIX):
                surface_contract.validate_command_file(path.read_text(encoding="utf-8"))
            plugin = write_agent_plugin(td, cli_prefix=PROBE_PREFIX)
            surface_contract.validate_plugin_source(plugin.read_text(encoding="utf-8"))
            profiles = write_agent_profiles(td, PROBE_MODEL)
            for path in profiles["written"]:
                surface_contract.validate_agent_profile(path.read_text(encoding="utf-8"))
    except surface_contract.SurfaceViolation as exc:
        return record("artifacts", False, str(exc))
    return record(
        "artifacts", True, "config, commands, plugin, and profiles pass the surface contract"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe the installed OpenCode for LAC compatibility."
    )
    parser.add_argument(
        "--require-version",
        default=None,
        help="Fail unless the installed OpenCode is exactly this version.",
    )
    parser.add_argument(
        "--binary",
        default=None,
        help="OpenCode binary to probe (default: first on PATH).",
    )
    args = parser.parse_args()

    binary = Path(args.binary) if args.binary else shutil.which("opencode")
    if not binary:
        record("binary", False, "opencode not found on PATH")
    else:
        record("binary", True, str(binary))
        probe_version(binary, args.require_version)
        probe_run_flags(binary)
    probe_artifacts()

    for entry in results:
        print(json.dumps(entry))
    return 0 if all(entry["ok"] for entry in results) else 1


if __name__ == "__main__":
    sys.exit(main())
