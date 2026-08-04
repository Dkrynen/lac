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
    load_models,
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


def available_vram_gb(info: SystemInfo) -> float:
    """VRAM budget LAC plans against: combined GPU tier memory when compute
    tiers exist (RAM-half fallback when they hold nothing), else the best
    discrete VRAM with a RAM-quarter fallback."""
    tiers = info.compute_tiers
    if tiers:
        combined = sum(t.memory_gb for t in tiers if t.kind != "ram")
        return combined if combined > 0 else max(info.ram_gb * 0.5, 0.1)
    return max(info.total_vram_gb, info.ram_gb * 0.25)


def _max_fitting_bpp(model: ModelEntry, info: SystemInfo, ctx: int) -> Optional[float]:
    """Largest bits/param at which the model's weights+KV+overhead fit. None if
    even an arbitrarily small quant cannot fit (KV alone exceeds the budget)."""
    avail = available_vram_gb(info)
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


@dataclass
class DistillSuggestion:
    model: ModelEntry
    relationship: str
    verified: bool
    note: str


def _curated_relatives(model: ModelEntry, by_id: dict) -> list[tuple[ModelEntry, str]]:
    """Verified relatives from curated catalog lineage (distill_of / family)."""
    out: list[tuple[ModelEntry, str]] = []
    if model.distill_of and model.distill_of in by_id:
        out.append((by_id[model.distill_of], "the base model this is distilled from"))
    for m in by_id.values():
        if m.id == model.id:
            continue
        if m.distill_of == model.id:
            out.append((m, "a distilled variant of this model"))
        elif model.family and m.family == model.family:
            out.append((m, f"in the same {model.family} family"))
    return out


def _unverified_relatives(model_id: str) -> list[tuple[ModelEntry, str]]:
    """Auto-derived relatives from upstream (HuggingFace base_model) metadata.
    NEVER verified — callers must frame these as 'likely related'. Wired to the
    HF base_model resolver in api.py when available; empty by default."""
    return []


def recommend_distill(info: SystemInfo, model_id: str, use_case: str = "coding") -> Optional[DistillSuggestion]:
    """If model_id doesn't fit, return the best-fitting relative, or None."""
    by_id = {m.id: m for m in load_models()}
    target = by_id.get(model_id)
    if target is None:
        return None
    target_v = fit_verdict(target, info, use_case)
    if target_v.kind == "fits":
        return None

    def best(candidates: list[tuple[ModelEntry, str]], verified: bool) -> Optional[DistillSuggestion]:
        fitted = []
        for rel, relationship in candidates:
            v = fit_verdict(rel, info, use_case)
            if v.kind in ("fits", "fits_at_quant"):
                fitted.append((rel, relationship, v))
        if not fitted:
            return None
        fitted.sort(key=lambda t: t[0].params_b, reverse=True)
        rel, relationship, v = fitted[0]
        if verified:
            note = (f"{target.name} won't fit your hardware. {rel.name} ({relationship}) "
                    f"fits at {v.quant or 'Q4_K_M'}.")
        else:
            note = (f"{target.name} won't fit your hardware. {rel.name} {relationship} "
                    f"and fits at {v.quant or 'Q4_K_M'} — worth trying.")
        return DistillSuggestion(model=rel, relationship=relationship, verified=verified, note=note)

    verified = best(_curated_relatives(target, by_id), verified=True)
    if verified is not None:
        return verified
    return best(_unverified_relatives(model_id), verified=False)
