"""Layer 3 — quantize-to-fit: shrink an installed model so it fits this machine.

Locked design (specs/2026-08-01-fit-intelligence-distillation-design.md §5):
LAC orchestrates + verifies llama.cpp quantization; it never re-implements it,
never quantizes a model that is not already installed (no silent multi-GB
pulls), states the honest quality cost before running, and leaves no scratch
files behind on any failure path.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .fit import _ctx_options, available_vram_gb
from .recommend import QUANTS, ModelEntry, _estimate_vram

QUANT_ALIASES = {"Q8_0": "Q8"}
LLAMA_QUANT_NAMES = {"Q8": "Q8_0"}

_Q4_INDEX = next(i for i, q in enumerate(QUANTS) if q.name == "Q4_K_M")
_SUB_Q4_QUANTS = QUANTS[_Q4_INDEX + 1:]


@dataclass(frozen=True)
class QuantizePlan:
    target_quant: str
    target_bpp: float
    estimated_size_gb: float
    quality_cost: float
    context: int


class QuantizeError(Exception):
    """Base for all Layer-3 failures; .reason is the user-facing message."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class QuantizeRefusal(QuantizeError):
    """An honest refusal: LAC says why quantizing is pointless or impossible."""

    def __init__(self, reason: str, suggestion: Optional[str] = None):
        super().__init__(reason)
        self.suggestion = suggestion


def _active_params_b(model: ModelEntry) -> float:
    return model.active_params_b if model.is_moe and model.active_params_b else model.params_b


def select_target_quant(model: ModelEntry, info, *,
                        vram_override_gb: float | None = None) -> QuantizePlan:
    """Pick the quant + context that makes `model` fit, or raise QuantizeRefusal.

    Refuses when Q4_K_M already fits at any practical context (quantizing would
    only lose quality) and when nothing on the ladder fits (shows the bpp budget
    a custom quant would need — below Q2_K is beyond what LAC produces).
    """
    avail = vram_override_gb if vram_override_gb is not None else available_vram_gb(info)
    q4 = QUANTS[_Q4_INDEX]
    for ctx in _ctx_options(model):
        if _estimate_vram(model, q4, ctx) <= avail:
            raise QuantizeRefusal(
                f"{model.name} already fits at Q4_K_M (context {ctx}) — "
                f"quantizing it would only lose quality."
            )
    for q in _SUB_Q4_QUANTS:
        for ctx in _ctx_options(model):
            if _estimate_vram(model, q, ctx) <= avail:
                return QuantizePlan(
                    target_quant=q.name,
                    target_bpp=q.bpp,
                    estimated_size_gb=round(model.params_b * q.bpp, 1),
                    quality_cost=abs(q.quality_penalty),
                    context=ctx,
                )
    small_ctx = min(_ctx_options(model))
    active = _active_params_b(model)
    kv_gb = 0.000008 * active * small_ctx
    budget = avail - kv_gb - 0.5
    if budget <= 0 or model.params_b <= 0:
        raise QuantizeRefusal(
            f"{model.name} cannot fit this hardware: at context {small_ctx} the KV "
            f"cache and overhead alone consume the {avail:.1f} GB budget."
        )
    bpp = budget / model.params_b
    raise QuantizeRefusal(
        f"{model.name} cannot fit this hardware: even a custom quant would need "
        f"~{bpp:.2f} bpp at context {small_ctx}, more aggressive than Q2_K "
        f"({QUANTS[-1].bpp} bpp) — the deepest quant LAC produces."
    )
