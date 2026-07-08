from __future__ import annotations

import json


def test_ollama_pull_web_install_logs_download_history(monkeypatch, flask_app, isolated_home):
    """The web UI's install path (POST /api/ollama/pull) must feed the same
    history.jsonl file the CLI's `lac pull` writes -- otherwise the
    Downloads page is permanently empty for anyone who only ever installs
    through the web UI (confirmed: only cli.py's _log_download ever wrote
    to that file; api.py's install path only fired the plugin hook)."""
    import urllib.request as real_urllib_request
    from backend import api as api_mod

    class FakeResp:
        def __iter__(self):
            lines = [
                json.dumps({"status": "pulling manifest"}).encode(),
                json.dumps({"status": "downloading", "completed": 1_000_000_000, "total": 2_000_000_000}).encode(),
                json.dumps({"status": "success"}).encode(),
            ]
            return iter(l + b"\n" for l in lines)

    monkeypatch.setattr(real_urllib_request, "urlopen", lambda req, timeout=3600: FakeResp())
    monkeypatch.setattr(api_mod, "_notify_model_installed_async", lambda model_name: None)

    client = flask_app.test_client()
    r = client.post("/api/ollama/pull", json={"model": "qwen3:0.6b"})
    assert r.status_code == 200
    # The route streams SSE via stream_with_context; Werkzeug's test client
    # does not eagerly consume a streaming body, so the generator (and its
    # success-chunk log_download() side effect) only runs once the body is
    # actually read -- exactly like a real client reading the SSE stream.
    r.get_data()

    r2 = client.get("/api/config/downloads")
    entries = r2.get_json()
    assert any(
        e["model"] == "qwen3:0.6b" and e["status"] == "completed" and e["size_gb"] > 0
        for e in entries
    )


def test_ollama_pull_status_tracks_completed_pull(monkeypatch, flask_app, isolated_home):
    import urllib.request as real_urllib_request
    from backend import api as api_mod

    with api_mod.PULL_PROGRESS_LOCK:
        api_mod.PULL_PROGRESS.clear()

    class FakeResp:
        def __iter__(self):
            lines = [
                json.dumps({"status": "pulling manifest"}).encode(),
                json.dumps({"status": "downloading", "completed": 1_000, "total": 2_000}).encode(),
                json.dumps({"status": "success"}).encode(),
            ]
            return iter(l + b"\n" for l in lines)

    monkeypatch.setattr(real_urllib_request, "urlopen", lambda req, timeout=3600: FakeResp())
    monkeypatch.setattr(api_mod, "_notify_model_installed_async", lambda model_name: None)

    client = flask_app.test_client()
    r = client.post("/api/ollama/pull", json={"model": "tiny:latest"})
    r.get_data()

    status = client.get("/api/ollama/pull-status?model=tiny:latest").get_json()
    assert status["state"] == "completed"
    assert status["status"] == "success"
    assert status["percent"] == 100
    assert status["total"] == 2000

    all_status = client.get("/api/ollama/pull-status").get_json()
    assert all_status["active"] == 0
    assert any(p["model"] == "tiny:latest" for p in all_status["pulls"])


def test_ollama_pull_status_tracks_failed_pull(monkeypatch, flask_app, isolated_home):
    import urllib.request as real_urllib_request
    from backend import api as api_mod

    with api_mod.PULL_PROGRESS_LOCK:
        api_mod.PULL_PROGRESS.clear()

    def boom(req, timeout=3600):
        raise OSError("network down")

    monkeypatch.setattr(real_urllib_request, "urlopen", boom)

    client = flask_app.test_client()
    r = client.post("/api/ollama/pull", json={"model": "broken:latest"})
    body = r.get_data(as_text=True)

    assert "network down" in body
    status = client.get("/api/ollama/pull-status?model=broken:latest").get_json()
    assert status["state"] == "failed"
    assert status["status"] == "failed"
    assert status["error"] == "network down"

    entries = client.get("/api/config/downloads").get_json()
    assert any(e["model"] == "broken:latest" and e["status"] == "failed" for e in entries)


def test_log_download_and_history_round_trip(isolated_home):
    from backend.cookbook.downloads import log_download, download_history

    log_download("llama3.2:3b", "completed", 1.9)
    history = download_history()
    assert any(e["model"] == "llama3.2:3b" and e["size_gb"] == 1.9 for e in history)


def test_download_history_empty_when_no_log_file(isolated_home):
    from backend.cookbook.downloads import download_history

    assert download_history() == []
