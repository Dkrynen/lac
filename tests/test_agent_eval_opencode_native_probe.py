from __future__ import annotations

import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("msvcrt", reason="Windows-only eval infrastructure")

from backend.agent_eval.http_observer import (
    LoopbackRecordingProxy,
    attach_observed_request,
)

from backend.agent_eval.identity import _probe_version, _wrapper_target, file_identity

from backend.agent_eval.opencode import run_stock

from backend.agent_eval.schedule import GenerationSettings, TrialSpec

from backend.agent_eval.task import EvalScorer, EvalTask

from backend.agent_launch.opencode_bin import SUPPORTED_OPENCODE_VERSION

def _task(workspace):
    return EvalTask(
        schema_version=1,
        id="native-opencode-probe",
        prompt="Answer only with ZeroDivisionError.",
        fixture_root=workspace,
        timeout_seconds=30,
        scorer=EvalScorer("exact_text", "ZeroDivisionError"),
    )

@pytest.mark.skipif(os.name != "nt", reason="pinned native target is Windows")
def test_provenance_proven_opencode_1184_reaches_real_http_observer(
    tmp_path,
    monkeypatch,
):
    isolated = tmp_path / "isolated"
    for name in ("home", "config", "data", "cache", "appdata", "localappdata"):
        (isolated / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(isolated / "home"))
    monkeypatch.setenv("USERPROFILE", str(isolated / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(isolated / "cache"))
    monkeypatch.setenv("APPDATA", str(isolated / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(isolated / "localappdata"))

    wrapper_name = shutil.which("opencode.cmd")
    if wrapper_name is None:
        pytest.skip("required local OpenCode 1.18.9 wrapper is unavailable")
    wrapper = file_identity(wrapper_name, version=None)
    native_path = _wrapper_target(wrapper.path)
    native = file_identity(
        native_path,
        version=None,
        version_fn=_probe_version,
    )
    assert native.version == SUPPORTED_OPENCODE_VERSION == "1.18.9"

    requests = []

    class FakeOllama(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            raw_body = self.rfile.read(length)
            requests.append((self.path, raw_body))
            first = {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-oss:20b",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": "ZeroDivisionError",
                        },
                        "finish_reason": None,
                    }
                ],
            }
            final = {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-oss:20b",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
            payload = (
                f"data: {json.dumps(first)}\n\n"
                f"data: {json.dumps(final)}\n\n"
                "data: [DONE]\n\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *_args):
            return None

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    upstream_thread = threading.Thread(
        target=upstream.serve_forever,
        daemon=True,
    )
    upstream_thread.start()
    proxy = LoopbackRecordingProxy.open(
        f"http://127.0.0.1:{upstream.server_address[1]}"
    )
    generation = GenerationSettings(1.0, 20260726, 128)
    trial = TrialSpec(1, 1209934845, ("raw", "stock", "lac"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        proxy.begin_capture("001-stock", "/v1/chat/completions")
        result = run_stock(
            _task(workspace),
            "gpt-oss:20b",
            proxy.endpoint,
            workspace,
            generation=generation,
            trial=trial,
            resolve_bin_fn=lambda: native.path,
        )
        try:
            observation = proxy.finish_capture("001-stock")
        except Exception as exc:
                pytest.fail(
                    f"native OpenCode produced no observable request: "
                    f"{exc}; result={result!r}; requests={requests!r}"
                )
        result = attach_observed_request(result, observation, trial)
    finally:
        proxy.close()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert result.completed is True
    assert result.response == "ZeroDivisionError"
    assert result.request_metadata["source"] == "loopback_recording_proxy"
    assert result.request_metadata["capture_token"] == "001-stock"
    assert result.request_metadata["temperature"] == 1.0, (
        observation.json_body()
    )
    assert result.request_metadata["seed"] == 1209934845
    assert result.request_metadata["max_output_tokens"] == 128
    assert result.request_metadata["raw_body"] == observation.raw_body.decode(
        "utf-8"
    )
    assert requests == [
        ("/v1/chat/completions", observation.raw_body),
    ]
