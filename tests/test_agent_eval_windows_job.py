import ctypes
import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import backend.agent_eval.windows_job as windows_job_module
from backend.agent_eval.windows_job import (
    WindowsJobError,
    WindowsJobLimits,
    WindowsJobProcess,
)


class FakeWin32Api:
    is_real = False

    def __init__(self):
        self.calls = []
        self.fail = {}
        self.fail_once = {}
        self.next_handle = 100
        self.active = 0
        self.wait_result = "signaled"
        self.exit_code = 0
        self.resume_result = 1
        self.command_line = None
        self.environment_block = None

    def _raise(self, name):
        error = self.fail.get(name)
        if error is not None:
            raise error

    def _handle(self):
        value = self.next_handle
        self.next_handle += 1
        return value

    def create_job(self):
        self.calls.append(("create_job",))
        self._raise("create_job")
        return self._handle()

    def set_limits(
        self,
        job_handle,
        *,
        active_processes,
        memory_bytes,
        kill_on_close,
    ):
        self.calls.append(("set_limits", active_processes, kill_on_close))
        self._raise("set_limits")

    def create_process_suspended(
        self,
        executable,
        command_line,
        *,
        cwd,
        environment_block,
    ):
        self.calls.append(("create_process_suspended", executable))
        self._raise("create_process_suspended")
        self.command_line = command_line
        self.environment_block = environment_block
        return SimpleNamespace(
            process_handle=self._handle(),
            thread_handle=self._handle(),
            pid=1234,
            stdout=io.BytesIO(b""),
            stderr=io.BytesIO(b""),
        )

    def assign_process(self, job_handle, process_handle):
        self.calls.append(("assign_process",))
        self._raise("assign_process")

    def resume_thread(self, thread_handle):
        self.calls.append(("resume_thread",))
        self._raise("resume_thread")
        return self.resume_result

    def terminate_process(self, process_handle, exit_code):
        self.calls.append(("terminate_process", exit_code))
        self._raise("terminate_process")

    def terminate_job(self, job_handle, exit_code):
        self.calls.append(("terminate_job", exit_code))
        self._raise("terminate_job")

    def query_active_processes(self, job_handle):
        self.calls.append(("query_active_processes",))
        self._raise("query_active_processes")
        return self.active

    def wait_for_process(self, process_handle, timeout_ms):
        self.calls.append(("wait_for_process", timeout_ms))
        self._raise("wait_for_process")
        return self.wait_result

    def get_exit_code_process(self, process_handle):
        self.calls.append(("get_exit_code_process",))
        self._raise("get_exit_code_process")
        return self.exit_code

    def close_handle(self, handle):
        self.calls.append(("close_handle", handle))
        error = self.fail_once.pop(f"close_handle:{handle}", None)
        if error is not None:
            raise error
        self._raise(f"close_handle:{handle}")
        self._raise("close_handle")


@pytest.fixture
def fake_win32():
    return FakeWin32Api()


def fake_started_job(fake_win32):
    return WindowsJobProcess.start(
        ["opencode.exe", "--version"],
        cwd=r"C:\work",
        env={"PATH": r"C:\bin"},
        limits=WindowsJobLimits(active_processes=1),
        api=fake_win32,
    )


def test_process_is_created_suspended_assigned_then_resumed(fake_win32):
    process = fake_started_job(fake_win32)

    assert fake_win32.calls[:4] == [
        ("create_job",),
        ("set_limits", 1, True),
        ("create_process_suspended", "opencode.exe"),
        ("assign_process",),
    ]
    assert fake_win32.calls[4] == ("resume_thread",)
    process.close()


def test_command_line_and_environment_are_canonical(fake_win32):
    process = WindowsJobProcess.start(
        ["program.exe", "argument with spaces", '"quoted"'],
        cwd=r"C:\work",
        env={"z": "last", "A": "first"},
        limits=WindowsJobLimits(),
        api=fake_win32,
    )

    assert fake_win32.command_line == subprocess.list2cmdline(
        ["program.exe", "argument with spaces", '"quoted"']
    )
    assert fake_win32.environment_block == "A=first\0z=last\0\0"
    process.close()


def test_empty_environment_is_double_nul_terminated(fake_win32):
    process = WindowsJobProcess.start(
        ["program.exe"],
        cwd=r"C:\work",
        env={},
        api=fake_win32,
    )

    assert fake_win32.environment_block == "\0\0"
    process.close()


@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_batch_targets_are_refused_before_any_kernel_call(fake_win32, suffix):
    with pytest.raises(WindowsJobError, match="native executable"):
        WindowsJobProcess.start(
            [f"opencode{suffix}"],
            cwd=r"C:\work",
            env={},
            api=fake_win32,
        )
    assert fake_win32.calls == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"active_processes": True}, "active_processes"),
        ({"active_processes": 0}, "active_processes"),
        ({"active_processes": 1.5}, "active_processes"),
        ({"memory_bytes": True}, "memory_bytes"),
        ({"memory_bytes": 0}, "memory_bytes"),
        ({"memory_bytes": -1}, "memory_bytes"),
    ],
)
def test_limits_reject_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        WindowsJobLimits(**kwargs)


@pytest.mark.parametrize(
    ("failure", "expected_cleanup"),
    [
        ("create_job", []),
        ("set_limits", ["close_handle"]),
        ("create_process_suspended", ["close_handle"]),
        (
            "assign_process",
            [
                "terminate_process",
                "close_handle",
                "close_handle",
                "close_handle",
            ],
        ),
        (
            "resume_thread",
            [
                "terminate_job",
                "close_handle",
                "close_handle",
                "close_handle",
            ],
        ),
    ],
)
def test_start_failures_clean_up_owned_resources(
    fake_win32, failure, expected_cleanup
):
    fake_win32.fail[failure] = OSError(f"{failure} denied")

    with pytest.raises(WindowsJobError, match=failure):
        fake_started_job(fake_win32)

    failure_index = next(
        index
        for index, call in enumerate(fake_win32.calls)
        if call[0] == failure
    )
    assert [
        call[0] for call in fake_win32.calls[failure_index + 1 :]
    ] == expected_cleanup


def test_nested_job_assignment_denial_fails_closed_without_fallback(fake_win32):
    fake_win32.fail["assign_process"] = OSError("already in a non-breakaway job")

    with pytest.raises(WindowsJobError, match="assign_process"):
        fake_started_job(fake_win32)

    names = [call[0] for call in fake_win32.calls]
    assert names.count("create_process_suspended") == 1
    assert names.count("terminate_process") == 1
    assert "resume_thread" not in names


@pytest.mark.parametrize("previous_suspend_count", [0, 2])
def test_resume_requires_exact_previous_suspend_count(
    fake_win32, previous_suspend_count
):
    fake_win32.resume_result = previous_suspend_count

    with pytest.raises(WindowsJobError, match="resume_thread"):
        fake_started_job(fake_win32)

    assert ("terminate_job", 1) in fake_win32.calls


def test_thread_handle_ownership_survives_transient_close_failure(fake_win32):
    thread_handle = 102
    fake_win32.fail_once[f"close_handle:{thread_handle}"] = OSError(
        "transient close failure"
    )

    with pytest.raises(WindowsJobError, match="close_handle"):
        fake_started_job(fake_win32)

    assert fake_win32.calls.count(("close_handle", thread_handle)) == 2


def test_close_kills_job_and_reports_zero_active_processes(fake_win32):
    process = fake_started_job(fake_win32)
    process.terminate(124)
    process.close()

    assert ("terminate_job", 124) in fake_win32.calls
    assert process.active_processes() == 0


def test_terminate_refuses_nonzero_final_active_count(fake_win32):
    process = fake_started_job(fake_win32)
    fake_win32.active = 2

    with pytest.raises(WindowsJobError, match="active processes"):
        process.terminate(124)

    with pytest.raises(WindowsJobError, match="active processes"):
        process.close()


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("query_active_processes", "query_active_processes"),
        ("wait_for_process", "wait_for_process"),
        ("get_exit_code_process", "get_exit_code_process"),
    ],
)
def test_query_wait_and_exit_failures_are_not_success(
    fake_win32, method, message
):
    process = fake_started_job(fake_win32)
    fake_win32.fail[method] = OSError("sentinel failure")

    with pytest.raises(WindowsJobError, match=message):
        process.terminate(124)

    with pytest.raises(WindowsJobError):
        process.close()


def test_wait_timeout_is_bounded_and_not_completion(fake_win32):
    process = fake_started_job(fake_win32)
    fake_win32.wait_result = "timeout"

    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=0.01)

    wait_call = next(
        call for call in fake_win32.calls if call[0] == "wait_for_process"
    )
    assert 0 <= wait_call[1] <= 10
    fake_win32.wait_result = "signaled"
    process.close()


def test_close_is_idempotent_and_handles_are_closed_exactly_once(fake_win32):
    process = fake_started_job(fake_win32)
    process.close()
    calls_after_first_close = list(fake_win32.calls)

    process.close()

    assert fake_win32.calls == calls_after_first_close
    assert process.stdout.closed is True
    assert process.stderr.closed is True
    closed = [call[1] for call in fake_win32.calls if call[0] == "close_handle"]
    assert len(closed) == len(set(closed)) == 3


def test_close_failure_remains_truthful_on_repeated_close(fake_win32):
    process = fake_started_job(fake_win32)
    process_handle = process._process_handle
    fake_win32.fail[f"close_handle:{process_handle}"] = OSError("close denied")

    with pytest.raises(WindowsJobError, match="close_handle"):
        process.close()
    with pytest.raises(WindowsJobError, match="close_handle"):
        process.close()

    assert fake_win32.calls.count(("close_handle", process_handle)) == 2
    assert process._process_handle == process_handle
    assert process._closed is False


def test_transient_process_handle_close_is_retried_without_reclosing_job(
    fake_win32,
):
    process = fake_started_job(fake_win32)
    process_handle = process._process_handle
    job_handle = process._job_handle
    fake_win32.fail_once[f"close_handle:{process_handle}"] = OSError(
        "transient process close failure"
    )

    with pytest.raises(WindowsJobError, match="close_handle"):
        process.close()
    assert process._process_handle == process_handle
    assert process._job_handle is None
    assert process._closed is False

    process.close()
    assert process._process_handle is None
    assert process._job_handle is None
    assert process._closed is True
    assert fake_win32.calls.count(("close_handle", process_handle)) == 2
    assert fake_win32.calls.count(("close_handle", job_handle)) == 1


def test_transient_job_handle_close_is_retried_without_reclosing_process(
    fake_win32,
):
    process = fake_started_job(fake_win32)
    process_handle = process._process_handle
    job_handle = process._job_handle
    fake_win32.fail_once[f"close_handle:{job_handle}"] = OSError(
        "transient job close failure"
    )

    with pytest.raises(WindowsJobError, match="close_handle"):
        process.close()
    assert process._process_handle is None
    assert process._job_handle == job_handle
    assert process._closed is False

    process.close()
    assert process._process_handle is None
    assert process._job_handle is None
    assert process._closed is True
    assert fake_win32.calls.count(("close_handle", process_handle)) == 1
    assert fake_win32.calls.count(("close_handle", job_handle)) == 2


def test_transient_termination_uncertainty_is_never_converted_to_close_success(
    fake_win32,
):
    process = fake_started_job(fake_win32)
    fake_win32.fail["wait_for_process"] = OSError("transient wait failure")
    with pytest.raises(WindowsJobError, match="wait_for_process"):
        process.terminate(124)
    del fake_win32.fail["wait_for_process"]

    with pytest.raises(WindowsJobError, match="wait_for_process"):
        process.close()
    calls_after_first_close = list(fake_win32.calls)
    with pytest.raises(WindowsJobError, match="wait_for_process"):
        process.close()
    assert fake_win32.calls == calls_after_first_close


def test_containment_evidence_is_measured_not_caller_supplied(fake_win32):
    process = fake_started_job(fake_win32)
    process.close()

    assert process.containment_evidence() == {
        "real_windows_job": False,
        "assignment_proven": True,
        "active_process_limit": 1,
        "memory_limit_bytes": None,
        "kill_on_close": True,
        "resume_after_assignment": True,
        "final_active_processes": 0,
        "handles_closed": True,
        "cleanup_certain": True,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_kernel32_bindings_are_exact_and_have_explicit_signatures():
    api = windows_job_module._Kernel32Api()
    w = windows_job_module.wintypes
    expected = {
        "_create_job": ((ctypes.c_void_p, w.LPCWSTR), w.HANDLE),
        "_set_information": (
            (w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD),
            w.BOOL,
        ),
        "_create_process": (
            (
                w.LPCWSTR,
                w.LPWSTR,
                ctypes.c_void_p,
                ctypes.c_void_p,
                w.BOOL,
                w.DWORD,
                ctypes.c_void_p,
                w.LPCWSTR,
                ctypes.POINTER(windows_job_module.STARTUPINFOW),
                ctypes.POINTER(windows_job_module.PROCESS_INFORMATION),
            ),
            w.BOOL,
        ),
        "_initialize_attributes": (
            (
                ctypes.c_void_p,
                w.DWORD,
                w.DWORD,
                ctypes.POINTER(windows_job_module.SIZE_T),
            ),
            w.BOOL,
        ),
        "_update_attribute": (
            (
                ctypes.c_void_p,
                w.DWORD,
                windows_job_module.ULONG_PTR,
                ctypes.c_void_p,
                windows_job_module.SIZE_T,
                ctypes.c_void_p,
                ctypes.POINTER(windows_job_module.SIZE_T),
            ),
            w.BOOL,
        ),
        "_delete_attributes": ((ctypes.c_void_p,), None),
        "_assign_process": ((w.HANDLE, w.HANDLE), w.BOOL),
        "_resume_thread": ((w.HANDLE,), w.DWORD),
        "_terminate_job": ((w.HANDLE, w.UINT), w.BOOL),
        "_query_information": (
            (
                w.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                w.DWORD,
                ctypes.POINTER(w.DWORD),
            ),
            w.BOOL,
        ),
        "_wait": ((w.HANDLE, w.DWORD), w.DWORD),
        "_get_exit_code": (
            (w.HANDLE, ctypes.POINTER(w.DWORD)),
            w.BOOL,
        ),
        "_close_handle": ((w.HANDLE,), w.BOOL),
    }

    assert set(vars(api)) == set(expected)
    for name, (argument_types, return_type) in expected.items():
        function = getattr(api, name)
        assert function.argtypes == argument_types
        assert function.restype is return_type


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_query_rejects_zero_return_length(monkeypatch):
    api = windows_job_module._Kernel32Api()

    def zero_length(_job, _kind, buffer, _size, returned):
        accounting = windows_job_module.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
        ctypes.cast(buffer, ctypes.POINTER(accounting)).contents.ActiveProcesses = 0
        ctypes.cast(
            returned,
            ctypes.POINTER(windows_job_module.wintypes.DWORD),
        ).contents.value = 0
        return True

    monkeypatch.setattr(api, "_query_information", zero_length)
    with pytest.raises(OSError, match="invalid size"):
        api.query_active_processes(1)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_attribute_update_failure_deletes_initialized_list(
    monkeypatch, tmp_path
):
    api = windows_job_module._Kernel32Api()
    deleted = []
    monkeypatch.setattr(api, "_update_attribute", lambda *_args: False)
    monkeypatch.setattr(
        api,
        "_delete_attributes",
        lambda pointer: deleted.append(pointer),
    )

    with pytest.raises(OSError, match="UpdateProcThreadAttribute"):
        api.create_process_suspended(
            sys._base_executable,
            subprocess.list2cmdline([sys._base_executable, "-c", "pass"]),
            cwd=str(tmp_path),
            environment_block="\0\0",
        )
    assert len(deleted) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_internal_preassignment_cleanup_uncertainty_is_reported(
    monkeypatch, tmp_path
):
    api = windows_job_module._Kernel32Api()

    def fake_create(
        _application,
        _command,
        _process_security,
        _thread_security,
        _inherit,
        _flags,
        _environment,
        _cwd,
        _startup,
        process_information,
    ):
        information = ctypes.cast(
            process_information,
            ctypes.POINTER(windows_job_module.PROCESS_INFORMATION),
        ).contents
        information.hProcess = 0xDEAD
        information.hThread = 0xBEEF
        information.dwProcessId = 1234
        return True

    monkeypatch.setattr(api, "_create_process", fake_create)
    monkeypatch.setattr(
        api,
        "terminate_process",
        lambda *_args: (_ for _ in ()).throw(OSError("terminate denied")),
    )
    monkeypatch.setattr(api, "close_handle", lambda _handle: None)
    monkeypatch.setattr(
        windows_job_module.os,
        "fdopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("stream wrapping denied")
        ),
    )

    with pytest.raises(
        WindowsJobError,
        match="cleanup uncertain.*terminate_process",
    ):
        api.create_process_suspended(
            sys._base_executable,
            subprocess.list2cmdline([sys._base_executable, "-c", "pass"]),
            cwd=str(tmp_path),
            environment_block="\0\0",
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_active_process_limit_denies_child_process(tmp_path):
    child = tmp_path / "attempt_child.py"
    child.write_text(
        "import subprocess,sys\n"
        "try:\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "except OSError:\n"
        "    raise SystemExit(23)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    python_executable = getattr(sys, "_base_executable", sys.executable)
    job = WindowsJobProcess.start(
        [python_executable, str(child)],
        cwd=tmp_path,
        env=dict(os.environ),
        limits=WindowsJobLimits(active_processes=1),
    )
    exit_code = job.wait(timeout=10)
    job.close()

    assert exit_code == 23
    assert job.active_processes() == 0
