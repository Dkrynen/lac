from backend.cookbook import hardware
from backend.cookbook.hardware import GPUInfo, SystemInfo, build_compute_tiers


def test_unverified_integrated_gpu_is_not_a_compute_tier():
    gpus = [
        GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
        GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="vulkan"),
    ]

    tiers = build_compute_tiers(gpus, 30.9)

    assert [tier.kind for tier in tiers] == ["discrete", "ram"]


def test_verified_integrated_gpu_remains_available_for_split():
    gpus = [
        GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
        GPUInfo(
            "AMD Radeon(TM) Graphics",
            10.5,
            backend="vulkan",
            split_verified=True,
        ),
    ]

    tiers = build_compute_tiers(gpus, 30.9)

    assert [tier.kind for tier in tiers] == ["discrete", "integrated", "ram"]


def test_unknown_gpu_is_reported_but_not_trusted_for_model_fit():
    gpu = GPUInfo("Future Graphics Adapter", 24.0, backend="vulkan")

    tiers = build_compute_tiers([gpu], 32.0)

    assert gpu.tier == "unknown"
    assert [tier.kind for tier in tiers] == ["ram"]


def test_combined_vram_excludes_unverified_shared_memory():
    info = SystemInfo(
        ram_gb=30.9,
        gpus=[
            GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
            GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="vulkan"),
        ],
    )

    hardware._finalize_compute_tiers(info)

    assert info.total_vram_gb == 16.0
    assert info.combined_vram_gb == 16.0


def test_combined_vram_includes_runtime_verified_integrated_memory():
    info = SystemInfo(
        ram_gb=30.9,
        gpus=[
            GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
            GPUInfo(
                "AMD Radeon(TM) Graphics",
                10.5,
                backend="vulkan",
                split_verified=True,
            ),
        ],
    )

    hardware._finalize_compute_tiers(info)

    assert info.combined_vram_gb == 26.5


def test_integrated_only_system_falls_back_to_ram_until_verified():
    info = SystemInfo(
        ram_gb=32.0,
        gpus=[GPUInfo("AMD Radeon(TM) Graphics", 12.0, backend="vulkan")],
    )

    hardware._finalize_compute_tiers(info)

    assert info.total_vram_gb == 0.0
    assert info.combined_vram_gb == 0.0
    assert [tier.kind for tier in info.compute_tiers] == ["ram"]


def test_apple_unified_memory_keeps_its_single_accelerator_tier():
    info = SystemInfo(
        os="Darwin",
        ram_gb=32.0,
        is_apple_silicon=True,
        gpus=[GPUInfo("Apple M3 Max", 24.0, backend="metal")],
    )

    hardware._finalize_compute_tiers(info)

    assert info.total_vram_gb == 24.0
    assert info.combined_vram_gb == 24.0
    assert [tier.backend for tier in info.compute_tiers] == ["metal"]


def test_print_system_labels_unverified_integrated_memory_as_excluded(capsys):
    info = SystemInfo(
        os="Windows",
        ram_gb=30.9,
        gpus=[
            GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
            GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="vulkan"),
        ],
    )
    hardware._finalize_compute_tiers(info)

    hardware.print_system(info)

    output = capsys.readouterr().out
    assert "10.5 GB reported shared memory" in output
    assert "excluded from model splitting" in output
