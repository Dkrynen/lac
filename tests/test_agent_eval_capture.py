import io

import pytest

from backend.agent_eval.capture import CaptureLimitExceeded, bounded_http_json


class Response:
    def __init__(
        self,
        payload: bytes,
        *,
        headers=None,
        final_url=None,
        max_chunk=None,
    ):
        self.payload = io.BytesIO(payload)
        self.headers = headers or {}
        self.final_url = final_url
        self.max_chunk = max_chunk
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size=-1):
        assert size != -1
        self.read_sizes.append(size)
        if self.max_chunk is not None:
            size = min(size, self.max_chunk)
        return self.payload.read(size)

    def geturl(self):
        return self.final_url or "http://127.0.0.1:11434/api/tags"


def test_bounded_http_json_reads_at_most_limit_plus_one():
    calls = []

    def open_fn(request, timeout):
        calls.append((request.full_url, timeout))
        return Response(
            b'{"ok":true}',
            final_url="http://127.0.0.1:11434/api/version",
        )

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


def test_bounded_http_json_accumulates_short_chunks_until_eof():
    response = Response(b'{"models":[]}', max_chunk=2)

    assert bounded_http_json(
        "http://127.0.0.1:11434/api/tags",
        method="GET",
        body=None,
        timeout=5,
        max_bytes=64,
        open_fn=lambda *_args, **_kwargs: response,
    ) == {"models": []}
    assert len(response.read_sizes) > 2
    assert all(0 < size <= 65 for size in response.read_sizes)


@pytest.mark.parametrize("content_length", ["65", "not-a-number"])
def test_bounded_http_json_rejects_oversized_or_invalid_content_length(content_length):
    expected = CaptureLimitExceeded if content_length == "65" else ValueError
    with pytest.raises(expected):
        bounded_http_json(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
            body=None,
            timeout=5,
            max_bytes=64,
            open_fn=lambda *_args, **_kwargs: Response(
                b'{"models":[]}',
                headers={"Content-Length": content_length},
            ),
        )


def test_bounded_http_json_rejects_redirected_final_url():
    with pytest.raises(ValueError, match="redirect"):
        bounded_http_json(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
            body=None,
            timeout=5,
            max_bytes=64,
            open_fn=lambda *_args, **_kwargs: Response(
                b'{"models":[]}',
                final_url="http://localhost:11434/api/tags",
            ),
        )


@pytest.mark.parametrize(
    ("url", "method", "body"),
    [
        ("http://127.0.0.1:11434/api/version", "POST", None),
        ("http://127.0.0.1:11434/api/version", "GET", {}),
        ("http://127.0.0.1:11434/api/tags", "POST", None),
        ("http://127.0.0.1:11434/api/show", "GET", None),
        ("http://127.0.0.1:11434/api/show", "POST", {"model": "base"}),
        (
            "http://127.0.0.1:11434/api/show",
            "POST",
            {"model": "base", "verbose": True},
        ),
    ],
)
def test_bounded_http_json_enforces_endpoint_method_and_body_contract(
    url, method, body
):
    with pytest.raises(ValueError):
        bounded_http_json(
            url,
            method=method,
            body=body,
            timeout=5,
            max_bytes=64,
            open_fn=lambda *_args, **_kwargs: pytest.fail("must not open"),
        )
