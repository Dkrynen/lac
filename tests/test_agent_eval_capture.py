import io
import subprocess
import sys

import pytest

from backend.agent_eval.capture import (
    CaptureLimitExceeded,
    CaptureLimits,
    bounded_http_json,
    capture_bounded_response,
    run_bounded_process,
)


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


def test_bounded_process_captures_stdout_stderr_and_exit_code(tmp_path):
    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=1024, stderr_bytes=1024),
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert result.overflowed is False
    assert result.completed is True
    assert result.limits == CaptureLimits(stdout_bytes=1024, stderr_bytes=1024)
    assert result.observed_stdout_bytes == len(b"out\r\n")
    assert result.observed_stderr_bytes == len(b"err\r\n")


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_bounded_process_terminates_on_stream_overflow(tmp_path, stream):
    target = "sys.stdout.buffer" if stream == "stdout" else "sys.stderr.buffer"
    result = run_bounded_process(
        [sys.executable, "-c", f"import sys,time; {target}.write(b'x'*4096); {target}.flush(); time.sleep(30)"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=128, stderr_bytes=128),
    )

    assert result.overflowed is True
    assert result.completed is False
    assert result.timed_out is False
    assert result.errors == (f"{stream}_capture_limit_exceeded",)
    observed = getattr(result, f"observed_{stream}_bytes")
    assert observed == 129
    assert len(getattr(result, stream).encode("utf-8")) <= 128


def test_bounded_process_terminates_on_timeout(tmp_path):
    result = run_bounded_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env={},
        timeout=0.1,
        limits=CaptureLimits(stdout_bytes=128, stderr_bytes=128),
    )

    assert result.completed is False
    assert result.timed_out is True
    assert result.errors == ("timeout:0.1s",)
    assert result.exit_code is not None


def test_bounded_process_replaces_invalid_utf8_and_closes_stdin(tmp_path):
    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(b'\\xff'+str(len(data)).encode())",
        ],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=128, stderr_bytes=128),
    )

    assert result.completed is True
    assert result.stdout == "\ufffd0"


def test_bounded_process_drains_stdout_and_stderr_concurrently(tmp_path):
    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            "import sys; "
            "[ (sys.stdout.buffer.write(b'o'*4096), sys.stdout.buffer.flush(), "
            "sys.stderr.buffer.write(b'e'*4096), sys.stderr.buffer.flush()) "
            "for _ in range(32) ]",
        ],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=256 * 1024, stderr_bytes=256 * 1024),
    )

    assert result.completed is True
    assert result.observed_stdout_bytes == 128 * 1024
    assert result.observed_stderr_bytes == 128 * 1024


def test_bounded_process_removes_exclusive_temporary_logs(tmp_path):
    result = run_bounded_process(
        [sys.executable, "-c", "print('done')"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=128, stderr_bytes=128),
    )

    assert result.temporary_paths
    assert all(not path.exists() for path in result.temporary_paths)


def test_optional_launcher_receives_binary_pipe_contract(tmp_path):
    calls = []

    def launcher(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.Popen(argv, **kwargs)

    result = run_bounded_process(
        [sys.executable, "-c", "print('launched')"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=128, stderr_bytes=128),
        launcher=launcher,
    )

    assert result.completed is True
    kwargs = calls[0][1]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert kwargs["text"] is False


def test_capture_bounded_response_accumulates_short_reads_and_reports_overflow():
    response = Response(b"x" * 9, max_chunk=2)

    with pytest.raises(CaptureLimitExceeded) as raised:
        capture_bounded_response(response, 8)

    assert raised.value.allowed_bytes == 8
    assert raised.value.observed_bytes == 9
    assert response.read_sizes[0] == 9
