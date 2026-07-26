"""Bounded local HTTP capture primitives shared by evaluation evidence tasks."""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


IDENTITY_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
OLLAMA_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
_OLLAMA_ENDPOINTS = frozenset({"/api/version", "/api/tags", "/api/show"})


class CaptureLimitExceeded(ValueError):
    """A captured response exceeded its declared, fail-closed byte limit."""


def _is_loopback(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or not _is_loopback(parsed.hostname)
        or parsed.port != 11434
        or parsed.query
        or parsed.fragment
        or parsed.path not in _OLLAMA_ENDPOINTS
    ):
        raise ValueError("capture URL must be an unauthenticated loopback Ollama endpoint")


def bounded_http_json(
    url: str,
    *,
    method: str,
    body: object | None,
    timeout: float,
    max_bytes: int,
    open_fn=urlopen,
) -> dict:
    """Fetch one local JSON object without ever performing an unbounded read."""
    _validate_url(url)
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    expected = {
        "/api/version": ("GET", None),
        "/api/tags": ("GET", None),
        "/api/show": ("POST", {"verbose": False}),
    }
    expected_method, expected_body = expected[urlsplit(url).path]
    if method != expected_method:
        raise ValueError("capture method is invalid for endpoint")
    if expected_body is None:
        if body is not None:
            raise ValueError("GET capture requests cannot include a body")
    elif not isinstance(body, dict) or set(body) != {"model", "verbose"} or not isinstance(body.get("model"), str) or not body["model"] or body.get("verbose") is not False:
        raise ValueError("show capture body must be an exact model/verbose object")
    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, method=method, headers=headers)
    with open_fn(request, timeout) as response:
        final_url = getattr(response, "geturl", lambda: url)()
        if final_url != url:
            raise ValueError("capture redirect is not allowed")
        content_length = getattr(response, "headers", {}).get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise ValueError("response Content-Length is invalid") from exc
            if declared_length < 0:
                raise ValueError("response Content-Length is invalid")
            if declared_length > max_bytes:
                raise CaptureLimitExceeded(
                    f"response exceeds {max_bytes} byte capture limit"
                )
        chunks = bytearray()
        while True:
            chunk = response.read(min(64 * 1024, max_bytes + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise CaptureLimitExceeded(f"response exceeds {max_bytes} byte capture limit")
        raw = bytes(chunks)
    if len(raw) > max_bytes:
        raise CaptureLimitExceeded(f"response exceeds {max_bytes} byte capture limit")
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("response is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("response JSON must be an object")
    return value
