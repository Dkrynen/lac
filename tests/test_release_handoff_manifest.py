from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "release_handoff_manifest.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")
SOURCE_COMMIT = "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret -- public deterministic test commit


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or PWSH is None,
    reason="release handoff helper is exercised on PowerShell for Windows",
)


def _ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(script: str, workspace: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GITHUB_WORKSPACE"] = str(workspace)
    return subprocess.run(
        [PWSH or "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=workspace,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_write_returns_digest_without_terminating_caller_and_verify_rechecks_it(
    tmp_path: Path,
):
    workspace = tmp_path / "repo"
    payload = workspace / ".handoff" / "unsigned-app" / "payload"
    payload.mkdir(parents=True)
    (payload / "artifact.bin").write_bytes(b"manifest-bound artifact")
    manifest = payload.parent / "manifest.json"

    write = _run(
        f"""
        $hash = & {_ps_literal(HELPER)} `
          -Mode Write `
          -RootPath {_ps_literal(payload)} `
          -ManifestPath {_ps_literal(manifest)} `
          -Stage 'unsigned-app' `
          -SourceCommit '{SOURCE_COMMIT}' `
          -Tag 'v2.7.0' `
          -Version '2.7.0'
        Write-Output "CALLER_CONTINUED:$hash"
        """,
        workspace,
    )

    assert write.returncode == 0, write.stderr
    marker = next(line for line in write.stdout.splitlines() if line.startswith("CALLER_CONTINUED:"))
    digest = marker.split(":", 1)[1]
    assert len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)

    verify = _run(
        f"""
        & {_ps_literal(HELPER)} `
          -Mode Verify `
          -RootPath {_ps_literal(payload)} `
          -ManifestPath {_ps_literal(manifest)} `
          -Stage 'unsigned-app' `
          -SourceCommit '{SOURCE_COMMIT}' `
          -Tag 'v2.7.0' `
          -Version '2.7.0' `
          -ExpectedManifestSha256 '{digest}'
        """,
        workspace,
    )

    assert verify.returncode == 0, verify.stderr

    (payload / "artifact.bin").write_bytes(b"tampered after manifest creation")
    tampered = _run(
        f"""
        & {_ps_literal(HELPER)} `
          -Mode Verify `
          -RootPath {_ps_literal(payload)} `
          -ManifestPath {_ps_literal(manifest)} `
          -Stage 'unsigned-app' `
          -SourceCommit '{SOURCE_COMMIT}' `
          -Tag 'v2.7.0' `
          -Version '2.7.0' `
          -ExpectedManifestSha256 '{digest}'
        """,
        workspace,
    )
    assert tampered.returncode != 0
    assert "file-by-file SHA-256 verification" in (tampered.stdout + tampered.stderr)


def test_rejects_reparse_point_in_handoff_ancestor_chain(tmp_path: Path):
    workspace = tmp_path / "repo"
    handoff = workspace / ".handoff"
    handoff.mkdir(parents=True)
    target = tmp_path / "junction-target"
    payload = target / "payload"
    payload.mkdir(parents=True)
    (payload / "artifact.bin").write_bytes(b"do not follow junctions")
    stage = handoff / "unsigned-app"

    create_junction = _run(
        f"New-Item -ItemType Junction -Path {_ps_literal(stage)} -Target {_ps_literal(target)} | Out-Null",
        workspace,
    )
    assert create_junction.returncode == 0, create_junction.stderr

    result = _run(
        f"""
        & {_ps_literal(HELPER)} `
          -Mode Write `
          -RootPath {_ps_literal(stage / 'payload')} `
          -ManifestPath {_ps_literal(stage / 'manifest.json')} `
          -Stage 'unsigned-app' `
          -SourceCommit '{SOURCE_COMMIT}' `
          -Tag 'v2.7.0' `
          -Version '2.7.0'
        """,
        workspace,
    )

    assert result.returncode != 0
    assert "reparse-point ancestor" in (result.stdout + result.stderr)
