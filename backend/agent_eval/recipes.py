"""Proven agent profiles: hardware-class taxonomy + evidence-backed recipe cards.

A recipe card asserts a model is *proven* on a hardware class — backed by sealed
eval evidence (agent_eval), never asserted without it. This module holds the
self-contained core: the hardware-class classifier and the card schema. The
results-aggregation layer that projects cards from sealed eval runs builds on it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..cookbook.hardware import SystemInfo

# Common discrete-VRAM sizes; a card's class buckets to the nearest so a
# 15.9 GB card and a 16 GB card match the same "amd-16gb" recipes.
_VRAM_TIERS = (4, 6, 8, 12, 16, 24, 32, 48, 64, 80, 128)


@dataclass(frozen=True)
class RecipeCard:
    model_id: str
    hardware_class: str
    tokens_per_second: float
    quant: str
    context: int
    trials: int
    evidence_run: str
    use_case: str = "agent"


def _vram_tier(vram_gb: float) -> int:
    return min(_VRAM_TIERS, key=lambda t: abs(t - vram_gb))


def _gpu_vendor(info: SystemInfo) -> str:
    if info.is_apple_silicon:
        return "apple"
    if not info.gpus:
        return "cpu"
    gpu = info.gpus[0]
    name = gpu.name.lower()
    backend = (gpu.backend or "").lower()
    if backend == "rocm" or "radeon" in name or "amd" in name:
        return "amd"
    if backend == "cuda" or any(k in name for k in ("geforce", "nvidia", "rtx", "gtx", "quadro")):
        return "nvidia"
    if backend == "metal":
        return "apple"
    if "intel" in name or "arc" in name or "uhd" in name:
        return "intel"
    return "gpu"


def hardware_class(info: SystemInfo) -> str:
    """Canonical hardware-class id (e.g. 'amd-16gb', 'apple-32gb', 'cpu-16gb')."""
    vendor = _gpu_vendor(info)
    if vendor == "cpu":
        return f"cpu-{_vram_tier(info.ram_gb)}gb"
    if vendor == "apple":
        vram = info.total_vram_gb or info.ram_gb
    else:
        vram = info.gpus[0].vram_gb if info.gpus else info.total_vram_gb
    return f"{vendor}-{_vram_tier(vram)}gb"
