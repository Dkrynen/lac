"""Proven agent profiles: hardware-class taxonomy + evidence-backed recipe cards.

A recipe card asserts a model is *proven* on a hardware class — backed by sealed
eval evidence (agent_eval), never asserted without it. This module holds the
self-contained core: the hardware-class classifier and the card schema. The
results-aggregation layer that projects cards from sealed eval runs builds on it.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

from ..cookbook.hardware import SystemInfo
from .ledger import verify_evidence

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


def _iter_run_dirs(evidence_root: Path):
    root = Path(evidence_root)
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir():
            yield child


def _read_manifest(run_root: Path):
    path = run_root / "run_manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "hardware_class" not in data:
        return None
    return data


def _read_results(run_root: Path):
    for path in sorted((run_root / "trials").glob("*/*/result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            yield data


def aggregate_recipes(evidence_root, min_trials: int = 3) -> dict[tuple[str, str], RecipeCard]:
    """Project proven recipe cards from sealed eval runs under evidence_root.

    A (model, hardware_class) card exists only when >= min_trials sealed runs
    carry a run_manifest (hardware + quant + context) and a completed result
    with a measured tokens_per_second. tok/s is the median of the passing runs.
    Runs that fail seal verification, lack a manifest, or have no completed
    measurement are ignored — a card is never asserted without sealed evidence.
    """
    grouped: dict[tuple[str, str], dict] = {}
    for run_root in _iter_run_dirs(Path(evidence_root)):
        if not verify_evidence(run_root).ok:
            continue
        manifest = _read_manifest(run_root)
        if manifest is None:
            continue
        hw_class = manifest["hardware_class"]
        for result in _read_results(run_root):
            if not result.get("completed"):
                continue
            model = result.get("model")
            tok_s = (result.get("metrics") or {}).get("tokens_per_second")
            if not model or tok_s is None:
                continue
            entry = grouped.setdefault((model, hw_class), {"tok_s": [], "manifest": manifest, "runs": set()})
            entry["tok_s"].append(float(tok_s))
            entry["runs"].add(run_root.name)

    cards: dict[tuple[str, str], RecipeCard] = {}
    for (model, hw_class), entry in grouped.items():
        trials = len(entry["runs"])
        if trials < min_trials:
            continue
        manifest = entry["manifest"]
        cards[(model, hw_class)] = RecipeCard(
            model_id=model,
            hardware_class=hw_class,
            tokens_per_second=round(statistics.median(entry["tok_s"]), 2),
            quant=manifest.get("quant", "Q4_K_M"),
            context=int(manifest.get("context", 0)),
            trials=trials,
            evidence_run=sorted(entry["runs"])[-1],
            use_case=manifest.get("use_case", "agent"),
        )
    return cards


def lookup_recipe(cards, model_id: str, info: SystemInfo):
    """Return the proven card for model_id on info's hardware class, or None."""
    return cards.get((model_id, hardware_class(info)))
