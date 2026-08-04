"""Layer 3 — quantize-to-fit: shrink an installed model so it fits this machine.

Locked design (specs/2026-08-01-fit-intelligence-distillation-design.md §5):
LAC orchestrates + verifies llama.cpp quantization; it never re-implements it,
never quantizes a model that is not already installed (no silent multi-GB
pulls), states the honest quality cost before running, and leaves no scratch
files behind on any failure path.
"""
from __future__ import annotations

import json
import os
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


class StoreError(QuantizeError):
    """The Ollama store could not resolve an installed model to one GGUF file."""


class ManifestNotFound(StoreError):
    pass


class MultiPartModel(StoreError):
    pass


class NonGgufWeights(StoreError):
    pass


_WEIGHTS_MEDIA_TYPE = "application/vnd.ollama.image.model"
_GGUF_MAGIC = b"GGUF"


def default_store_root() -> Path:
    configured = os.environ.get("OLLAMA_MODELS")
    if configured:
        return Path(configured)
    return Path.home() / ".ollama" / "models"


def _manifest_path(store_root: Path, model_name: str) -> Path:
    name, _, tag = model_name.partition(":")
    tag = tag or "latest"
    first, slash, rest = name.partition("/")
    if slash and "." in first:
        return store_root / "manifests" / first / rest / tag
    return store_root / "manifests" / "registry.ollama.ai" / "library" / name / tag


def resolve_source_gguf(model_name: str, *, store_root: Path | None = None) -> Path:
    """Resolve an installed model to its single GGUF blob path (read-only).

    Refuses manifests that are absent, sharded across several weights layers
    (llama-quantize needs one input file), or backed by non-GGUF weights.
    """
    root = Path(store_root) if store_root is not None else default_store_root()
    manifest_file = _manifest_path(root, model_name)
    if not manifest_file.is_file():
        raise ManifestNotFound(
            f"{model_name} has no manifest in the Ollama store at {root} — "
            f"is it installed? Run `lac pull {model_name}` first."
        )
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestNotFound(f"{model_name} has an unreadable manifest: {exc}") from exc
    weights = [
        layer for layer in manifest.get("layers", [])
        if isinstance(layer, dict) and layer.get("mediaType") == _WEIGHTS_MEDIA_TYPE
    ]
    if not weights:
        raise NonGgufWeights(f"{model_name} has no weights layer in its manifest.")
    if len(weights) > 1:
        raise MultiPartModel(
            f"{model_name} is stored in {len(weights)} shards; LAC quantizes "
            f"single-file GGUF models only."
        )
    digest = weights[0].get("digest", "")
    blob = root / "blobs" / digest.replace(":", "-", 1)
    if not blob.is_file():
        raise ManifestNotFound(f"{model_name} manifest points at a missing blob: {digest}")
    with open(blob, "rb") as f:
        magic = f.read(4)
    if magic != _GGUF_MAGIC:
        raise NonGgufWeights(
            f"{model_name} weights are not GGUF (e.g. safetensors) — LAC can only "
            f"quantize GGUF models."
        )
    return blob
