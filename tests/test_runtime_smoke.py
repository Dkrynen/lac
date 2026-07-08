from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "runtime_smoke.py"


def _load_runtime_smoke():
    spec = importlib.util.spec_from_file_location("runtime_smoke", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**overrides):
    defaults = {
        "app_url": "http://lac.local",
        "model": "qwen2.5:0.5b",
        "prompt": "say ok",
        "system_prompt": "qa",
        "workspace": "",
        "session_name": "QA",
        "timeout": 30,
        "skip_warm": False,
        "delete_session": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_runtime_smoke_build_report_success(monkeypatch):
    smoke = _load_runtime_smoke()
    calls = []

    def fake_request_json(base_url, path, payload=None, method=None, timeout=60):
        calls.append((path, payload, method))
        if path == "/api/system/version":
            return 200, {"version": "2.6.4", "app_name": "LAC"}
        if path == "/api/sessions" and method is None:
            return 201, {"id": "sid"}
        if path == "/api/ollama/warm":
            return 200, {"state": "warm", "model": payload["model"]}
        if path == "/api/sessions/sid" and method == "PUT":
            return 200, {"success": True}
        if path == "/api/sessions/sid" and method is None:
            return 200, {"messages": [{"role": "user"}, {"role": "assistant"}]}
        raise AssertionError((path, payload, method))

    monkeypatch.setattr(smoke, "request_json", fake_request_json)
    monkeypatch.setattr(
        smoke,
        "stream_chat",
        lambda *a, **kw: {
            "status": 200,
            "content_type": "text/event-stream",
            "first_chunk_ms": 10.0,
            "total_wall_ms": 20.0,
            "eval_count": 3,
            "tokens_per_second": 100.0,
            "response": "ok",
            "errors": [],
        },
    )

    report = smoke.build_report(_args())

    assert report["ok"] is True
    assert report["session"]["id"] == "sid"
    assert report["session"]["saved"] is True
    assert report["chat"]["response"] == "ok"
    assert any(call[0] == "/api/ollama/warm" for call in calls)


def test_runtime_smoke_reports_chat_errors(monkeypatch):
    smoke = _load_runtime_smoke()

    def fake_request_json(base_url, path, payload=None, method=None, timeout=60):
        if path == "/api/system/version":
            return 200, {"version": "2.6.4"}
        if path == "/api/sessions" and method is None:
            return 201, {"id": "sid"}
        if path == "/api/sessions/sid" and method == "PUT":
            return 200, {"success": True}
        if path == "/api/sessions/sid" and method is None:
            return 200, {"messages": []}
        raise AssertionError((path, payload, method))

    monkeypatch.setattr(smoke, "request_json", fake_request_json)
    monkeypatch.setattr(
        smoke,
        "stream_chat",
        lambda *a, **kw: {"status": 200, "response": "", "errors": ["boom"]},
    )

    report = smoke.build_report(_args(skip_warm=True))

    assert report["ok"] is False
    assert report["chat"]["errors"] == ["boom"]
    assert report["warm"] is None
