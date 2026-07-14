from pathlib import Path
import pytest
from backend.agent_launch.opencode_bin import resolve_opencode_binary, OpenCodeNotFound


def test_resolves_when_on_path(monkeypatch):
    monkeypatch.setattr("backend.agent_launch.opencode_bin.shutil.which",
                        lambda name: r"C:\tools\opencode.exe")
    assert resolve_opencode_binary() == Path(r"C:\tools\opencode.exe")


def test_raises_with_install_guidance_when_absent(monkeypatch):
    monkeypatch.setattr("backend.agent_launch.opencode_bin.shutil.which",
                        lambda name: None)
    with pytest.raises(OpenCodeNotFound) as exc:
        resolve_opencode_binary()
    assert "opencode" in str(exc.value).lower()
    assert "install" in str(exc.value).lower()
