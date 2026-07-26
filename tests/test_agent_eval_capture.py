import io

import pytest

from backend.agent_eval.capture import CaptureLimitExceeded, bounded_http_json


class Response:
    def __init__(self, payload: bytes):
        self.payload = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size=-1):
        assert size != -1
        return self.payload.read(size)


def test_bounded_http_json_reads_at_most_limit_plus_one():
    calls = []

    def open_fn(request, timeout):
        calls.append((request.full_url, timeout))
        return Response(b'{"ok":true}')

    assert bounded_http_json(
        "http://127.0.0.1:11434/api/version",
        method="GET",
        body=None,
        timeout=5,
        max_bytes=64,
        open_fn=open_fn,
    ) == {"ok": True}
    assert calls == [("http://127.0.0.1:11434/api/version", 5)]


@pytest.mark.parametrize(
    "payload",
    [b'{"models":[]}', b'\xff', b'{', b'[]'],
)
def test_bounded_http_json_rejects_overflow_or_invalid_object(payload):
    expected = CaptureLimitExceeded if len(payload) > 4 else ValueError
    with pytest.raises(expected):
        bounded_http_json(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
            body=None,
            timeout=5,
            max_bytes=4,
            open_fn=lambda *_args, **_kwargs: Response(payload),
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434/api/tags",
        "http://user:pass@127.0.0.1:11434/api/tags",
        "http://127.0.0.1:11434/api/tags?x=1",
        "http://127.0.0.1:11434/api/tags#fragment",
        "http://127.0.0.1:11434/not-ollama",
    ],
)
def test_bounded_http_json_rejects_nonlocal_or_noncanonical_urls(url):
    with pytest.raises(ValueError):
        bounded_http_json(
            url,
            method="GET",
            body=None,
            timeout=5,
            max_bytes=64,
            open_fn=lambda *_args, **_kwargs: pytest.fail("must not open"),
        )


def test_bounded_http_json_rejects_response_larger_than_two_mib():
    with pytest.raises(CaptureLimitExceeded):
        bounded_http_json(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
            body=None,
            timeout=5,
            max_bytes=2 * 1024 * 1024,
            open_fn=lambda *_args, **_kwargs: Response(b"x" * (2 * 1024 * 1024 + 1)),
        )
