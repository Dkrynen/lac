import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import backend.agent_eval.capture as capture_module
from backend.agent_eval.capture import (
    CaptureLimitExceeded,
    CaptureLimits,
    bounded_http_json,
    capture_bounded_response,
    run_bounded_process,
)
from backend.agent_eval.windows_job import WindowsJobProcess


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


def test_default_launcher_routes_through_proc_popen_with_binary_contract(
    tmp_path,
    monkeypatch,
):
    calls = []

    class ExitedProcess:
        def __init__(self):
            self.stdout = io.BytesIO(b"wrapped-out")
            self.stderr = io.BytesIO()
            self.returncode = 0

        def poll(self):
            return self.returncode

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return ExitedProcess()

    monkeypatch.setattr(capture_module.proc, "popen", fake_popen)

    result = run_bounded_process(
        ["wrapped-child", "--flag"],
        cwd=tmp_path,
        env={"ONLY": "VALUE"},
        timeout=1,
        limits=CaptureLimits(stdout_bytes=128, stderr_bytes=128),
    )

    assert result.completed is True
    assert result.stdout == "wrapped-out"
    assert calls == [
        (
            ["wrapped-child", "--flag"],
            {
                "cwd": tmp_path,
                "env": {"ONLY": "VALUE"},
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": False,
            },
        )
    ]


class _CleanupPipe(io.BytesIO):
    def __init__(self, payload=b""):
        super().__init__(payload)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        super().close()


class _CleanupProcess:
    def __init__(self):
        self.stdout = _CleanupPipe()
        self.stderr = _CleanupPipe()
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = []
        self.close_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = 71

    def kill(self):
        self.kill_calls += 1
        self.returncode = 72

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        assert timeout is not None
        if self.returncode is None:
            raise subprocess.TimeoutExpired("cleanup-child", timeout)
        return self.returncode

    def close(self):
        self.close_calls += 1
        if self.stdout is not None:
            self.stdout.close()
        if self.stderr is not None:
            self.stderr.close()


def _capture_reader_threads():
    return [
        thread
        for thread in threading.enumerate()
        if thread.name in {"agent-eval-stdout", "agent-eval-stderr"}
    ]


def test_launch_with_missing_pipe_cleans_process_and_temp_before_reraising(
    tmp_path,
    monkeypatch,
):
    process = _CleanupProcess()
    process.stdout = None
    capture_root = tmp_path / "missing-pipe-capture"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )

    with pytest.raises(RuntimeError, match="binary stdout and stderr"):
        run_bounded_process(
            ["missing-pipe-child"],
            cwd=tmp_path,
            env={},
            timeout=1,
            limits=CaptureLimits(cleanup_grace_seconds=0.2),
            launcher=lambda *_args, **_kwargs: process,
        )

    assert process.terminate_calls == 1
    assert process.wait_calls
    assert process.close_calls == 1
    assert process.stderr.closed is True
    assert not capture_root.exists()
    assert _capture_reader_threads() == []


def test_second_reader_start_failure_cleans_started_reader_and_process(
    tmp_path,
    monkeypatch,
):
    process = _CleanupProcess()
    capture_root = tmp_path / "reader-start-capture"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )
    real_thread = threading.Thread

    def thread_factory(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        if kwargs.get("name") == "agent-eval-stderr":
            thread.start = lambda: (_ for _ in ()).throw(
                RuntimeError("second reader start failed")
            )
        return thread

    monkeypatch.setattr(capture_module.threading, "Thread", thread_factory)

    with pytest.raises(RuntimeError, match="second reader start failed"):
        run_bounded_process(
            ["reader-start-child"],
            cwd=tmp_path,
            env={},
            timeout=1,
            limits=CaptureLimits(cleanup_grace_seconds=0.2),
            launcher=lambda *_args, **_kwargs: process,
        )

    assert process.terminate_calls == 1
    assert process.close_calls == 1
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    assert not capture_root.exists()
    assert _capture_reader_threads() == []


def test_poll_exception_after_readers_start_cleans_every_acquired_resource(
    tmp_path,
    monkeypatch,
):
    process = _CleanupProcess()
    process.stdout = _CleanupPipe(b"out")
    process.stderr = _CleanupPipe(b"err")
    capture_root = tmp_path / "poll-failure-capture"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )

    def fail_poll():
        raise RuntimeError("poll exploded")

    process.poll = fail_poll

    with pytest.raises(RuntimeError, match="poll exploded"):
        run_bounded_process(
            ["poll-failure-child"],
            cwd=tmp_path,
            env={},
            timeout=1,
            limits=CaptureLimits(cleanup_grace_seconds=0.2),
            launcher=lambda *_args, **_kwargs: process,
        )

    assert process.terminate_calls == 1
    assert process.close_calls == 1
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    assert not capture_root.exists()
    assert _capture_reader_threads() == []


def test_job_containment_query_failure_still_closes_job_and_temp(
    tmp_path,
    monkeypatch,
):
    class QueryFailureJob(WindowsJobProcess):
        def __init__(self):
            self.stdout = _CleanupPipe()
            self.stderr = _CleanupPipe()
            self.returncode = None
            self.terminate_calls = 0
            self.wait_calls = []
            self.close_calls = 0

        def containment_evidence(self):
            raise RuntimeError("job containment query failed")

        def terminate_tree(self):
            self.terminate_calls += 1
            self.returncode = 73

        def kill_tree(self):
            self.returncode = 74

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            assert timeout is not None
            return self.returncode

        def close(self):
            self.close_calls += 1
            self.stdout.close()
            self.stderr.close()

    process = QueryFailureJob()
    capture_root = tmp_path / "job-query-capture"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )

    def launcher(*_args, **_kwargs):
        return process

    launcher._windows_job_launcher = True

    with pytest.raises(RuntimeError, match="job containment query failed"):
        run_bounded_process(
            ["job-query-child.exe"],
            cwd=tmp_path,
            env={},
            timeout=1,
            limits=CaptureLimits(cleanup_grace_seconds=0.2),
            launcher=launcher,
        )

    assert process.terminate_calls == 1
    assert process.wait_calls
    assert process.close_calls == 1
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    assert not capture_root.exists()
    assert _capture_reader_threads() == []


def test_capture_bounded_response_accumulates_short_reads_and_reports_overflow():
    response = Response(b"x" * 9, max_chunk=2)

    with pytest.raises(CaptureLimitExceeded) as raised:
        capture_bounded_response(response, 8)

    assert raised.value.allowed_bytes == 8
    assert raised.value.observed_bytes == 9
    assert response.read_sizes[0] == 9


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf")])
def test_capture_limits_rejects_invalid_cleanup_grace(value):
    with pytest.raises(ValueError, match="cleanup_grace_seconds"):
        CaptureLimits(cleanup_grace_seconds=value)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf")])
def test_bounded_process_rejects_invalid_timeout_before_launch(tmp_path, value):
    with pytest.raises(ValueError, match="timeout"):
        run_bounded_process(
            [sys.executable, "-c", "print('must not launch')"],
            cwd=tmp_path,
            env={},
            timeout=value,
            limits=CaptureLimits(),
            launcher=lambda *_args, **_kwargs: pytest.fail("must not launch"),
        )


@pytest.mark.parametrize(
    "value",
    [True, 0, -1, float("nan"), float("inf"), -float("inf")],
)
def test_bounded_http_json_rejects_invalid_timeout_before_open(value):
    with pytest.raises(ValueError, match="timeout"):
        bounded_http_json(
            "http://127.0.0.1:11434/api/version",
            method="GET",
            body=None,
            timeout=value,
            max_bytes=64,
            open_fn=lambda *_args, **_kwargs: pytest.fail("must not open"),
        )


def test_bounded_process_reports_inherited_pipe_handles_without_closing_live_readers(
    tmp_path, monkeypatch, recwarn
):
    release = threading.Event()

    class BlockingStream:
        def __init__(self):
            self.close_calls = 0

        def read(self, _size):
            release.wait(2)
            return b""

        def close(self):
            self.close_calls += 1
            if not release.is_set():
                raise AssertionError("live reader pipe was closed")

    class ExitedParent:
        def __init__(self):
            self.stdout = BlockingStream()
            self.stderr = BlockingStream()
            self.returncode = 0

        def poll(self):
            return 0

    parent = ExitedParent()
    capture_root = tmp_path / "capture-root"
    capture_root.mkdir()
    monkeypatch.setattr(
        "backend.agent_eval.capture.tempfile.mkdtemp",
        lambda **_kwargs: str(capture_root),
    )
    started = time.monotonic()
    try:
        result = run_bounded_process(
            ["fake-parent"],
            cwd=tmp_path,
            env={},
            timeout=1,
            limits=CaptureLimits(cleanup_grace_seconds=0.05),
            launcher=lambda *_args, **_kwargs: parent,
        )
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert result.completed is False
        assert result.cleanup_complete is False
        assert result.cleanup_deferred is True
        assert result.errors == (
            "capture_cleanup_incomplete:task5_containment_required",
        )
        assert result.raw_stdout == b""
        assert result.raw_stderr == b""
        assert result.temporary_paths == ()
        assert parent.stdout.close_calls == 0
        assert parent.stderr.close_calls == 0
        assert capture_root.exists()
    finally:
        release.set()

    removal_deadline = time.monotonic() + 1
    while capture_root.exists() and time.monotonic() < removal_deadline:
        time.sleep(0.01)
    assert not capture_root.exists()
    assert not [
        warning
        for warning in recwarn
        if "Exception in thread agent-eval" in str(warning.message)
    ]


def test_bounded_process_retries_deferred_temp_deletion_without_returning_content(
    tmp_path, monkeypatch
):
    capture_root = tmp_path / "capture-root"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )
    real_rmtree = capture_module.shutil.rmtree
    attempts = 0

    def fail_once(path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("transient delete denial")
        return real_rmtree(path)

    monkeypatch.setattr(capture_module.shutil, "rmtree", fail_once)
    result = run_bounded_process(
        [sys.executable, "-c", "print('secret-output')"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(cleanup_grace_seconds=1),
    )

    assert result.completed is False
    assert result.cleanup_complete is False
    assert result.cleanup_deferred is True
    assert result.raw_stdout == b""
    assert result.stdout == ""
    assert result.temporary_paths == ()
    assert result.errors == ("temporary_capture_cleanup_incomplete",)
    removal_deadline = time.monotonic() + 1
    while capture_root.exists() and time.monotonic() < removal_deadline:
        time.sleep(0.01)
    assert attempts >= 2
    assert not capture_root.exists()


def test_deferred_cleanup_sanitizes_files_when_root_deletion_stays_denied(
    tmp_path, monkeypatch
):
    capture_root = tmp_path / "capture-root"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )
    attempts = 0

    def always_fail(_path):
        nonlocal attempts
        attempts += 1
        raise OSError("persistent delete denial")

    monkeypatch.setattr(capture_module.shutil, "rmtree", always_fail)
    result = run_bounded_process(
        [sys.executable, "-c", "print('secret-output')"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(cleanup_grace_seconds=1),
    )

    assert result.cleanup_complete is False
    assert result.cleanup_deferred is True
    assert result.raw_stdout == b""
    assert result.temporary_paths == ()
    sanitization_deadline = time.monotonic() + 1
    while time.monotonic() < sanitization_deadline:
        if attempts >= 6 and all(
            path.read_bytes() == b""
            for path in (
                capture_root / "stdout.bin",
                capture_root / "stderr.bin",
            )
        ):
            break
        time.sleep(0.01)
    assert attempts >= 6
    assert capture_root.exists()
    assert all(
        path.read_bytes() == b""
        for path in (
            capture_root / "stdout.bin",
            capture_root / "stderr.bin",
        )
    )


def test_deferred_cleanup_stays_owned_until_sanitize_and_delete_denials_lift(
    tmp_path, monkeypatch
):
    capture_root = tmp_path / "capture-root"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )
    real_open = Path.open
    real_rmtree = capture_module.shutil.rmtree
    denied = {"sanitize": True, "delete": True}
    attempts = {"sanitize": 0, "delete": 0}

    def controlled_open(path, mode="r", *args, **kwargs):
        if (
            denied["sanitize"]
            and Path(path).parent == capture_root
            and mode == "r+b"
        ):
            attempts["sanitize"] += 1
            raise PermissionError(f"sanitization denied: {capture_root}")
        return real_open(path, mode, *args, **kwargs)

    def controlled_rmtree(path):
        if denied["delete"] and Path(path) == capture_root:
            attempts["delete"] += 1
            raise PermissionError(f"deletion denied: {capture_root}")
        return real_rmtree(path)

    monkeypatch.setattr(Path, "open", controlled_open)
    monkeypatch.setattr(capture_module.shutil, "rmtree", controlled_rmtree)
    started = time.monotonic()
    result = run_bounded_process(
        [sys.executable, "-c", "print('sensitive-output')"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(cleanup_grace_seconds=0.1),
    )
    elapsed = time.monotonic() - started

    try:
        assert elapsed < 0.5
        assert result.cleanup_complete is False
        assert result.cleanup_deferred is True
        assert result.raw_stdout == b""
        assert result.temporary_paths == ()
        status = result.deferred_cleanup_status
        assert status is not None
        denial_deadline = time.monotonic() + 1
        snapshot = status.snapshot()
        while (
            snapshot["sanitize_attempts"] < 2
            or snapshot["delete_attempts"] < 2
        ) and time.monotonic() < denial_deadline:
            time.sleep(0.01)
            snapshot = status.snapshot()
        assert snapshot["active"] is True
        assert snapshot["sanitize_attempts"] >= 2
        assert snapshot["delete_attempts"] >= 2
        assert snapshot["last_error"]
        assert "capture-root" not in str(snapshot)
        assert (
            real_open(capture_root / "stdout.bin", "rb").read()
            == b"sensitive-output\r\n"
        )

        denied["sanitize"] = False
        denied["delete"] = False
        recovery_deadline = time.monotonic() + 1
        while capture_root.exists() and time.monotonic() < recovery_deadline:
            time.sleep(0.01)
        assert not capture_root.exists()
        recovered = status.snapshot()
        assert recovered["active"] is False
        assert recovered["state"] == "complete"
        assert recovered["sanitize_attempts"] > snapshot["sanitize_attempts"]
        assert recovered["delete_attempts"] > snapshot["delete_attempts"]
        assert recovered["last_error"] is None
    finally:
        denied["sanitize"] = False
        denied["delete"] = False
        if capture_root.exists():
            real_rmtree(capture_root)


def test_deferred_cleanup_recovers_from_unexpected_owner_exception(
    tmp_path, monkeypatch, recwarn
):
    capture_root = tmp_path / "capture-root"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )
    real_exists = Path.exists
    real_rmtree = capture_module.shutil.rmtree
    delete_calls = 0
    injected = False

    def fail_first_delete(path):
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            raise OSError("force deferred ownership")
        return real_rmtree(path)

    def fail_once_in_owner(path):
        nonlocal injected
        if (
            not injected
            and threading.current_thread().name
            == "agent-eval-deferred-cleanup"
            and Path(path) == capture_root
        ):
            injected = True
            raise RuntimeError(f"unexpected owner failure: {capture_root}")
        return real_exists(path)

    monkeypatch.setattr(capture_module.shutil, "rmtree", fail_first_delete)
    monkeypatch.setattr(Path, "exists", fail_once_in_owner)
    result = run_bounded_process(
        [sys.executable, "-c", "print('owned-secret')"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(cleanup_grace_seconds=0.1),
    )

    try:
        assert result.raw_stdout == b""
        assert result.temporary_paths == ()
        status = result.deferred_cleanup_status
        assert status is not None
        recovery_deadline = time.monotonic() + 1
        while real_exists(capture_root) and time.monotonic() < recovery_deadline:
            time.sleep(0.01)
        snapshot = status.snapshot()
        assert not real_exists(capture_root)
        assert snapshot["active"] is False
        assert snapshot["state"] == "complete"
        assert snapshot["supervisor_errors"] >= 1
        assert snapshot["last_error"] is None
        assert "capture-root" not in str(snapshot)
        assert not [
            warning
            for warning in recwarn
            if "agent-eval-deferred-cleanup" in str(warning.message)
        ]
    finally:
        if real_exists(capture_root):
            real_rmtree(capture_root)


def test_blocked_deferred_operation_uses_one_owner_thread_until_release(
    tmp_path, monkeypatch
):
    capture_root = tmp_path / "capture-root"
    capture_root.mkdir()
    monkeypatch.setattr(
        capture_module.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(capture_root),
    )
    real_open = Path.open
    real_rmtree = capture_module.shutil.rmtree
    release = threading.Event()
    operation_started = threading.Event()
    delete_calls = 0

    def block_sanitize(path, mode="r", *args, **kwargs):
        if Path(path).parent == capture_root and mode == "r+b":
            operation_started.set()
            release.wait(2)
        return real_open(path, mode, *args, **kwargs)

    def fail_then_block_delete(path):
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            raise OSError("force deferred ownership")
        operation_started.set()
        release.wait(2)
        return real_rmtree(path)

    monkeypatch.setattr(Path, "open", block_sanitize)
    monkeypatch.setattr(capture_module.shutil, "rmtree", fail_then_block_delete)
    started = time.monotonic()
    result = run_bounded_process(
        [sys.executable, "-c", "print('blocked-secret')"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(cleanup_grace_seconds=0.1),
    )
    elapsed = time.monotonic() - started

    def live_cleanup_threads():
        return [
            thread
            for thread in threading.enumerate()
            if thread.name
            in {"agent-eval-deferred-cleanup", "agent-eval-cleanup"}
        ]

    try:
        assert elapsed < 0.5
        assert result.raw_stdout == b""
        assert result.temporary_paths == ()
        assert operation_started.wait(0.5)
        counts = []
        for _sample in range(3):
            time.sleep(0.12)
            counts.append(len(live_cleanup_threads()))
        assert counts == [1, 1, 1]
        status = result.deferred_cleanup_status
        assert status is not None
        snapshot = status.snapshot()
        assert snapshot["active"] is True
        assert snapshot["state"] in {"sanitizing", "deleting"}
    finally:
        release.set()

    recovery_deadline = time.monotonic() + 1
    while (
        capture_root.exists() or live_cleanup_threads()
    ) and time.monotonic() < recovery_deadline:
        time.sleep(0.01)
    assert not capture_root.exists()
    assert live_cleanup_threads() == []
    assert result.deferred_cleanup_status.snapshot()["state"] == "complete"


def test_bounded_process_never_uses_unbounded_wait_when_termination_is_ineffective(
    tmp_path,
):
    class IneffectiveProcess:
        def __init__(self):
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.returncode = None
            self.wait_timeouts = []

        def poll(self):
            return None

        def terminate_tree(self):
            return None

        def kill_tree(self):
            return None

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            if timeout is None:
                time.sleep(0.5)
            raise subprocess.TimeoutExpired("fake", timeout)

    process = IneffectiveProcess()
    started = time.monotonic()
    result = run_bounded_process(
        ["fake-child"],
        cwd=tmp_path,
        env={},
        timeout=0.01,
        limits=CaptureLimits(cleanup_grace_seconds=0.05),
        launcher=lambda *_args, **_kwargs: process,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert result.cleanup_complete is False
    assert result.errors == (
        "timeout:0.01s",
        "process_cleanup_incomplete",
    )
    assert process.wait_timeouts
    assert all(value is not None for value in process.wait_timeouts)


def test_verified_windows_launcher_refuses_non_job_result(tmp_path):
    class OrdinaryProcess:
        def __init__(self):
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.returncode = None
            self.terminate_calls = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminate_calls += 1
            self.returncode = 1

        def kill(self):
            self.returncode = 1

        def wait(self, timeout=None):
            assert timeout is not None
            return self.returncode

    ordinary = OrdinaryProcess()

    def launcher(*_args, **_kwargs):
        return ordinary

    launcher._windows_job_launcher = True
    with pytest.raises(RuntimeError, match="real WindowsJobProcess"):
        run_bounded_process(
            ["ordinary.exe"],
            cwd=tmp_path,
            env={},
            timeout=1,
            limits=CaptureLimits(cleanup_grace_seconds=0.1),
            launcher=launcher,
        )
    assert ordinary.terminate_calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
@pytest.mark.parametrize("failure_mode", ["timeout", "overflow"])
def test_windows_job_timeout_and_overflow_prove_zero_active_processes(
    tmp_path, failure_mode
):
    if failure_mode == "timeout":
        script = "import time; time.sleep(30)"
        timeout = 0.05
        limits = CaptureLimits(
            stdout_bytes=128,
            stderr_bytes=128,
            cleanup_grace_seconds=2,
        )
    else:
        script = (
            "import sys,time; "
            "sys.stdout.buffer.write(b'x'*4096); "
            "sys.stdout.buffer.flush(); time.sleep(30)"
        )
        timeout = 10
        limits = CaptureLimits(
            stdout_bytes=128,
            stderr_bytes=128,
            cleanup_grace_seconds=2,
        )

    result = run_bounded_process(
        [getattr(sys, "_base_executable", sys.executable), "-c", script],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout=timeout,
        limits=limits,
        launcher=WindowsJobProcess.start,
    )

    assert result.completed is False
    assert result.timed_out is (failure_mode == "timeout")
    assert result.overflowed is (failure_mode == "overflow")
    assert result.cleanup_complete is True
    assert result.containment["final_active_processes"] == 0
    assert result.containment["handles_closed"] is True
    assert result.containment["cleanup_certain"] is True
