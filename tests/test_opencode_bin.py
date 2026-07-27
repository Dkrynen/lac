from pathlib import Path
import subprocess
import pytest
from backend.agent_launch import opencode_bin
from backend.agent_launch.opencode_bin import OpenCodeNotFound, resolve_opencode_binary

EXPECTED_OPENCODE_VERSION = "1.18.7"


def test_resolves_when_on_path(monkeypatch):
    monkeypatch.setattr("backend.agent_launch.opencode_bin.shutil.which",
                        lambda name: r"C:\tools\opencode.exe")
    monkeypatch.setattr(
        opencode_bin,
        "_probe_version",
        lambda binary: EXPECTED_OPENCODE_VERSION,
        raising=False,
    )
    assert resolve_opencode_binary() == Path(r"C:\tools\opencode.exe")


def test_raises_with_install_guidance_when_absent(monkeypatch):
    monkeypatch.setattr("backend.agent_launch.opencode_bin.shutil.which",
                        lambda name: None)
    with pytest.raises(OpenCodeNotFound) as exc:
        resolve_opencode_binary()
    assert "opencode" in str(exc.value).lower()
    assert "install" in str(exc.value).lower()
    assert f"opencode-ai@{EXPECTED_OPENCODE_VERSION}" in str(exc.value)


def test_rejects_untested_opencode_version(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which",
        lambda name: r"C:\tools\opencode.exe",
    )
    monkeypatch.setattr(
        opencode_bin, "_probe_version", lambda binary: "1.19.0", raising=False
    )

    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()

    message = str(exc.value)
    assert "1.19.0" in message
    assert EXPECTED_OPENCODE_VERSION in message


def test_rejects_opencode_when_version_probe_fails(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which",
        lambda name: r"C:\tools\opencode.exe",
    )
    def failed_probe(binary):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(
        opencode_bin, "_probe_version", failed_probe, raising=False
    )

    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()

    assert "probe failed" in str(exc.value)


def test_translates_version_probe_timeout(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which",
        lambda name: r"C:\tools\opencode.exe",
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=10)

    monkeypatch.setattr(opencode_bin.proc, "run", timeout)

    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()

    assert "verify OpenCode" in str(exc.value)
