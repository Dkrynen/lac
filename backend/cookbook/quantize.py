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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import proc

from .fit import _ctx_options, available_vram_gb
from .recommend import (
    QUANTS,
    ModelEntry,
    _estimate_vram,
    load_models,
    register_custom_model,
)
from ..agent_launch.variant import is_installed, normalize_model_name

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


def select_target_quant(model: ModelEntry, info=None, *,
                        vram_override_gb: float | None = None) -> QuantizePlan:
    """Pick the quant + context that makes `model` fit, or raise QuantizeRefusal.

    Refuses when Q4_K_M already fits at any practical context (quantizing would
    only lose quality) and when nothing on the ladder fits (shows the bpp budget
    a custom quant would need — below Q2_K is beyond what LAC produces).
    """
    if vram_override_gb is None and info is None:
        raise ValueError("select_target_quant needs info or vram_override_gb")
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


class QuantizerNotFound(QuantizeError):
    pass


class QuantizeRunFailed(QuantizeError):
    pass


def find_quantizer(*, override: str | None = None) -> Path:
    """Locate the llama.cpp quantize binary: explicit override, then
    LAC_LLAMA_QUANTIZE, then PATH ('llama-quantize', legacy 'quantize')."""
    if override:
        p = Path(override)
        if p.is_file():
            return p
        raise QuantizerNotFound(f"quantizer not found at the given path: {override}")
    env = os.environ.get("LAC_LLAMA_QUANTIZE")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise QuantizerNotFound(
            f"LAC_LLAMA_QUANTIZE points at a missing file: {env}"
        )
    for name in ("llama-quantize", "quantize"):
        found = shutil.which(name)
        if found:
            return Path(found)
    raise QuantizerNotFound(
        "No llama.cpp quantizer found. Install llama.cpp (build it or grab a "
        "release) so `llama-quantize` is on your PATH, or point "
        "LAC_LLAMA_QUANTIZE at the binary."
    )


def run_quantize(src: Path, dst: Path, quant_type: str, *,
                 quantizer: Path, run=proc.run) -> None:
    """Run the quantizer to completion; delete any partial output on failure."""
    result = run([str(quantizer), str(src), str(dst), quant_type],
                 capture_output=True, text=True)
    if result.returncode != 0:
        if Path(dst).exists():
            Path(dst).unlink()
        tail = ((result.stdout or "") + (result.stderr or ""))[-500:]
        raise QuantizeRunFailed(
            f"llama-quantize failed (exit {result.returncode}): {tail.strip()}"
        )


class InsufficientDiskSpace(QuantizeError):
    pass


def required_space_gb(plan: QuantizePlan) -> float:
    """Staged file + Ollama's import copy + 10% margin."""
    return plan.estimated_size_gb * 2.2


def _existing_anchor(path: Path) -> Path:
    anchor = Path(path)
    while not anchor.exists() and anchor.parent != anchor:
        anchor = anchor.parent
    return anchor


def check_disk_space(staging_dir: Path, store_root: Path, required_gb: float, *,
                     disk_usage=shutil.disk_usage) -> None:
    """Refuse unless BOTH the staging volume and the Ollama store volume have
    required_gb free — the quantized file is staged, then Ollama copies it into
    its blob store on import."""
    for label, path in (("staging", Path(staging_dir)), ("Ollama store", Path(store_root))):
        free_gb = disk_usage(str(_existing_anchor(path))).free / (1024 ** 3)
        if free_gb < required_gb:
            raise InsufficientDiskSpace(
                f"Not enough disk space: the {label} volume at {path} has "
                f"{free_gb:.1f} GB free but ~{required_gb:.1f} GB is needed "
                f"(quantized file + Ollama's import copy)."
            )


STAGING_DIR = Path.home() / ".model-hub" / "quantize-staging"


def quantize_variant_name(base_model: str, quant: str) -> str:
    return f"{base_model}-{quant.lower()}-fit"


@dataclass(frozen=True)
class QuantizeResult:
    variant: str
    plan: QuantizePlan
    source: str
    catalog_id: str


def _quant_bpp(quant_name: str) -> float | None:
    name = QUANT_ALIASES.get(quant_name, quant_name)
    for q in QUANTS:
        if q.name == name:
            return q.bpp
    return None


def quantize_model(model_id: str, *,
                   info=None,
                   vram_override_gb: float | None = None,
                   list_names: Callable[[], Iterable[str]],
                   quant_levels: Callable[[], dict],
                   create_from_file: Callable[[str, Path], None],
                   store_root: Path | None = None,
                   quantizer: str | None = None,
                   run=proc.run,
                   disk_usage=shutil.disk_usage) -> QuantizeResult:
    """Quantize an installed model into a fitting local variant.

    Every refusal is a QuantizeError subclass with a user-facing reason; every
    failure after staging deletes the staged file. Never triggers a download:
    the source must already be installed (variant.py hard rule).
    """
    by_id = {m.id: m for m in load_models()}
    model = by_id.get(model_id)
    if model is None:
        raise QuantizeRefusal(
            f"Unknown model '{model_id}'. LAC quantizes models it knows — "
            f"see `lac browse` for the catalog."
        )
    installed = list(list_names())
    if not is_installed(model_id, installed):
        raise QuantizeRefusal(
            f"{model_id} is not installed. Run `lac pull {model_id}` first — "
            f"LAC never downloads a model silently."
        )
    plan = select_target_quant(model, info, vram_override_gb=vram_override_gb)
    variant = quantize_variant_name(normalize_model_name(model_id), plan.target_quant)
    if is_installed(variant, installed):
        raise QuantizeRefusal(
            f"{variant} already exists. Delete it first: `lac delete {variant}`."
        )
    levels = quant_levels()
    source_level = None
    for name, level in levels.items():
        if normalize_model_name(name) == normalize_model_name(model_id):
            source_level = level or None
            break
    source_bpp = _quant_bpp(source_level) if source_level else None
    if source_bpp is None:
        raise QuantizeRefusal(
            f"Cannot verify the quant of {model_id} (reported: "
            f"{source_level or 'unknown'}) — LAC only quantizes from known "
            f"ladder quants."
        )
    if plan.target_bpp >= source_bpp:
        raise QuantizeRefusal(
            f"{model_id} is already at {source_level}; re-quantizing to "
            f"{plan.target_quant} cannot improve it."
        )
    src = resolve_source_gguf(model_id, store_root=store_root)
    root = Path(store_root) if store_root is not None else default_store_root()
    check_disk_space(STAGING_DIR, root, required_space_gb(plan), disk_usage=disk_usage)
    quantizer_path = find_quantizer(override=quantizer)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    safe = model_id.replace(":", "-").replace("/", "-")
    staged = STAGING_DIR / f"{safe}-{plan.target_quant.lower()}.gguf"
    try:
        run_quantize(src, staged, LLAMA_QUANT_NAMES.get(plan.target_quant, plan.target_quant),
                     quantizer=quantizer_path, run=run)
        create_from_file(variant, staged)
        entry = {
            "id": variant,
            "name": f"{model.name} {plan.target_quant} (LAC quantized)",
            "provider": model.provider,
            "params_b": model.params_b,
            "arch": model.arch,
            "context": plan.context,
            "use_cases": list(model.use_cases),
            "is_moe": model.is_moe,
            "quantized_from": model.id,
        }
        if model.active_params_b is not None:
            entry["active_params_b"] = model.active_params_b
        register_custom_model(entry)
    finally:
        if staged.exists():
            staged.unlink()
    return QuantizeResult(variant=variant, plan=plan, source=model_id, catalog_id=variant)
