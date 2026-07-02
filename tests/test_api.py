from __future__ import annotations

import pytest


def test_index_serves_html(flask_app):
    client = flask_app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    assert b"Apt" in r.data or b"apt" in r.data


def test_docs_route(flask_app):
    client = flask_app.test_client()
    for path in ("/docs", "/docs/api", "/docs/guide"):
        r = client.get(path)
        assert r.status_code == 200


def test_system_version(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/system/version")
    assert r.status_code == 200
    data = r.get_json()
    assert data["version"]


def test_system_check_update(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/system/check-update")
    assert r.status_code == 200


def test_scan(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/scan")
    assert r.status_code == 200
    data = r.get_json()
    assert "cpu" in data or "os" in data


def test_workspaces_list(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/workspaces")
    assert r.status_code == 200


def test_sessions_crud(flask_app, isolated_home):
    client = flask_app.test_client()
    r = client.post("/api/sessions", json={"model": "llama3.2:3b"})
    assert r.status_code in (200, 201)
    sid = r.get_json().get("id") or r.get_json().get("session_id")
    if sid:
        r2 = client.get(f"/api/sessions/{sid}")
        assert r2.status_code == 200


def test_ollama_status(flask_app, ollama_available):
    if not ollama_available:
        pytest.skip("Ollama not running")
    client = flask_app.test_client()
    r = client.get("/api/ollama/status")
    assert r.status_code == 200


def test_openapi_endpoint(flask_app):
    client = flask_app.test_client()
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    spec = r.get_json()
    assert spec["openapi"] == "3.1.0"
    assert "/api/system/version" in spec["paths"]


def test_recommend_serializes_speed_source(flask_app, isolated_home):
    """Each recommendation must carry speed_source + speed_band_pct so the
    web UI can tag measured/calibrated/estimated values."""
    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=coding&top_k=3")
    assert r.status_code == 200
    data = r.get_json()
    assert "recommendations" in data and len(data["recommendations"]) > 0
    for rec in data["recommendations"]:
        assert rec["speed_source"] in ("measured", "calibrated", "estimated")
        assert isinstance(rec["speed_band_pct"], (int, float))
        assert rec["speed_band_pct"] > 0  # never a zero-width band


def test_recommend_no_calibration_escape_hatch(flask_app, isolated_home):
    """?no_calibration=1 must still return recs, all tagged 'estimated'."""
    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=coding&top_k=3&no_calibration=1")
    assert r.status_code == 200
    for rec in r.get_json()["recommendations"]:
        assert rec["speed_source"] == "estimated"


def test_benchmark_requires_model(flask_app):
    r = flask_app.test_client().post("/api/benchmark", json={})
    assert r.status_code == 400


def test_benchmark_streams_runs_and_median(flask_app, isolated_home, monkeypatch):
    import json as _json
    import urllib.request

    fake_result = {
        "eval_count": 100,
        "eval_duration": 5_000_000_000,
        "load_duration": 1_000_000_000,
        "prompt_eval_duration": 1_000_000_000,
        "total_duration": 7_000_000_000,
        "response": "ok",
    }

    class _FakeResp:
        def __init__(self, data): self._d = data
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=None):
        return _FakeResp(_json.dumps(fake_result).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = flask_app.test_client()
    r = client.post("/api/benchmark", json={"model": "test:1b", "repeat": 2})
    assert r.status_code == 200

    frames = []
    for line in r.data.decode().split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            frames.append(_json.loads(payload))

    run_frames = [f for f in frames if "done" not in f]
    done = [f for f in frames if f.get("done")]
    assert len(run_frames) == 2
    assert len(done) == 1
    assert run_frames[0]["tokens_per_second"] == 20.0
    assert done[0]["median_tps"] == 20.0
    assert done[0]["logged"] == 2

    from backend.cookbook.benchmark import history
    assert len(history()) == 2
