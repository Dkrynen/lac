"""Fit intelligence: classify a model against the user's hardware.

One verdict, consumed by Layer 1 (quality-cost surfacing), Layer 2 (distill
routing), and Layer 3 (quantize-to-fit). Reuses recommend.py's VRAM math so
the verdict agrees with what recommend() would actually pick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .hardware import SystemInfo
from .recommend import (
    QUANTS,
    ModelEntry,
    _compute_split_plan,
    _estimate_vram,
)

# Quality cost at/above this many points (<= Q3_K_M) is "punishing": the UI
# should offer a distill/quantize alternative alongside the low-quant fit.
HIGH_QUALITY_COST = 10.0

# Context ladder (mirrors recommend()): a model's headline context (e.g. 262K)
# carries a huge KV cache, so fit is judged across practical contexts, not only
# the max.
CTX_LADDER = (65536, 32768, 16384, 8192, 4096, 2048)


@dataclass
class FitVerdict:
    kind: str  # "fits" | "fits_at_quant" | "distill_available" | "quantize_to_fit" | "too_big"
    quant: Optional[str] = None
    quality_cost: float = 0.0
    bpp: Optional[float] = None
    context: Optional[int] = None


def _ctx_options(model: ModelEntry) -> list[int]:
    return [c for c in [model.context, *CTX_LADDER] if c <= model.context]


def _available_vram_gb(info: SystemInfo) -> float:
    tiers = info.compute_tiers
    if tiers:
        combined = sum(t.memory_gb for t in tiers if t.kind != "ram")
        return combined if combined > 0 else max(info.ram_gb * 0.5, 0.1)
    return max(info.total_vram_gb, info.ram_gb * 0.25)


def _max_fitting_bpp(model: ModelEntry, info: SystemInfo, ctx: int) -> Optional[float]:
    """Largest bits/param at which the model's weights+KV+overhead fit. None if
    even an arbitrarily small quant cannot fit (KV alone exceeds the budget)."""
    avail = _available_vram_gb(info)
    active = model.active_params_b if model.is_moe and model.active_params_b else model.params_b
    kv = 0.000008 * active * ctx
    overhead = 0.5
    budget = avail - kv - overhead
    if budget <= 0 or model.params_b <= 0:
        return None
    return budget / model.params_b


def _fits(model: ModelEntry, q, ctx: int, info: SystemInfo) -> bool:
    return _compute_split_plan(_estimate_vram(model, q, ctx), info, model) is not None


def fit_verdict(model: ModelEntry, info: SystemInfo, use_case: str = "coding") -> FitVerdict:
    # 1. Fits at the default quant (Q4_K_M) at some practical context?
    for ctx in _ctx_options(model):
        if _fits(model, QUANTS[4], ctx, info):
            return FitVerdict(kind="fits", quant="Q4_K_M", context=ctx)
    # 2. Fits at a lower quant? Take the highest-quality quant that fits at any context.
    for q in QUANTS:  # ordered F16 -> Q2_K (best quality first)
        for ctx in _ctx_options(model):
            if _fits(model, q, ctx, info):
                return FitVerdict(kind="fits_at_quant", quant=q.name,
                                  quality_cost=abs(q.quality_penalty), context=ctx)
    # 3. No ladder quant fits. Could a custom (more aggressive) quant fit at the
    #    smallest practical context?
    small_ctx = min(_ctx_options(model))
    bpp = _max_fitting_bpp(model, info, small_ctx)
    if bpp is not None and bpp < QUANTS[-1].bpp:
        return FitVerdict(kind="quantize_to_fit", bpp=round(bpp, 2), context=small_ctx)
    return FitVerdict(kind="too_big")
