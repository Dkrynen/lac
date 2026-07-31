from __future__ import annotations

from backend.cookbook.hardware import GPUInfo, SystemInfo
from backend.agent_eval.recipes import RecipeCard, hardware_class


def _info(gpu_name="", vram=0.0, backend="cuda", apple=False, ram=32.0):
    return SystemInfo(
        os="Test", cpu="c", cpu_cores=8, ram_gb=ram,
        gpus=[GPUInfo(name=gpu_name, vram_gb=vram, backend=backend)] if gpu_name else [],
        total_vram_gb=vram,
        is_apple_silicon=apple,
    )


def test_hardware_class_amd():
    assert hardware_class(_info("AMD Radeon RX 6800 XT", 16.0, "rocm")) == "amd-16gb"


def test_hardware_class_nvidia():
    assert hardware_class(_info("NVIDIA GeForce RTX 4090", 24.0, "cuda")) == "nvidia-24gb"


def test_hardware_class_apple_unified_memory():
    assert hardware_class(_info("Apple M2 Max", 32.0, "metal", apple=True)) == "apple-32gb"


def test_hardware_class_cpu_only_uses_ram():
    assert hardware_class(_info("", 0.0, "", ram=16.0)) == "cpu-16gb"


def test_hardware_class_vram_tier_rounds_to_nearest():
    assert hardware_class(_info("AMD Radeon RX 6800 XT", 15.9, "rocm")) == "amd-16gb"


def test_recipe_card_defaults_and_fields():
    card = RecipeCard(
        model_id="gpt-oss:20b", hardware_class="amd-16gb", tokens_per_second=94.67,
        quant="Q4_K_M", context=65536, trials=3, evidence_run="2026-07-28-eval",
    )
    assert card.use_case == "agent"
    assert card.tokens_per_second == 94.67
    assert card.trials == 3
