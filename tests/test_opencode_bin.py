from pathlib import Path
import subprocess
import pytest
from backend.agent_launch import opencode_bin
from backend.agent_launch.opencode_bin import (
    OpenCodeNotFound,
    resolve_opencode_binary,
    version_verdict,
)

VERIFIED = "1.18.9"
BINARY = r"C:\tools\opencode.CMD"


def _fake(monkeypatch, version):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which", lambda name: BINARY
    )
    monkeypatch.setattr(
        opencode_bin, "_probe_version", lambda binary: version, raising=False
    )


# --- pure version policy -------------------------------------------------


@pytest.mark.parametrize(
    "installed,status",
    [
        (VERIFIED, "verified"),
        ("1.18.10", "compatible"),
        ("1.18.99", "compatible"),
        ("1.18.8", "incompatible"),
        ("1.17.5", "incompatible"),
        ("1.19.0", "incompatible"),
        ("2.0.0", "incompatible"),
        ("banana", "unparseable"),
    ],
)
def test_version_verdict_policy(installed, status):
    got, _ = version_verdict(installed)
    assert got == status


# --- resolver behavior ----------------------------------------------------


def test_resolves_verified_version_silently(monkeypatch, capsys):
    _fake(monkeypatch, VERIFIED)
    assert resolve_opencode_binary() == Path(BINARY)
    assert capsys.readouterr().out == ""


def test_resolves_newer_patch_with_warning(monkeypatch, capsys):
    _fake(monkeypatch, "1.18.10")
    assert resolve_opencode_binary() == Path(BINARY)
    out = capsys.readouterr().out
    assert "1.18.10" in out
    assert VERIFIED in out


def test_rejects_older_patch_with_override_hint(monkeypatch):
    _fake(monkeypatch, "1.18.8")
    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()
    msg = str(exc.value)
    assert "1.18.8" in msg
    assert VERIFIED in msg
    assert "LAC_OPENCODE_ALLOW_ANY" in msg


def test_rejects_newer_minor_with_override_hint(monkeypatch):
    _fake(monkeypatch, "1.19.0")
    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()
    msg = str(exc.value)
    assert "1.19.0" in msg
    assert VERIFIED in msg
    assert "LAC_OPENCODE_ALLOW_ANY" in msg


def test_override_env_allows_unverified_with_loud_warning(monkeypatch, capsys):
    _fake(monkeypatch, "1.19.0")
    monkeypatch.setenv("LAC_OPENCODE_ALLOW_ANY", "1")
    assert resolve_opencode_binary() == Path(BINARY)
    out = capsys.readouterr().out
    assert "UNVERIFIED" in out
    assert "1.19.0" in out


def test_override_still_rejects_unparseable_version(monkeypatch):
    _fake(monkeypatch, "banana")
    monkeypatch.setenv("LAC_OPENCODE_ALLOW_ANY", "1")
    with pytest.raises(RuntimeError):
        resolve_opencode_binary()


# --- pre-existing guarantees ----------------------------------------------


def test_raises_with_install_guidance_when_absent(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which", lambda name: None
    )
    with pytest.raises(OpenCodeNotFound) as exc:
        resolve_opencode_binary()
    assert "opencode" in str(exc.value).lower()
    assert "install" in str(exc.value).lower()
    assert f"opencode-ai@{VERIFIED}" in str(exc.value)


def test_rejects_opencode_when_version_probe_fails(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which", lambda name: BINARY
    )

    def failed_probe(binary):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(opencode_bin, "_probe_version", failed_probe, raising=False)

    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()

    assert "probe failed" in str(exc.value)


def test_translates_version_probe_timeout(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which", lambda name: BINARY
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=10)

    monkeypatch.setattr(opencode_bin.proc, "run", timeout)

    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()

    assert "verify OpenCode" in str(exc.value)
