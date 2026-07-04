"""API plugin mounting + /api/plugins listing."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import LoadedPlugin


def test_api_plugins_endpoint_lists(monkeypatch, flask_app):
    plug = SimpleNamespace(name="fake", version="9.9")
    monkeypatch.setattr(plugins_mod, "discover", lambda: [
        LoadedPlugin("fake", "9.9", plug),
        LoadedPlugin("broken", "?", None, error="nope"),
    ])
    client = flask_app.test_client()
    r = client.get("/api/plugins")
    assert r.status_code == 200
    data = r.get_json()
    assert {p["name"] for p in data} == {"fake", "broken"}
    assert next(p for p in data if p["name"] == "broken")["ok"] is False


def test_register_api_mounts_routes(monkeypatch, flask_app):
    def register_api(app):
        @app.route("/api/pro/ping")
        def _pro_ping():
            return {"pong": True}

    plug = SimpleNamespace(name="fake", version="9.9", register_api=register_api)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("fake", "9.9", plug)])

    # flask_app is a shared module-level app; a prior test in this session may
    # have already dispatched a request against it, which locks route
    # registration. In production _mount_plugins(app) runs once at import
    # time, before any request is served — replicate that ordering here.
    monkeypatch.setattr(flask_app, "_got_first_request", False)

    from backend.api import _mount_plugins
    _mount_plugins(flask_app)
    client = flask_app.test_client()
    assert client.get("/api/pro/ping").get_json() == {"pong": True}


def test_broken_register_api_is_isolated(monkeypatch, flask_app):
    def register_api(app):
        raise RuntimeError("boom")

    plug = SimpleNamespace(name="bad", version="0.0", register_api=register_api)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("bad", "0.0", plug)])
    from backend.api import _mount_plugins
    _mount_plugins(flask_app)  # must not raise


def test_notify_model_installed_calls_hook(monkeypatch):
    calls = []
    plug = SimpleNamespace(name="fake", version="1.0", on_model_installed=lambda m: calls.append(m))
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("fake", "1.0", plug)])

    from backend.api import _notify_model_installed
    _notify_model_installed("m:1b")
    assert calls == ["m:1b"]


def test_notify_model_installed_isolates_raising_hook(monkeypatch, capsys):
    def boom(model_name):
        raise RuntimeError("boom")

    plug = SimpleNamespace(name="bad", version="0.0", on_model_installed=boom)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("bad", "0.0", plug)])

    from backend.api import _notify_model_installed
    _notify_model_installed("m:1b")  # must not raise
    assert "on_model_installed failed" in capsys.readouterr().out


def test_notify_model_installed_async_runs_in_background_thread(monkeypatch):
    import threading
    calls = []
    done = threading.Event()

    def hook(model_name):
        calls.append(model_name)
        done.set()

    plug = SimpleNamespace(name="fake", version="1.0", on_model_installed=hook)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("fake", "1.0", plug)])

    from backend.api import _notify_model_installed_async
    _notify_model_installed_async("m:1b")
    assert done.wait(timeout=2)
    assert calls == ["m:1b"]


def test_ollama_pull_fires_hook_on_success(monkeypatch, flask_app):
    import json as _json
    import urllib.request
    from backend import api as api_mod

    lines = [
        _json.dumps({"status": "pulling manifest"}).encode(),
        _json.dumps({"status": "success"}).encode(),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: iter(lines))

    calls = []
    monkeypatch.setattr(api_mod, "_notify_model_installed_async", lambda m: calls.append(m))

    client = flask_app.test_client()
    r = client.post("/api/ollama/pull", json={"model": "m:1b"})
    assert r.status_code == 200
    _ = r.data  # fully consume the streamed SSE response
    assert calls == ["m:1b"]
