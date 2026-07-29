"""Fail-closed Windows Job Object process-tree containment."""
from __future__ import annotations

import ctypes
import math
import os
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
STILL_ACTIVE = 259
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_TERMINATION_WAIT_MS = 5_000

ULONG_PTR = ctypes.c_size_t
SIZE_T = ctypes.c_size_t
LARGE_INTEGER = ctypes.c_longlong


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", LARGE_INTEGER),
        ("PerJobUserTimeLimit", LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ULONG_PTR),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", LARGE_INTEGER),
        ("TotalKernelTime", LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class WindowsJobError(RuntimeError):
    """A Job Object lifecycle result was failed or uncertain."""


@dataclass(frozen=True)
class WindowsJobLimits:
    active_processes: int = 1
    memory_bytes: int | None = None

    def __post_init__(self) -> None:
        if type(self.active_processes) is not int or self.active_processes <= 0:
            raise ValueError("active_processes must be a positive integer")
        if (
            self.memory_bytes is not None
            and (
                type(self.memory_bytes) is not int
                or self.memory_bytes <= 0
            )
        ):
            raise ValueError("memory_bytes must be a positive integer or None")


@dataclass
class _CreatedProcess:
    process_handle: int
    thread_handle: int
    pid: int
    stdout: BinaryIO
    stderr: BinaryIO


def _handle_value(handle: object) -> int:
    value = getattr(handle, "value", handle)
    if not value:
        raise OSError(ctypes.get_last_error(), "Win32 returned a null handle")
    return int(value)


class _Kernel32Api:
    """Small injectable wrapper around only the Job lifecycle APIs we require."""

    is_real = True

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects require Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._create_job = kernel32.CreateJobObjectW
        self._create_job.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self._create_job.restype = wintypes.HANDLE

        self._set_information = kernel32.SetInformationJobObject
        self._set_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self._set_information.restype = wintypes.BOOL

        self._create_process = kernel32.CreateProcessW
        self._create_process.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        )
        self._create_process.restype = wintypes.BOOL

        self._initialize_attributes = (
            kernel32.InitializeProcThreadAttributeList
        )
        self._initialize_attributes.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(SIZE_T),
        )
        self._initialize_attributes.restype = wintypes.BOOL

        self._update_attribute = kernel32.UpdateProcThreadAttribute
        self._update_attribute.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            ULONG_PTR,
            ctypes.c_void_p,
            SIZE_T,
            ctypes.c_void_p,
            ctypes.POINTER(SIZE_T),
        )
        self._update_attribute.restype = wintypes.BOOL

        self._delete_attributes = (
            kernel32.DeleteProcThreadAttributeList
        )
        self._delete_attributes.argtypes = (ctypes.c_void_p,)
        self._delete_attributes.restype = None

        self._assign_process = kernel32.AssignProcessToJobObject
        self._assign_process.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self._assign_process.restype = wintypes.BOOL

        self._resume_thread = kernel32.ResumeThread
        self._resume_thread.argtypes = (wintypes.HANDLE,)
        self._resume_thread.restype = wintypes.DWORD

        self._terminate_job = kernel32.TerminateJobObject
        self._terminate_job.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self._terminate_job.restype = wintypes.BOOL

        self._query_information = kernel32.QueryInformationJobObject
        self._query_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._query_information.restype = wintypes.BOOL

        self._wait = kernel32.WaitForSingleObject
        self._wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._wait.restype = wintypes.DWORD

        self._get_exit_code = kernel32.GetExitCodeProcess
        self._get_exit_code.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._get_exit_code.restype = wintypes.BOOL

        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (wintypes.HANDLE,)
        self._close_handle.restype = wintypes.BOOL

    @staticmethod
    def _error(operation: str) -> OSError:
        return OSError(ctypes.get_last_error(), f"{operation} failed")

    def create_job(self) -> int:
        return _handle_value(self._create_job(None, None))

    def set_limits(
        self,
        job_handle: int,
        *,
        active_processes: int,
        memory_bytes: int | None,
        kill_on_close: bool,
    ) -> None:
        information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        if kill_on_close:
            flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_bytes is not None:
            flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            information.JobMemoryLimit = memory_bytes
        information.BasicLimitInformation.LimitFlags = flags
        information.BasicLimitInformation.ActiveProcessLimit = active_processes
        if not self._set_information(
            job_handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise self._error("SetInformationJobObject")

    def create_process_suspended(
        self,
        executable: str,
        command_line: str,
        *,
        cwd: str,
        environment_block: str | None,
    ) -> _CreatedProcess:
        stdin_fd = os.open(os.devnull, os.O_RDONLY)
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        opened = [
            stdin_fd,
            stdout_read,
            stdout_write,
            stderr_read,
            stderr_write,
        ]
        stdout_stream: BinaryIO | None = None
        stderr_stream: BinaryIO | None = None
        process_info = PROCESS_INFORMATION()
        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        attribute_buffer = None
        attributes_initialized = False
        try:
            import msvcrt

            os.set_handle_inheritable(
                msvcrt.get_osfhandle(stdin_fd),
                True,
            )
            os.set_handle_inheritable(
                msvcrt.get_osfhandle(stdout_write),
                True,
            )
            os.set_handle_inheritable(
                msvcrt.get_osfhandle(stderr_write),
                True,
            )
            os.set_handle_inheritable(
                msvcrt.get_osfhandle(stdout_read),
                False,
            )
            os.set_handle_inheritable(
                msvcrt.get_osfhandle(stderr_read),
                False,
            )
            std_handles = (
                msvcrt.get_osfhandle(stdin_fd),
                msvcrt.get_osfhandle(stdout_write),
                msvcrt.get_osfhandle(stderr_write),
            )
            startup.StartupInfo.hStdInput = std_handles[0]
            startup.StartupInfo.hStdOutput = std_handles[1]
            startup.StartupInfo.hStdError = std_handles[2]
            attribute_size = SIZE_T()
            ctypes.set_last_error(0)
            sizing_succeeded = self._initialize_attributes(
                None,
                1,
                0,
                ctypes.byref(attribute_size),
            )
            if (
                sizing_succeeded
                or ctypes.get_last_error() != 122
                or attribute_size.value <= 0
            ):
                raise self._error(
                    "InitializeProcThreadAttributeList(size)"
                )
            attribute_buffer = ctypes.create_string_buffer(
                attribute_size.value
            )
            startup.lpAttributeList = ctypes.cast(
                attribute_buffer,
                ctypes.c_void_p,
            )
            if not self._initialize_attributes(
                startup.lpAttributeList,
                1,
                0,
                ctypes.byref(attribute_size),
            ):
                raise self._error("InitializeProcThreadAttributeList")
            attributes_initialized = True
            inherited_handles = (wintypes.HANDLE * len(std_handles))(
                *std_handles
            )
            if not self._update_attribute(
                startup.lpAttributeList,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(inherited_handles, ctypes.c_void_p),
                ctypes.sizeof(inherited_handles),
                None,
                None,
            ):
                raise self._error("UpdateProcThreadAttribute")
            mutable_command = ctypes.create_unicode_buffer(command_line)
            environment = (
                None
                if environment_block is None
                else ctypes.create_unicode_buffer(environment_block)
            )
            flags = (
                CREATE_SUSPENDED
                | CREATE_NO_WINDOW
                | CREATE_UNICODE_ENVIRONMENT
                | EXTENDED_STARTUPINFO_PRESENT
            )
            if not self._create_process(
                executable,
                mutable_command,
                None,
                None,
                True,
                flags,
                environment,
                cwd,
                ctypes.cast(
                    ctypes.byref(startup),
                    ctypes.POINTER(STARTUPINFOW),
                ),
                ctypes.byref(process_info),
            ):
                raise self._error("CreateProcessW")
            process_handle = _handle_value(process_info.hProcess)
            thread_handle = _handle_value(process_info.hThread)
            for fd in (stdin_fd, stdout_write, stderr_write):
                os.close(fd)
                opened.remove(fd)
            stdout_stream = os.fdopen(stdout_read, "rb", buffering=0)
            opened.remove(stdout_read)
            stderr_stream = os.fdopen(stderr_read, "rb", buffering=0)
            opened.remove(stderr_read)
            return _CreatedProcess(
                process_handle,
                thread_handle,
                int(process_info.dwProcessId),
                stdout_stream,
                stderr_stream,
            )
        except BaseException as original_error:
            cleanup_errors: list[str] = []
            for stream in (stdout_stream, stderr_stream):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError as exc:
                        cleanup_errors.append(f"close_stream: {exc}")
            for fd in opened:
                try:
                    os.close(fd)
                except OSError as exc:
                    cleanup_errors.append(f"close_fd: {exc}")
            if process_info.hProcess:
                try:
                    self.terminate_process(
                        _handle_value(process_info.hProcess),
                        1,
                    )
                except OSError as exc:
                    cleanup_errors.append(f"terminate_process: {exc}")
            if process_info.hThread:
                try:
                    self.close_handle(_handle_value(process_info.hThread))
                except OSError as exc:
                    cleanup_errors.append(f"close_thread: {exc}")
            if process_info.hProcess:
                try:
                    self.close_handle(_handle_value(process_info.hProcess))
                except OSError as exc:
                    cleanup_errors.append(f"close_process: {exc}")
            if cleanup_errors:
                raise WindowsJobError(
                    f"{original_error}; cleanup uncertain: "
                    + "; ".join(cleanup_errors)
                ) from original_error
            raise
        finally:
            if attributes_initialized:
                self._delete_attributes(startup.lpAttributeList)

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        if not self._assign_process(job_handle, process_handle):
            raise self._error("AssignProcessToJobObject")

    def resume_thread(self, thread_handle: int) -> int:
        result = int(self._resume_thread(thread_handle))
        if result == 0xFFFFFFFF:
            raise self._error("ResumeThread")
        return result

    def terminate_process(self, process_handle: int, exit_code: int) -> None:
        import _winapi

        _winapi.TerminateProcess(process_handle, exit_code)

    def terminate_job(self, job_handle: int, exit_code: int) -> None:
        if not self._terminate_job(job_handle, exit_code):
            raise self._error("TerminateJobObject")

    def query_active_processes(self, job_handle: int) -> int:
        information = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        returned = wintypes.DWORD()
        if not self._query_information(
            job_handle,
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned),
        ):
            raise self._error("QueryInformationJobObject")
        if returned.value != ctypes.sizeof(information):
            raise OSError("QueryInformationJobObject returned an invalid size")
        return int(information.ActiveProcesses)

    def wait_for_process(self, process_handle: int, timeout_ms: int) -> str:
        result = int(self._wait(process_handle, timeout_ms))
        if result == WAIT_OBJECT_0:
            return "signaled"
        if result == WAIT_TIMEOUT:
            return "timeout"
        if result == WAIT_FAILED:
            raise self._error("WaitForSingleObject")
        raise OSError(f"WaitForSingleObject returned unexpected value {result}")

    def get_exit_code_process(self, process_handle: int) -> int:
        value = wintypes.DWORD()
        if not self._get_exit_code(process_handle, ctypes.byref(value)):
            raise self._error("GetExitCodeProcess")
        return int(value.value)

    def close_handle(self, handle: int) -> None:
        if not self._close_handle(handle):
            raise self._error("CloseHandle")


def _environment_block(env: Mapping[str, str] | None) -> str | None:
    if env is None:
        return None
    entries: list[str] = []
    for key in sorted(env):
        value = env[key]
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("environment keys and values must be strings")
        if not key or "\0" in key or "=" in key or "\0" in value:
            raise ValueError("environment contains an invalid key or value")
        entries.append(f"{key}={value}\0")
    return "".join(entries) + ("\0" if entries else "\0\0")


def _call(operation: str, function):
    try:
        return function()
    except WindowsJobError:
        raise
    except BaseException as exc:
        raise WindowsJobError(f"{operation} failed: {exc}") from exc


class WindowsJobProcess:
    """Popen-compatible process whose entire tree is owned by one Job Object."""

    def __init__(
        self,
        *,
        api: object,
        argv: Sequence[str],
        limits: WindowsJobLimits,
        job_handle: int,
        created: _CreatedProcess,
    ) -> None:
        self._api = api
        self._argv = tuple(argv)
        self._limits = limits
        self._job_handle: int | None = job_handle
        self._process_handle: int | None = created.process_handle
        self.pid = created.pid
        self.stdout = created.stdout
        self.stderr = created.stderr
        self.returncode: int | None = None
        self._assignment_proven = True
        self._resume_after_assignment = True
        self._final_active_processes: int | None = None
        self._terminated = False
        self._closed = False
        self._cleanup_error: WindowsJobError | None = None
        self._close_error: WindowsJobError | None = None

    @classmethod
    def start(
        cls,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        env: Mapping[str, str] | None,
        limits: WindowsJobLimits = WindowsJobLimits(),
        api: object | None = None,
        stdin: object = subprocess.DEVNULL,
        stdout: object = subprocess.PIPE,
        stderr: object = subprocess.PIPE,
        text: bool = False,
    ) -> "WindowsJobProcess":
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("argv must contain non-empty strings")
        if Path(argv[0]).suffix.lower() in {".cmd", ".bat"}:
            raise WindowsJobError(
                "Windows Job launcher requires a provenance-verified native "
                "executable; .cmd and .bat targets are refused"
            )
        if (
            stdin is not subprocess.DEVNULL
            or stdout is not subprocess.PIPE
            or stderr is not subprocess.PIPE
            or text is not False
        ):
            raise ValueError("Windows Job launcher requires binary capture pipes")
        if not isinstance(limits, WindowsJobLimits):
            raise TypeError("limits must be WindowsJobLimits")
        selected_api = api if api is not None else _Kernel32Api()
        job_handle: int | None = None
        created: _CreatedProcess | None = None
        assigned = False
        thread_owned = False
        operation = "create_job"
        try:
            job_handle = _call(
                operation,
                selected_api.create_job,
            )
            operation = "set_limits"
            _call(
                operation,
                lambda: selected_api.set_limits(
                    job_handle,
                    active_processes=limits.active_processes,
                    memory_bytes=limits.memory_bytes,
                    kill_on_close=True,
                ),
            )
            operation = "create_process_suspended"
            command_line = subprocess.list2cmdline(list(argv))
            created = _call(
                operation,
                lambda: selected_api.create_process_suspended(
                    argv[0],
                    command_line,
                    cwd=str(cwd),
                    environment_block=_environment_block(env),
                ),
            )
            thread_owned = True
            operation = "assign_process"
            _call(
                operation,
                lambda: selected_api.assign_process(
                    job_handle,
                    created.process_handle,
                ),
            )
            assigned = True
            operation = "resume_thread"
            previous_suspend_count = _call(
                operation,
                lambda: selected_api.resume_thread(created.thread_handle),
            )
            if (
                type(previous_suspend_count) is not int
                or previous_suspend_count != 1
            ):
                raise WindowsJobError(
                    "resume_thread did not return the expected previous "
                    "suspend count of 1"
                )
            operation = "close_handle"
            _call(
                operation,
                lambda: selected_api.close_handle(created.thread_handle),
            )
            thread_owned = False
            return cls(
                api=selected_api,
                argv=argv,
                limits=limits,
                job_handle=job_handle,
                created=created,
            )
        except BaseException as exc:
            cleanup_errors: list[str] = []

            def cleanup(name: str, function) -> None:
                try:
                    function()
                except BaseException as cleanup_exc:
                    cleanup_errors.append(f"{name}: {cleanup_exc}")

            if created is not None:
                if assigned and job_handle is not None:
                    cleanup(
                        "terminate_job",
                        lambda: selected_api.terminate_job(job_handle, 1),
                    )
                else:
                    cleanup(
                        "terminate_process",
                        lambda: selected_api.terminate_process(
                            created.process_handle,
                            1,
                        ),
                    )
                if thread_owned:
                    thread_owned = False
                    cleanup(
                        "close_handle",
                        lambda: selected_api.close_handle(
                            created.thread_handle
                        ),
                    )
                cleanup(
                    "close_handle",
                    lambda: selected_api.close_handle(
                        created.process_handle
                    ),
                )
                for stream in (created.stdout, created.stderr):
                    cleanup("close_stream", stream.close)
            if job_handle is not None:
                cleanup(
                    "close_handle",
                    lambda: selected_api.close_handle(job_handle),
                )
            message = str(exc)
            if not isinstance(exc, WindowsJobError):
                message = f"{operation} failed: {exc}"
            if cleanup_errors:
                message += "; cleanup uncertain: " + "; ".join(cleanup_errors)
            raise WindowsJobError(message) from exc

    def _ensure_open_handle(self) -> tuple[int, int]:
        if self._job_handle is None or self._process_handle is None:
            if self._close_error is not None:
                raise self._close_error
            raise WindowsJobError("Windows Job handles are closed")
        return self._job_handle, self._process_handle

    @staticmethod
    def _timeout_ms(timeout: float) -> int:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or timeout < 0
        ):
            raise ValueError("timeout must be finite and non-negative")
        return min(0xFFFFFFFE, max(0, math.ceil(float(timeout) * 1000)))

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        _job, process = self._ensure_open_handle()
        result = _call(
            "wait_for_process",
            lambda: self._api.wait_for_process(process, 0),
        )
        if result == "timeout":
            return None
        if result != "signaled":
            raise WindowsJobError(
                f"wait_for_process returned invalid result {result!r}"
            )
        exit_code = _call(
            "get_exit_code_process",
            lambda: self._api.get_exit_code_process(process),
        )
        if type(exit_code) is not int or exit_code == STILL_ACTIVE:
            raise WindowsJobError("get_exit_code_process returned uncertain state")
        self.returncode = exit_code
        return exit_code

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        if timeout is None:
            raise ValueError("Windows Job waits must be bounded")
        _job, process = self._ensure_open_handle()
        result = _call(
            "wait_for_process",
            lambda: self._api.wait_for_process(
                process,
                self._timeout_ms(timeout),
            ),
        )
        if result == "timeout":
            raise subprocess.TimeoutExpired(self._argv, timeout)
        if result != "signaled":
            raise WindowsJobError(
                f"wait_for_process returned invalid result {result!r}"
            )
        exit_code = _call(
            "get_exit_code_process",
            lambda: self._api.get_exit_code_process(process),
        )
        if type(exit_code) is not int or exit_code == STILL_ACTIVE:
            raise WindowsJobError("get_exit_code_process returned uncertain state")
        self.returncode = exit_code
        return exit_code

    def terminate(self, exit_code: int) -> None:
        if type(exit_code) is not int or not 0 <= exit_code <= 0xFFFFFFFF:
            raise ValueError("exit_code must be an unsigned 32-bit integer")
        if self._terminated:
            if self._cleanup_error is not None:
                raise self._cleanup_error
            return
        job, process = self._ensure_open_handle()
        try:
            _call(
                "terminate_job",
                lambda: self._api.terminate_job(job, exit_code),
            )
            result = _call(
                "wait_for_process",
                lambda: self._api.wait_for_process(
                    process,
                    _TERMINATION_WAIT_MS,
                ),
            )
            if result != "signaled":
                raise WindowsJobError(
                    "wait_for_process did not prove termination"
                )
            observed_exit = _call(
                "get_exit_code_process",
                lambda: self._api.get_exit_code_process(process),
            )
            if type(observed_exit) is not int or observed_exit == STILL_ACTIVE:
                raise WindowsJobError(
                    "get_exit_code_process returned uncertain state"
                )
            active = _call(
                "query_active_processes",
                lambda: self._api.query_active_processes(job),
            )
            if type(active) is not int or active < 0:
                raise WindowsJobError(
                    "query_active_processes returned invalid state"
                )
            self._final_active_processes = active
            if active != 0:
                raise WindowsJobError(
                    f"Job still has {active} active processes"
                )
            self.returncode = observed_exit
            self._terminated = True
        except WindowsJobError as exc:
            if self._cleanup_error is None:
                self._cleanup_error = exc
            raise

    def terminate_tree(self) -> None:
        self.terminate(124)

    def kill_tree(self) -> None:
        self.terminate(137)

    def kill(self) -> None:
        self.kill_tree()

    def active_processes(self) -> int:
        if self._closed:
            if self._cleanup_error is not None:
                raise self._cleanup_error
            if self._final_active_processes != 0:
                raise WindowsJobError("final active process count is uncertain")
            return 0
        job, _process = self._ensure_open_handle()
        active = _call(
            "query_active_processes",
            lambda: self._api.query_active_processes(job),
        )
        if type(active) is not int or active < 0:
            raise WindowsJobError(
                "query_active_processes returned invalid state"
            )
        return active

    def close(self) -> None:
        if self._closed:
            if self._cleanup_error is not None:
                raise self._cleanup_error
            return
        close_errors: list[WindowsJobError] = []
        if (
            not self._terminated
            and self._process_handle is not None
            and self._job_handle is not None
        ):
            try:
                self.terminate(1)
            except WindowsJobError as exc:
                if self._cleanup_error is None:
                    self._cleanup_error = exc
        for field_name in ("_process_handle", "_job_handle"):
            handle = getattr(self, field_name)
            if handle is None:
                continue
            try:
                _call(
                    "close_handle",
                    lambda handle=handle: self._api.close_handle(handle),
                )
            except WindowsJobError as exc:
                close_errors.append(exc)
            else:
                setattr(self, field_name, None)
        for stream in (self.stdout, self.stderr):
            if getattr(stream, "closed", False):
                continue
            try:
                _call("close_stream", stream.close)
            except WindowsJobError as exc:
                close_errors.append(exc)
        resources_closed = (
            self._process_handle is None
            and self._job_handle is None
            and all(
                getattr(stream, "closed", False)
                for stream in (self.stdout, self.stderr)
            )
        )
        self._closed = resources_closed
        if close_errors:
            self._close_error = WindowsJobError(
                "; ".join(str(error) for error in close_errors)
            )
        else:
            self._close_error = None
        errors = [
            error
            for error in (self._cleanup_error, self._close_error)
            if error is not None
        ]
        if errors:
            raise WindowsJobError(
                "; ".join(str(error) for error in errors)
            )

    def containment_evidence(self) -> dict[str, object]:
        handles_closed = (
            self._closed
            and self._process_handle is None
            and self._job_handle is None
            and all(
                getattr(stream, "closed", False)
                for stream in (self.stdout, self.stderr)
            )
            and self._cleanup_error is None
            and self._close_error is None
        )
        return {
            "real_windows_job": bool(
                getattr(self._api, "is_real", False)
            ),
            "assignment_proven": self._assignment_proven,
            "active_process_limit": self._limits.active_processes,
            "memory_limit_bytes": self._limits.memory_bytes,
            "kill_on_close": True,
            "resume_after_assignment": self._resume_after_assignment,
            "final_active_processes": self._final_active_processes,
            "handles_closed": handles_closed,
            "cleanup_certain": (
                self._cleanup_error is None
                and self._close_error is None
                and handles_closed
            ),
        }


WindowsJobProcess.start.__func__._windows_job_launcher = True
