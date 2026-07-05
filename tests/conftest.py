from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    import backend.cookbook.config as cfg
    import backend.cookbook.persistence as pers
    import backend.cookbook.downloads as downloads

    new_cfg_dir = home / ".model-hub"
    monkeypatch.setattr(cfg, "CONFIG_DIR", new_cfg_dir)
    monkeypatch.setattr(cfg, "CONFIG_FILE", new_cfg_dir / "config.json")
    monkeypatch.setattr(downloads, "CONFIG_DIR", new_cfg_dir)
    new_cfg_dir.mkdir(parents=True, exist_ok=True)

    new_db_dir = new_cfg_dir
    new_db_path = new_cfg_dir / "cookbook.db"
    monkeypatch.setattr(pers, "DB_DIR", new_db_dir)
    monkeypatch.setattr(pers, "DB_PATH", new_db_path)
    monkeypatch.setattr(pers, "_MIGRATED", False)

    return home


@pytest.fixture
def mock_provider():
    from backend.provider.base import ChatDelta, LLMProvider, ModelInfo

    class MockProvider(LLMProvider):
        type = "mock"
        display_name = "Mock"

        def __init__(self):
            self._script: list = []
            self._calls: list[dict] = []

        @property
        def name(self) -> str:
            return "mock"

        def set_script(self, deltas: list):
            self._script = list(deltas)

        def list_models(self):
            return [ModelInfo(name="mock:1b"), ModelInfo(name="mock:7b")]

        def chat(self, model, messages, stream=True, tools=None, system=None, **kwargs):
            self._calls.append({"model": model, "messages": list(messages), "tools": tools})
            script = self._script
            if not script:
                script = [ChatDelta(content="mock reply", done=True)]
            for d in script:
                yield d

    return MockProvider()


@pytest.fixture
def tool_registry():
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS

    return {"handlers": dict(TOOL_HANDLERS), "schemas": list(TOOL_SCHEMAS)}


@pytest.fixture
def flask_app():
    from backend.api import app

    app.config.update(TESTING=True)
    return app


@pytest.fixture
def ollama_available():
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return True
    except Exception:
        return False
