"""Bounded loopback HTTP observation for agent-evaluation requests."""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import math
import select
import socket
import threading
import time
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .result import ArmResult
from .schedule import TrialSpec


DEFAULT_REQUEST_BYTES = 2 * 1024 * 1024
DEFAULT_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 30.0
DEFAULT_FINISH_TIMEOUT_SECONDS = 2.0
DEFAULT_CAPTURE_START_TIMEOUT_SECONDS = 0.1


class HttpObservationError(ValueError):
    """The proxy could not prove one exact request for a capture window."""


class _BufferedResponseSocket:
    """The minimal socket interface ``HTTPResponse`` needs for parsing."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def makefile(self, _mode: str) -> io.BytesIO:
        return io.BytesIO(self._payload)


@dataclass(frozen=True)
class ObservedHttpRequest:
    token: str
    method: str
    path: str
    raw_body: bytes
    body_sha256: str

    def json_body(self) -> dict[str, object]:
        try:
            value = json.loads(self.raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpObservationError("captured request body is malformed") from exc
        if not isinstance(value, dict):
            raise HttpObservationError(
                "captured request body must be a JSON object"
            )
        return value


@dataclass
class _WindowState:
    token: str
    expected_path: str
    attempts: int = 0
    request: ObservedHttpRequest | None = None
    errors: list[str] | None = None
    active_handlers: int = 0
    successful_handlers: int = 0
    terminal_state: str | None = None
    started: threading.Event = field(default_factory=threading.Event)
    completion: threading.Event = field(default_factory=threading.Event)
    sealing: bool = False
    sealed: bool = False
    accepted_generations: set[int] = field(default_factory=set)
    pending_generations: set[int] = field(default_factory=set)
    active_generations: set[int] = field(default_factory=set)
    terminal_generations: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _validated_upstream(value: str) -> tuple[str, int]:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or parsed.username
        or parsed.password
        or parsed.path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise HttpObservationError(
            "upstream must be a canonical loopback HTTP endpoint"
        )
    try:
        addresses = {
            ipaddress.ip_address(parsed.hostname)
        }
    except ValueError:
        if parsed.hostname != "localhost":
            raise HttpObservationError("upstream host must be loopback")
        addresses = {
            ipaddress.ip_address("127.0.0.1"),
            ipaddress.ip_address("::1"),
        }
    if not all(address.is_loopback for address in addresses):
        raise HttpObservationError("upstream host must be loopback")
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise HttpObservationError("upstream port is invalid") from exc
    if not 1 <= port <= 65535:
        raise HttpObservationError("upstream port is invalid")
    host = (
        "127.0.0.1"
        if parsed.hostname == "localhost"
        else ipaddress.ip_address(parsed.hostname).compressed
    )
    return host, port


def _positive_limit(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise HttpObservationError(f"{name} must be a positive integer")
    return value


class LoopbackRecordingProxy:
    """One fixed-upstream proxy with explicit one-request capture windows."""

    def __init__(
        self,
        upstream: str,
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        upstream_timeout_seconds: float,
    ) -> None:
        self._upstream_host, self._upstream_port = _validated_upstream(
            upstream
        )
        self._max_request_bytes = _positive_limit(
            max_request_bytes,
            "max_request_bytes",
        )
        self._max_response_bytes = _positive_limit(
            max_response_bytes,
            "max_response_bytes",
        )
        if (
            isinstance(upstream_timeout_seconds, bool)
            or not isinstance(upstream_timeout_seconds, (int, float))
            or not math.isfinite(float(upstream_timeout_seconds))
            or upstream_timeout_seconds <= 0
        ):
            raise HttpObservationError(
                "upstream_timeout_seconds must be positive"
            )
        self._upstream_timeout_seconds = float(upstream_timeout_seconds)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active: _WindowState | None = None
        self._used_tokens: set[str] = set()
        self._upstream_sockets: set[socket.socket] = set()
        self._client_sockets: set[socket.socket] = set()
        self._active_requests = 0
        self._next_handler_generation = 0
        self._pending_handler_generations: set[int] = set()
        self._active_handler_generations: set[int] = set()
        self._accepted_handlers: dict[
            int,
            tuple[int, _WindowState | None, socket.socket],
        ] = {}
        self._closed = False
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                proxy._handle(self)

            def do_GET(self) -> None:
                self.send_error(405)

            def log_message(self, _format: str, *_args: object) -> None:
                return None

        class RecordingServer(ThreadingHTTPServer):
            def get_request(
                self,
            ) -> tuple[socket.socket, tuple[str, int]]:
                with proxy._condition:
                    request, client_address = super().get_request()
                    try:
                        proxy._register_accepted(request)
                    except BaseException:
                        request.close()
                        raise
                    return request, client_address

            def process_request(
                self,
                request: socket.socket,
                client_address: tuple[str, int],
            ) -> None:
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    proxy._accepted_connection_finished(request)
                    raise

            def process_request_thread(
                self,
                request: socket.socket,
                client_address: tuple[str, int],
            ) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    proxy._accepted_connection_finished(request)

        self._server = RecordingServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05),
            daemon=True,
            name="agent-eval-http-observer",
        )
        self._thread.start()
        self.endpoint = (
            f"http://127.0.0.1:{self._server.server_address[1]}"
        )

    @classmethod
    def open(
        cls,
        upstream: str,
        *,
        max_request_bytes: int = DEFAULT_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_RESPONSE_BYTES,
        upstream_timeout_seconds: float = DEFAULT_UPSTREAM_TIMEOUT_SECONDS,
    ) -> "LoopbackRecordingProxy":
        return cls(
            upstream,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            upstream_timeout_seconds=upstream_timeout_seconds,
        )

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def active_requests(self) -> int:
        with self._lock:
            return self._active_requests

    def begin_capture(self, token: str, expected_path: str) -> None:
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(expected_path, str)
            or expected_path not in {"/api/chat", "/v1/chat/completions"}
        ):
            raise HttpObservationError("capture token or path is invalid")
        with self._lock:
            if self._closed:
                raise HttpObservationError("recording proxy is closed")
            if self._active is not None:
                raise HttpObservationError("a capture window is already active")
            if token in self._used_tokens:
                raise HttpObservationError("capture token was already used")
            self._used_tokens.add(token)
            self._active = _WindowState(token, expected_path)

    def finish_capture(
        self,
        token: str,
        *,
        timeout_seconds: float = DEFAULT_FINISH_TIMEOUT_SECONDS,
    ) -> ObservedHttpRequest:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise HttpObservationError(
                "capture completion timeout must be positive"
            )
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            state = self._active
            if state is None:
                raise HttpObservationError("no capture window is active")
            if token != state.token:
                raise HttpObservationError("capture token does not match")
            state.sealing = True
            started = state.started
            has_accept = bool(state.accepted_generations)
        if not has_accept:
            started.wait(
                timeout=min(
                    DEFAULT_CAPTURE_START_TIMEOUT_SECONDS,
                    max(0.0, deadline - time.monotonic()),
                )
            )
            with self._condition:
                if self._active is not state:
                    raise HttpObservationError(
                        "capture window closed before completion"
                    )
                if not state.accepted_generations:
                    self._active = None
                    state.sealed = True
                    raise HttpObservationError(
                        "capture requires exactly one request: "
                        "missing request"
                    )
        with self._condition:
            while (
                state.pending_generations
                or state.active_generations
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "capture completion timed out"
                    if reason not in state.errors:
                        state.errors.append(reason)
                    raise HttpObservationError(reason)
                self._condition.wait(timeout=remaining)
            if self._active is not state:
                raise HttpObservationError(
                    "capture window closed before completion"
                )
            if (
                state.pending_generations
                or state.active_generations
                or state.active_handlers
            ):
                raise HttpObservationError(
                    "capture completion state is inconsistent"
                )
            state.sealed = True
            self._active = None
        if state.attempts != 1:
            suffix = "multiple requests" if state.attempts > 1 else "missing request"
            raise HttpObservationError(
                f"capture requires exactly one request: {suffix}"
            )
        if state.errors:
            raise HttpObservationError("; ".join(state.errors))
        if state.request is None:
            raise HttpObservationError("capture request is missing")
        if (
            state.terminal_state != "succeeded"
            or state.successful_handlers != 1
        ):
            raise HttpObservationError(
                "capture forwarding did not complete successfully"
            )
        return state.request

    def _reject(
        self,
        handler: BaseHTTPRequestHandler,
        state: _WindowState | None,
        status: int,
        reason: str,
    ) -> None:
        if state is not None:
            with self._lock:
                state.errors.append(reason)
        handler.send_error(status)

    def _register_accepted(self, request: socket.socket) -> None:
        with self._condition:
            self._next_handler_generation += 1
            generation = self._next_handler_generation
            state = self._active if not self._closed else None
            self._accepted_handlers[id(request)] = (
                generation,
                state,
                request,
            )
            self._pending_handler_generations.add(generation)
            self._client_sockets.add(request)
            if state is not None:
                state.accepted_generations.add(generation)
                state.pending_generations.add(generation)
                state.started.set()
            self._condition.notify_all()

    def _accepted_connection_finished(
        self,
        request: socket.socket,
    ) -> None:
        with self._condition:
            accepted = self._accepted_handlers.pop(id(request), None)
            self._client_sockets.discard(request)
            if accepted is None:
                return
            generation, state, _socket = accepted
            if generation in self._pending_handler_generations:
                self._pending_handler_generations.discard(generation)
                if state is not None:
                    state.pending_generations.discard(generation)
                    state.terminal_generations.add(generation)
                    state.errors.append(
                        "accepted connection ended before request claim"
                    )
                    state.terminal_state = "failed"
                    state.completion.set()
            if generation in self._active_handler_generations:
                self._active_handler_generations.discard(generation)
                if state is not None:
                    state.active_generations.discard(generation)
                    state.terminal_generations.add(generation)
                    state.errors.append(
                        "accepted handler ended without terminal accounting"
                    )
                    state.terminal_state = "failed"
                    state.completion.set()
            self._condition.notify_all()

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        with self._condition:
            self._active_requests += 1
        state: _WindowState | None = None
        generation: int | None = None
        success = False
        try:
            claimed = self._claim_capture(handler)
            if claimed is not None:
                state, attempt, generation = claimed
                if state is not None:
                    success = self._handle_request(
                        handler,
                        state,
                        attempt,
                    )
        finally:
            with self._condition:
                if generation is not None:
                    self._active_handler_generations.discard(generation)
                if state is not None:
                    if success:
                        state.successful_handlers += 1
                    state.active_handlers -= 1
                    state.active_generations.discard(generation)
                    state.terminal_generations.add(generation)
                    if state.active_handlers == 0:
                        state.terminal_state = (
                            "succeeded"
                            if (
                                state.attempts == 1
                                and state.successful_handlers == 1
                                and not state.errors
                            )
                            else "failed"
                        )
                        state.completion.set()
                self._active_requests -= 1
                self._condition.notify_all()

    def _claim_capture(
        self,
        handler: BaseHTTPRequestHandler,
    ) -> tuple[_WindowState | None, int, int] | None:
        try:
            peer = ipaddress.ip_address(handler.client_address[0])
        except ValueError:
            handler.send_error(403)
            return None
        if not peer.is_loopback:
            handler.send_error(403)
            return None
        with self._condition:
            accepted = self._accepted_handlers.get(id(handler.connection))
            if accepted is None:
                handler.send_error(409)
                return None
            generation, state, _socket = accepted
            self._pending_handler_generations.discard(generation)
            self._active_handler_generations.add(generation)
            if state is not None:
                state.pending_generations.discard(generation)
                state.active_generations.add(generation)
                state.attempts += 1
                attempt = state.attempts
                state.active_handlers += 1
                state.started.set()
                state.completion.clear()
            else:
                attempt = 0
            self._condition.notify_all()
        if state is None:
            handler.send_error(409)
        return state, attempt, generation

    def _handle_request(
        self,
        handler: BaseHTTPRequestHandler,
        state: _WindowState,
        attempt: int,
    ) -> bool:
        if attempt != 1:
            self._reject(
                handler,
                state,
                409,
                "capture received multiple requests",
            )
            return False
        if handler.path != state.expected_path:
            self._reject(
                handler,
                state,
                404,
                "capture request path mismatch",
            )
            return False
        if handler.headers.get("Transfer-Encoding"):
            self._reject(
                handler,
                state,
                400,
                "chunked request bodies are unsupported",
            )
            return False
        raw_length = handler.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._reject(
                handler,
                state,
                411,
                "request Content-Length is missing or invalid",
            )
            return False
        if length > self._max_request_bytes:
            self._reject(
                handler,
                state,
                413,
                "request body exceeded capture limit",
            )
            return False
        raw_body = handler.rfile.read(length)
        if len(raw_body) != length:
            self._reject(
                handler,
                state,
                400,
                "request body was truncated",
            )
            return False
        try:
            parsed = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reject(
                handler,
                state,
                400,
                "captured request body is malformed",
            )
            return False
        if not isinstance(parsed, dict):
            self._reject(
                handler,
                state,
                400,
                "captured request body is malformed",
            )
            return False
        request = ObservedHttpRequest(
            state.token,
            "POST",
            handler.path,
            raw_body,
            hashlib.sha256(raw_body).hexdigest(),
        )
        with self._lock:
            state.request = request

        upstream_socket: socket.socket | None = None
        try:
            upstream_socket = socket.create_connection(
                (self._upstream_host, self._upstream_port),
                timeout=self._upstream_timeout_seconds,
            )
            with self._lock:
                if self._closed:
                    state.errors.append(
                        "recording proxy closed during forwarding"
                    )
                    upstream_socket.shutdown(socket.SHUT_RDWR)
                    upstream_socket.close()
                    return False
                self._upstream_sockets.add(upstream_socket)
            upstream_socket.setblocking(False)
            response = self._exchange(
                upstream_socket,
                handler.path,
                raw_body,
                handler.headers.get("Content-Type", "application/json"),
                handler.headers.get("Accept", "*/*"),
            )
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_length = int(declared)
                except ValueError as exc:
                    raise HttpObservationError(
                        "upstream response Content-Length is invalid"
                    ) from exc
                if declared_length > self._max_response_bytes:
                    raise HttpObservationError(
                        "response body exceeded capture limit"
                    )
                if declared_length < 0:
                    raise HttpObservationError(
                        "upstream response Content-Length is invalid"
                    )
            response_body = response.read(self._max_response_bytes + 1)
            if len(response_body) > self._max_response_bytes:
                raise HttpObservationError(
                    "response body exceeded capture limit"
                )
            if declared is not None and len(response_body) != declared_length:
                raise HttpObservationError(
                    "upstream response body was truncated"
                )
            handler.send_response(response.status)
            content_type = response.getheader("Content-Type")
            if content_type:
                handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(response_body)))
            handler.end_headers()
            handler.wfile.write(response_body)
            return True
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, HttpObservationError)
                else f"upstream forwarding failed: {type(exc).__name__}: {exc}"
            )
            with self._lock:
                state.errors.append(reason)
                closed = self._closed
            if not closed and not handler.wfile.closed:
                handler.send_error(502)
            return False
        finally:
            if upstream_socket is not None:
                upstream_socket.close()
                with self._lock:
                    self._upstream_sockets.discard(upstream_socket)

    def _exchange(
        self,
        upstream_socket: socket.socket,
        path: str,
        raw_body: bytes,
        content_type: str,
        accept: str,
    ) -> http.client.HTTPResponse:
        if "\r" in content_type or "\n" in content_type:
            raise HttpObservationError("request Content-Type is invalid")
        if "\r" in accept or "\n" in accept:
            raise HttpObservationError("request Accept is invalid")
        host_header = (
            f"[{self._upstream_host}]"
            if ":" in self._upstream_host
            else self._upstream_host
        )
        request_head = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host_header}:{self._upstream_port}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Accept: {accept}\r\n"
            f"Content-Length: {len(raw_body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        outbound = memoryview(request_head + raw_body)
        deadline = time.monotonic() + self._upstream_timeout_seconds
        while outbound:
            self._raise_if_exchange_cancelled(deadline)
            _, writable, _ = select.select(
                [],
                [upstream_socket],
                [],
                min(0.05, max(0.0, deadline - time.monotonic())),
            )
            if writable:
                sent = upstream_socket.send(outbound)
                if sent <= 0:
                    raise HttpObservationError(
                        "upstream closed while receiving request"
                    )
                outbound = outbound[sent:]

        response_wire_limit = self._max_response_bytes + 64 * 1024
        response_wire = bytearray()
        while True:
            self._raise_if_exchange_cancelled(deadline)
            readable, _, _ = select.select(
                [upstream_socket],
                [],
                [],
                min(0.05, max(0.0, deadline - time.monotonic())),
            )
            if not readable:
                continue
            chunk = upstream_socket.recv(64 * 1024)
            if not chunk:
                break
            response_wire.extend(chunk)
            if len(response_wire) > response_wire_limit:
                raise HttpObservationError(
                    "upstream response exceeded wire limit"
                )
        if not response_wire:
            raise HttpObservationError("upstream returned no HTTP response")
        response = http.client.HTTPResponse(
            _BufferedResponseSocket(bytes(response_wire))
        )
        response.begin()
        return response

    def _raise_if_exchange_cancelled(self, deadline: float) -> None:
        with self._lock:
            closed = self._closed
        if closed:
            raise HttpObservationError(
                "recording proxy closed during forwarding"
            )
        if time.monotonic() >= deadline:
            raise HttpObservationError("upstream forwarding timed out")

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._active = None
            upstream_sockets = tuple(self._upstream_sockets)
            client_sockets = tuple(self._client_sockets)
        for upstream_socket in upstream_sockets:
            try:
                upstream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            upstream_socket.close()
        for client_socket in client_sockets:
            try:
                client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client_socket.close()
        self._server.shutdown()
        self._server.server_close()
        deadline = time.monotonic() + 2
        with self._condition:
            while (
                self._active_requests
                or self._pending_handler_generations
                or self._active_handler_generations
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HttpObservationError(
                        "recording proxy request cleanup did not complete"
                    )
                self._condition.wait(timeout=remaining)
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise HttpObservationError(
                "recording proxy cleanup did not complete"
            )


def attach_observed_request(
    result: ArmResult,
    observation: ObservedHttpRequest,
    trial: TrialSpec,
) -> ArmResult:
    """Replace any adapter claim with metadata from one recorded HTTP body."""
    if not isinstance(result, ArmResult):
        raise HttpObservationError("observed request requires an ArmResult")
    if not isinstance(observation, ObservedHttpRequest):
        raise HttpObservationError(
            "observed request requires a typed proxy observation"
        )
    if not isinstance(trial, TrialSpec):
        raise HttpObservationError("observed request requires a TrialSpec")
    expected_path = (
        "/api/chat"
        if result.arm == "raw"
        else "/v1/chat/completions"
    )
    expected_token = f"{trial.index:03d}-{result.arm}"
    if (
        observation.method != "POST"
        or observation.path != expected_path
        or observation.token != expected_token
    ):
        raise HttpObservationError(
            "observed request identity does not match the scheduled arm"
        )
    body = observation.json_body()
    metadata: dict[str, object] = {
        "source": "loopback_recording_proxy",
        "observed": True,
        "capture_token": observation.token,
        "method": observation.method,
        "path": observation.path,
        "raw_body_sha256": observation.body_sha256,
        "raw_body": observation.raw_body.decode("utf-8"),
        "trial_index": trial.index,
    }
    if result.arm == "raw":
        metadata.update(
            {
                "stream": body.get("stream"),
                "options": body.get("options"),
            }
        )
    else:
        metadata.update(
            {
                "temperature": body.get("temperature"),
                "seed": body.get("seed"),
                "max_output_tokens": body.get("max_tokens"),
            }
        )
    return replace(result, request_metadata=metadata)
