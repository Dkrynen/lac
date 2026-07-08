from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "live_import_stress.py"


def _load_stress():
    spec = importlib.util.spec_from_file_location("live_import_stress", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**overrides):
    defaults = {
        "app_url": "http://lac.local",
        "repo_id": "org/model-GGUF",
        "quant": "Q4_K_M",
        "filename": "model-Q4_K_M.gguf",
        "target": "",
        "delete_from_model": "qwen2.5:0.5b",
        "delete_model": "",
        "skip_delete_check": False,
        "timeout": 30,
        "import_timeout": 60,
        "poll_interval": 0.01,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_model_names_accepts_list_and_wrapped_shapes():
    stress = _load_stress()

    assert stress.model_names([{"name": "a"}, {"name": "b"}]) == {"a", "b"}
    assert stress.model_names({"models": [{"name": "c"}]}) == {"c"}
    assert stress.model_names({"value": [{"name": "d"}]}) == {"d"}
    assert stress.model_names({"unexpected": []}) == set()


def test_live_import_stress_build_report_success(monkeypatch):
    stress = _load_stress()

    def fake_request_json(base_url, path, payload=None, method=None, timeout=60):
        if path == "/api/system/version":
            return 200, {"version": "2.6.4", "app_name": "LAC"}
        if path == "/api/plugins":
            return 200, [{"name": "pro", "ok": True}]
        if path == "/api/ollama/models":
            return 200, [{"name": "org-model-gguf:latest"}]
        if path.startswith("/api/model/install-preflight?"):
            return 200, {"state": "ok", "kind": "hf_gguf"}
        if path == "/api/pro/import-model":
            return 200, {"accepted": True}
        if path == "/api/ollama/warm":
            return 200, {"state": "warm", "model": payload["model"]}
        raise AssertionError((path, payload, method))

    monkeypatch.setattr(stress, "request_json", fake_request_json)
    monkeypatch.setattr(stress, "wait_for_import", lambda args: {
        "final": {"state": "done", "model_name": "org-model-gguf:latest", "quant": "Q4_K_M"},
        "events": [],
        "event_count": 0,
    })
    monkeypatch.setattr(stress, "stream_chat", lambda *a, **kw: {
        "status": 200,
        "response": "ok",
        "errors": [],
    })
    monkeypatch.setattr(stress, "run_delete_check", lambda args: {"ok": True})

    report = stress.build_report(_args())

    assert report["ok"] is True
    assert report["import"]["model_present_after"] is True
    assert report["delete_check"]["ok"] is True


def test_live_import_stress_blocks_failed_import(monkeypatch):
    stress = _load_stress()

    def fake_request_json(base_url, path, payload=None, method=None, timeout=60):
        if path == "/api/system/version":
            return 200, {"version": "2.6.4"}
        if path == "/api/plugins":
            return 200, [{"name": "pro", "ok": True}]
        if path == "/api/ollama/models":
            return 200, []
        if path.startswith("/api/model/install-preflight?"):
            return 200, {"state": "ok"}
        if path == "/api/pro/import-model":
            return 200, {"accepted": True}
        raise AssertionError((path, payload, method))

    monkeypatch.setattr(stress, "request_json", fake_request_json)
    monkeypatch.setattr(stress, "wait_for_import", lambda args: {
        "final": {"state": "failed", "error_type": "conversion_failed", "message": "boom"},
        "events": [],
        "event_count": 0,
    })
    monkeypatch.setattr(stress, "run_delete_check", lambda args: {"ok": True})

    report = stress.build_report(_args())

    assert report["ok"] is False
    assert report["import"]["final"]["error_type"] == "conversion_failed"
