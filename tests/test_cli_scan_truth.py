from types import SimpleNamespace

import cli
from backend.cookbook import hardware
from backend.cookbook.hardware import GPUInfo, SystemInfo


def _run_scan(monkeypatch, capsys, gpus):
    info = hardware._finalize_compute_tiers(SystemInfo(
        os="Windows",
        cpu="Test CPU",
        cpu_cores=8,
        ram_gb=32.0,
        gpus=gpus,
    ))
    monkeypatch.setattr(hardware, "detect", lambda: info)

    cli.cmd_scan(SimpleNamespace())

    return capsys.readouterr().out


def test_scan_labels_unverified_integrated_memory_as_excluded(
    monkeypatch, capsys
):
    output = _run_scan(
        monkeypatch,
        capsys,
        [
            GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
            GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="vulkan"),
        ],
    )

    assert "10.5 GB reported shared memory" in output
    assert "excluded from model splitting" in output
    assert "Total VRAM:" in output
    assert "16.0 GB" in output


def test_scan_does_not_promote_unverified_igpu_on_integrated_only_system(
    monkeypatch, capsys
):
    output = _run_scan(
        monkeypatch,
        capsys,
        [GPUInfo("AMD Radeon(TM) Graphics", 12.0, backend="vulkan")],
    )

    assert "12.0 GB reported shared memory" in output
    assert "Total VRAM:" not in output
    assert "Models that fit:" not in output
