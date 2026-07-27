from __future__ import annotations

import json
import threading
import time
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


def test_finish_waits_for_exact_19mb_body_forwarding_and_response_completion():
    raw_body = json.dumps(
        {"model": "base", "padding": "x" * 1_900_000},
        separators=(",", ":"),
    ).encode("utf-8")
    assert 1_900_000 < len(raw_body) < 2_000_000
    with _upstream() as (upstream, forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        entered_exchange = threading.Event()
        release_exchange = threading.Event()
        original_exchange = proxy._exchange

        def delayed_exchange(*args):
            entered_exchange.set()
            assert release_exchange.wait(timeout=2)
            return original_exchange(*args)

        proxy._exchange = delayed_exchange
        client_errors = []
        finish_results = []
        finish_errors = []
        proxy.begin_capture("001-raw", "/api/chat")
        client_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: _post(proxy.endpoint, "/api/chat", raw_body),
                client_errors,
            ),
            daemon=True,
        )
        finish_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: finish_results.append(
                    proxy.finish_capture("001-raw")
                ),
                finish_errors,
            ),
            daemon=True,
        )
        client_thread.start()
        assert entered_exchange.wait(timeout=2)
        finish_thread.start()
        time.sleep(0.05)
        try:
            assert finish_thread.is_alive()
            assert forwarded == []
            release_exchange.set()
            finish_thread.join(timeout=3)
            client_thread.join(timeout=3)
        finally:
            release_exchange.set()
            proxy.close()

    assert not finish_thread.is_alive()
    assert not client_thread.is_alive()
    assert client_errors == []
    assert finish_errors == []
    assert finish_results[0].raw_body == raw_body
    assert forwarded[0]["body"] == raw_body


def _capture_call(call, errors):
    try:
        call()
    except Exception as exc:
        errors.append(exc)


def test_finish_rejects_late_upstream_error_after_request_capture():
    with _upstream() as (upstream, _forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        entered_exchange = threading.Event()
        release_exchange = threading.Event()

        def failing_exchange(*_args):
            entered_exchange.set()
            assert release_exchange.wait(timeout=2)
            raise HttpObservationError("late upstream failure")

        proxy._exchange = failing_exchange
        client_errors = []
        finish_errors = []
        proxy.begin_capture("001-raw", "/api/chat")
        client_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: _post(
                    proxy.endpoint,
                    "/api/chat",
                    b'{"model":"base"}',
                ),
                client_errors,
            ),
            daemon=True,
        )
        finish_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: proxy.finish_capture("001-raw"),
                finish_errors,
            ),
            daemon=True,
        )
        client_thread.start()
        assert entered_exchange.wait(timeout=2)
        finish_thread.start()
        time.sleep(0.05)
        try:
            assert finish_thread.is_alive()
            release_exchange.set()
            finish_thread.join(timeout=3)
            client_thread.join(timeout=3)
        finally:
            release_exchange.set()
            proxy.close()

    assert len(finish_errors) == 1
    assert "late upstream failure" in str(finish_errors[0])


def test_finish_timeout_fails_closed_without_detaching_active_window():
    with _upstream() as (upstream, _forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        entered_exchange = threading.Event()
        release_exchange = threading.Event()
        original_exchange = proxy._exchange

        def delayed_exchange(*args):
            entered_exchange.set()
            assert release_exchange.wait(timeout=2)
            return original_exchange(*args)

        proxy._exchange = delayed_exchange
        client_errors = []
        proxy.begin_capture("001-raw", "/api/chat")
        client_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: _post(
                    proxy.endpoint,
                    "/api/chat",
                    b'{"model":"base"}',
                ),
                client_errors,
            ),
            daemon=True,
        )
        client_thread.start()
        assert entered_exchange.wait(timeout=2)
        try:
            with pytest.raises(HttpObservationError, match="timed out"):
                proxy.finish_capture("001-raw", timeout_seconds=0.05)
            with pytest.raises(HttpObservationError, match="already active"):
                proxy.begin_capture("002-raw", "/api/chat")
            release_exchange.set()
            client_thread.join(timeout=3)
            with pytest.raises(HttpObservationError, match="timed out"):
                proxy.finish_capture("001-raw", timeout_seconds=1)
        finally:
            release_exchange.set()
            proxy.close()


def test_finish_returns_only_after_successful_response_path_completion():
    with _upstream() as (upstream, forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        client_errors = []
        proxy.begin_capture("001-raw", "/api/chat")
        client_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: _post(
                    proxy.endpoint,
                    "/api/chat",
                    b'{"model":"base"}',
                ),
                client_errors,
            ),
            daemon=True,
        )
        client_thread.start()
        try:
            capture = proxy.finish_capture(
                "001-raw",
                timeout_seconds=2,
            )
            assert proxy.active_requests == 0
        finally:
            proxy.close()
            client_thread.join(timeout=2)

    assert capture.raw_body == b'{"model":"base"}'
    assert forwarded[0]["body"] == capture.raw_body
    assert client_errors == []


def test_finish_seal_counts_second_handler_accepted_before_claim():
    with _upstream() as (upstream, forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        proxy.begin_capture("001-raw", "/api/chat")
        assert _post(
            proxy.endpoint,
            "/api/chat",
            b'{"request":1}',
        ) == b'{"ok":true}'

        entered_claim = threading.Event()
        release_claim = threading.Event()
        original_claim = proxy._claim_capture

        def paused_claim(*args):
            entered_claim.set()
            assert release_claim.wait(timeout=2)
            return original_claim(*args)

        proxy._claim_capture = paused_claim
        second_errors = []
        finish_results = []
        finish_errors = []
        second_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: _post(
                    proxy.endpoint,
                    "/api/chat",
                    b'{"request":2}',
                ),
                second_errors,
            ),
            daemon=True,
        )
        finish_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: finish_results.append(
                    proxy.finish_capture("001-raw")
                ),
                finish_errors,
            ),
            daemon=True,
        )
        second_thread.start()
        assert entered_claim.wait(timeout=2)
        finish_thread.start()
        time.sleep(0.05)
        try:
            assert finish_thread.is_alive()
            release_claim.set()
            second_thread.join(timeout=3)
            finish_thread.join(timeout=3)
        finally:
            release_claim.set()
            proxy.close()

    assert not second_thread.is_alive()
    assert not finish_thread.is_alive()
    assert finish_results == []
    assert len(finish_errors) == 1
    assert (
        "multiple" in str(finish_errors[0])
        or "accepted" in str(finish_errors[0])
    )
    assert len(second_errors) == 1
    assert isinstance(
        second_errors[0],
        (urllib.error.HTTPError, ConnectionAbortedError),
    )
    assert len(forwarded) == 1


def test_accepted_and_claimed_handler_generations_are_condition_accounted():
    with _upstream() as (upstream, _forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        entered_claim = threading.Event()
        release_claim = threading.Event()
        original_claim = proxy._claim_capture

        def paused_claim(*args):
            entered_claim.set()
            assert release_claim.wait(timeout=2)
            return original_claim(*args)

        proxy._claim_capture = paused_claim
        errors = []
        proxy.begin_capture("001-raw", "/api/chat")
        client_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: _post(
                    proxy.endpoint,
                    "/api/chat",
                    b'{"model":"base"}',
                ),
                errors,
            ),
            daemon=True,
        )
        client_thread.start()
        assert entered_claim.wait(timeout=2)
        try:
            with proxy._condition:
                assert len(proxy._pending_handler_generations) == 1
                assert proxy._active_handler_generations == set()
            release_claim.set()
            client_thread.join(timeout=3)
            with proxy._condition:
                assert proxy._pending_handler_generations == set()
                assert proxy._active_handler_generations == set()
            proxy.finish_capture("001-raw")
        finally:
            release_claim.set()
            proxy.close()

    assert errors == []


def test_new_connection_after_atomic_seal_cannot_mutate_observation():
    with _upstream() as (upstream, forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        adapter_complete = threading.Event()
        client_errors = []

        def adapter():
            try:
                _post(
                    proxy.endpoint,
                    "/api/chat",
                    b'{"request":1}',
                )
            except Exception as exc:
                client_errors.append(exc)
            finally:
                adapter_complete.set()

        proxy.begin_capture("001-raw", "/api/chat")
        adapter_thread = threading.Thread(target=adapter, daemon=True)
        adapter_thread.start()
        assert adapter_complete.wait(timeout=2)
        adapter_thread.join(timeout=2)
        capture = proxy.finish_capture("001-raw")

        late_errors = []
        late_thread = threading.Thread(
            target=lambda: _capture_call(
                lambda: _post(
                    proxy.endpoint,
                    "/api/chat",
                    b'{"request":2}',
                ),
                late_errors,
            ),
            daemon=True,
        )
        late_thread.start()
        late_thread.join(timeout=2)
        try:
            assert capture.raw_body == b'{"request":1}'
            assert len(forwarded) == 1
            assert len(late_errors) == 1
        finally:
            proxy.close()

    assert client_errors == []


def test_quiet_window_seals_at_global_quiescence_without_deadlock():
    with _upstream() as (upstream, _forwarded):
        proxy = LoopbackRecordingProxy.open(upstream)
        proxy.begin_capture("001-raw", "/api/chat")
        assert _post(
            proxy.endpoint,
            "/api/chat",
            b'{"model":"base"}',
        ) == b'{"ok":true}'

        started = time.monotonic()
        capture = proxy.finish_capture("001-raw", timeout_seconds=1)
        elapsed = time.monotonic() - started
        try:
            with proxy._condition:
                assert proxy._pending_handler_generations == set()
                assert proxy._active_handler_generations == set()
        finally:
            proxy.close()

    assert capture.raw_body == b'{"model":"base"}'
    assert elapsed < 1
