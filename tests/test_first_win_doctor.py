from pathlib import Path
from types import SimpleNamespace

from backend.cookbook.hardware import GPUInfo, SystemInfo, _finalize_compute_tiers
from backend.first_win.doctor import run_doctor


class _Provider:
    def __init__(self, names):
        self._names = names

    def list_models(self):
        return [SimpleNamespace(name=name) for name in self._names]


def _hardware(*, include_unverified_igpu=False):
    gpus = [GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan")]
    if include_unverified_igpu:
        gpus.append(
            GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="vulkan")
        )
    return _finalize_compute_tiers(
        SystemInfo(
            os="Windows",
            cpu="Test CPU",
            cpu_cores=12,
            ram_gb=32.0,
            gpus=gpus,
        )
    )


def _recommendations(*unused_args, **unused_kwargs):
    return [
        SimpleNamespace(
            model=SimpleNamespace(id="qwen3:8b"),
            score=91,
        )
    ]


def _run(tmp_path, **overrides):
    dependencies = {
        "detect_fn": _hardware,
        "provider_factory": lambda: _Provider(["qwen3:8b"]),
        "recommend_fn": _recommendations,
        "which_fn": lambda name: str(tmp_path / f"{name}.exe"),
        "opencode_probe_fn": lambda: Path("C:/tools/opencode.exe"),
        "disk_usage_fn": lambda path: SimpleNamespace(
            total=100 * 1024**3,
            used=20 * 1024**3,
            free=80 * 1024**3,
        ),
        "receipt_probe_fn": lambda path: None,
    }
    dependencies.update(overrides)
    return run_doctor(project_dir=tmp_path, **dependencies)


def _check(report, name):
    return next(check for check in report.checks if check.name == name)


def test_healthy_local_agent_environment_is_ready(tmp_path):
    report = _run(tmp_path)

    assert report.ready is True
    assert all(check.status in {"pass", "warn"} for check in report.checks)
    assert _check(report, "ollama_model").evidence["selected"] == "qwen3:8b"
    assert _check(report, "opencode").evidence["supported_version"] == "1.18.4"


def test_missing_ollama_is_structured_failure(tmp_path):
    report = _run(
        tmp_path,
        provider_factory=lambda: (_ for _ in ()).throw(
            ConnectionError("connection refused")
        ),
    )

    check = _check(report, "ollama")
    assert report.ready is False
    assert check.status == "fail"
    assert "connection refused" in check.evidence["error"]
    assert "start" in check.remediation.lower()


def test_no_installed_agent_model_fails_without_downloading(tmp_path):
    calls = []

    class EmptyProvider:
        def list_models(self):
            calls.append("list")
            return []

        def pull(self, *args):
            calls.append(("pull", args))

    report = _run(tmp_path, provider_factory=EmptyProvider)

    check = _check(report, "ollama_model")
    assert report.ready is False
    assert check.status == "fail"
    assert check.evidence["installed"] == []
    assert calls == ["list"]


def test_unsupported_opencode_reports_exact_supported_version(tmp_path):
    report = _run(
        tmp_path,
        opencode_probe_fn=lambda: (_ for _ in ()).throw(
            RuntimeError("OpenCode 1.19.0 is installed")
        ),
    )

    check = _check(report, "opencode")
    assert report.ready is False
    assert check.status == "fail"
    assert check.evidence["supported_version"] == "1.18.4"
    assert "1.18.4" in check.remediation


def test_unverified_integrated_memory_is_disclosed_as_excluded(tmp_path):
    report = _run(
        tmp_path,
        detect_fn=lambda: _hardware(include_unverified_igpu=True),
    )

    check = _check(report, "hardware")
    assert check.status == "warn"
    assert check.evidence["verified_model_fit_vram_gb"] == 16.0
    assert check.evidence["excluded_shared_memory_gb"] == 10.5
    assert "excluded" in check.summary.lower()


def test_external_boundary_exception_becomes_failed_check(tmp_path):
    report = _run(
        tmp_path,
        detect_fn=lambda: (_ for _ in ()).throw(PermissionError("blocked")),
        disk_usage_fn=lambda path: (_ for _ in ()).throw(OSError("disk denied")),
        receipt_probe_fn=lambda path: (_ for _ in ()).throw(
            PermissionError("receipt denied")
        ),
    )

    assert report.ready is False
    assert _check(report, "hardware").evidence["error"] == "blocked"
    assert _check(report, "disk").evidence["error"] == "disk denied"
    assert _check(report, "receipts").evidence["error"] == "receipt denied"


def test_probe_error_redacts_url_credentials(tmp_path):
    report = _run(
        tmp_path,
        provider_factory=lambda: (_ for _ in ()).throw(
            ConnectionError(
                "Cannot connect to http://local-user:super-secret@localhost:11434"
            )
        ),
    )

    error = _check(report, "ollama").evidence["error"]
    assert "super-secret" not in error
    assert "local-user" not in error
    assert "http://localhost:11434" in error


def test_zeroed_hardware_probe_fails_instead_of_claiming_detection(tmp_path):
    report = _run(
        tmp_path,
        detect_fn=lambda: SystemInfo(os="Windows"),
    )

    check = _check(report, "hardware")
    assert check.status == "fail"
    assert "no usable" in check.summary.lower()
