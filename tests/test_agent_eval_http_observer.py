from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.agent_eval.http_observer import (
    HttpObservationError,
    LoopbackRecordingProxy,
    _validated_upstream,
)


@contextmanager
def _upstream(response_body: bytes = b'{"ok":true}'):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "body": body,
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, _format, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_address[1]}",
            requests,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post(endpoint: str, path: str, body: bytes) -> bytes:
    request = urllib.request.Request(
        endpoint + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.read()


def test_localhost_upstream_is_pinned_without_deferred_name_resolution():
    assert _validated_upstream("http://localhost:11434") == (
        "127.0.0.1",
        11434,
    )


def test_proxy_records_raw_request_and_forwards_exact_body_to_fixed_upstream():
    raw_body = (
        b'{"model":"gpt-oss:20b","temperature":1.0,'
        b'"seed":1209934845,"max_tokens":128}'
    )
    with _upstream() as (upstream, forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        try:
            proxy.begin_capture("001-stock", "/v1/chat/completions")

            assert _post(
                proxy.endpoint,
                "/v1/chat/completions",
                raw_body,
            ) == b'{"ok":true}'
            capture = proxy.finish_capture("001-stock")
        finally:
            proxy.close()

    assert capture.token == "001-stock"
    assert capture.method == "POST"
    assert capture.path == "/v1/chat/completions"
    assert capture.raw_body == raw_body
    assert capture.json_body() == {
        "model": "gpt-oss:20b",
        "temperature": 1.0,
        "seed": 1209934845,
        "max_tokens": 128,
    }
    assert forwarded == [
        {
            "method": "POST",
            "path": "/v1/chat/completions",
            "body": raw_body,
        }
    ]


def test_proxy_rejects_missing_duplicate_and_wrong_capture_tokens():
    with _upstream() as (upstream, _forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        try:
            proxy.begin_capture("001-raw", "/api/chat")
            with pytest.raises(HttpObservationError, match="token"):
                proxy.finish_capture("001-stock")
            with pytest.raises(HttpObservationError, match="active"):
                proxy.begin_capture("001-stock", "/v1/chat/completions")
            with pytest.raises(HttpObservationError, match="exactly one"):
                proxy.finish_capture("001-raw")

            proxy.begin_capture("002-raw", "/api/chat")
            _post(proxy.endpoint, "/api/chat", b'{"first":true}')
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(proxy.endpoint, "/api/chat", b'{"second":true}')
            assert exc.value.code == 409
            with pytest.raises(HttpObservationError, match="multiple"):
                proxy.finish_capture("002-raw")
        finally:
            proxy.close()


@pytest.mark.parametrize(
    ("path", "body", "message"),
    [
        ("/v1/chat/completions", b"not-json", "malformed"),
        ("/api/chat", b'{"valid":true}', "path"),
    ],
)
def test_proxy_rejects_malformed_or_mismatched_capture(path, body, message):
    with _upstream() as (upstream, forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        try:
            proxy.begin_capture(
                "001-stock",
                "/v1/chat/completions",
            )
            with pytest.raises(urllib.error.HTTPError):
                _post(proxy.endpoint, path, body)
            with pytest.raises(HttpObservationError, match=message):
                proxy.finish_capture("001-stock")
        finally:
            proxy.close()
    assert forwarded == []


def test_proxy_bounds_request_and_response_bodies():
    with _upstream(b'{"response":"too-large"}') as (upstream, forwarded):
        proxy = LoopbackRecordingProxy.open(
            upstream,
            max_request_bytes=16,
            max_response_bytes=8,
        )
        try:
            proxy.begin_capture("001-raw", "/api/chat")
            with pytest.raises(urllib.error.HTTPError):
                _post(proxy.endpoint, "/api/chat", b'{"request":"too-large"}')
            with pytest.raises(HttpObservationError, match="request body"):
                proxy.finish_capture("001-raw")

            proxy.begin_capture("002-raw", "/api/chat")
            with pytest.raises(urllib.error.HTTPError):
                _post(proxy.endpoint, "/api/chat", b'{"ok":true}')
            with pytest.raises(HttpObservationError, match="response body"):
                proxy.finish_capture("002-raw")
        finally:
            proxy.close()
    assert len(forwarded) == 1


def test_proxy_close_is_bounded_and_refuses_new_windows():
    with _upstream() as (upstream, _forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        proxy.close()

        assert proxy.closed is True
        with pytest.raises(HttpObservationError, match="closed"):
            proxy.begin_capture("001-raw", "/api/chat")
        proxy.close()


def test_proxy_close_cancels_inflight_forward_and_joins_handler():
    upstream_started = threading.Event()
    release_upstream = threading.Event()

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            upstream_started.set()
            release_upstream.wait(timeout=5)

        def log_message(self, _format, *_args):
            return None

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    upstream_thread = threading.Thread(
        target=upstream.serve_forever,
        daemon=True,
    )
    upstream_thread.start()
    proxy = LoopbackRecordingProxy.open(
        f"http://127.0.0.1:{upstream.server_address[1]}"
    )
    client_errors = []

    def client():
        try:
            _post(proxy.endpoint, "/api/chat", b'{"model":"base"}')
        except Exception as exc:
            client_errors.append(type(exc).__name__)

    proxy.begin_capture("001-raw", "/api/chat")
    client_thread = threading.Thread(target=client, daemon=True)
    client_thread.start()
    assert upstream_started.wait(timeout=2)
    try:
        proxy.close()
        alive_before_upstream_release = client_thread.is_alive()
    finally:
        release_upstream.set()
        client_thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)

    assert alive_before_upstream_release is False
    assert proxy.active_requests == 0
    assert client_errors
