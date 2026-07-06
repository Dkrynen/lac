"""Regression tests for the hardware-probe caching fix.

Root cause: backend.cookbook.hardware.detect() re-ran the expensive per-OS raw
probe (on Windows: a ~1s PowerShell/WMI subprocess) on EVERY call, and detect()
is invoked on every /api/scan, /api/recommend, and Autopilot benchmark step.

Fix: cache the expensive raw probe with functools.lru_cache(maxsize=1) so the
subprocess runs once per process, while detect() keeps building a FRESH
SystemInfo (with fresh GPUInfo instances) on every call so callers that mutate
their returned SystemInfo (e.g. /api/recommend?vram= overriding info.gpus /
info.combined_vram_gb) can never corrupt a later detect() call.
"""
import platform
import types

import pytest

from backend.cookbook import hardware


_CACHED_PROBE_NAMES = (
    "_detect_windows",
    "_detect_apple_silicon",
    "_detect_nvidia",
    "_detect_amd_linux",
)


def _clear_all_probe_caches() -> None:
    for name in _CACHED_PROBE_NAMES:
        fn = getattr(hardware, name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_probe_cache():
    """Every test starts and ends with clean lru_caches so tests can't bleed
    cached results into each other (order-independence)."""
    _clear_all_probe_caches()
    yield
    _clear_all_probe_caches()


class _FakeCompletedProcess:
    """Minimal stand-in for subprocess.CompletedProcess used by proc.run()."""

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


_FAKE_WINDOWS_PS_STDOUT = (
    '{"gpu": "[{\\"Name\\": \\"Test GPU\\", \\"AdapterRAM\\": 8, '
    '\\"DriverVersion\\": \\"1.0\\", \\"Backend\\": \\"cuda\\"}]", '
    '"ram": 16777216, "cpu_name": "Test CPU", "cpu_cores": 8}'
)


def _install_fake_windows_probe(monkeypatch, call_counter=None):
    """Force detect() down the Windows branch and stub proc.run() so the test
    is fast and deterministic on any host OS, while still exercising the real
    caching logic in _detect_windows()."""

    def fake_run(cmd, **kwargs):
        if call_counter is not None:
            call_counter["n"] += 1
        return _FakeCompletedProcess(stdout=_FAKE_WINDOWS_PS_STDOUT, returncode=0)

    monkeypatch.setattr(hardware, "platform", types.SimpleNamespace(system=lambda: "Windows"))
    monkeypatch.setattr(hardware.proc, "run", fake_run)
    monkeypatch.setattr(hardware, "_in_container", lambda: False)


@pytest.mark.skipif(platform.system() != "Windows", reason="exercises the real Windows WMI/PowerShell probe")
def test_windows_probe_runs_once_across_multiple_detect_calls(monkeypatch):
    """The expensive PowerShell/WMI subprocess must run once per session, not
    once per detect() call -- this is the actual perf bug being fixed."""
    call_counter = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_counter["n"] += 1
        return _FakeCompletedProcess(stdout=_FAKE_WINDOWS_PS_STDOUT, returncode=0)

    monkeypatch.setattr(hardware.proc, "run", fake_run)

    hardware.detect()
    hardware.detect()
    hardware.detect()

    assert call_counter["n"] == 1, (
        f"expected the Windows WMI/PowerShell probe to run once (cached) across "
        f"3 detect() calls, but it ran {call_counter['n']} times -- the "
        f"expensive subprocess is not being cached"
    )


def test_windows_probe_stub_runs_once_across_multiple_detect_calls_any_platform(monkeypatch):
    """Same invariant as above, but forces the Windows branch via a stubbed
    platform.system() so this coverage isn't gated to a Windows CI runner."""
    call_counter = {"n": 0}
    _install_fake_windows_probe(monkeypatch, call_counter)

    hardware.detect()
    hardware.detect()
    hardware.detect()

    assert call_counter["n"] == 1, (
        f"expected the (stubbed) Windows probe subprocess to run once across "
        f"3 detect() calls, but it ran {call_counter['n']} times"
    )


def test_detect_returns_distinct_object_each_call(monkeypatch):
    """detect() must build a fresh SystemInfo every call -- callers like
    /api/recommend?vram= mutate the returned object in place, and that must
    never bleed into the next request's detect() result."""
    _install_fake_windows_probe(monkeypatch)

    a = hardware.detect()
    b = hardware.detect()

    assert a is not b, "detect() returned the SAME SystemInfo instance twice"
    assert a.gpus is not b.gpus, "detect() results share the same gpus list instance"
    if a.gpus and b.gpus:
        assert a.gpus[0] is not b.gpus[0], "detect() results share the same GPUInfo instance"


def test_mutating_one_detect_result_does_not_corrupt_the_next(monkeypatch):
    """Direct regression test for the subtle constraint: caching the raw probe
    must NOT cache-and-return the same SystemInfo/GPUInfo objects, because
    /api/recommend?vram= overrides info.gpus / info.combined_vram_gb on the
    object it gets back."""
    _install_fake_windows_probe(monkeypatch)

    a = hardware.detect()
    # Mimic what /api/recommend?vram= does today: mutate the returned object.
    a.combined_vram_gb = 999.0
    a.total_vram_gb = 999.0
    a.gpus.append(hardware.GPUInfo(name="Injected-GPU", vram_gb=123.0))
    if a.gpus:
        a.gpus[0].vram_gb = 4242.0
        a.gpus[0].name = "Mutated-Name"

    c = hardware.detect()

    assert c.combined_vram_gb != 999.0, "mutating a's combined_vram_gb leaked into a later detect() call"
    assert c.total_vram_gb != 999.0, "mutating a's total_vram_gb leaked into a later detect() call"
    assert not any(g.name == "Injected-GPU" for g in c.gpus), (
        "appending to a's gpus list leaked into a later detect() call's gpus list -- "
        "the raw probe's GPUInfo objects are being shared, not cloned, per call"
    )
    assert not any(g.name == "Mutated-Name" for g in c.gpus), (
        "mutating a GPUInfo field on a's gpus leaked into a later detect() call -- "
        "GPUInfo instances must be fresh copies each detect() call"
    )
