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
