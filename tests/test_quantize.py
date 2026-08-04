from __future__ import annotations

import json

import pytest

from backend.cookbook.hardware import GPUInfo, SystemInfo, build_compute_tiers
from backend.cookbook.recommend import ModelEntry
from backend.cookbook.quantize import (
    ManifestNotFound,
    MultiPartModel,
    NonGgufWeights,
    QuantizePlan,
    QuantizeRefusal,
    resolve_source_gguf,
    select_target_quant,
)

GGUF_MAGIC = b"GGUF" + b"\x00" * 16


def _fake_store(tmp_path, name, tag, layers, blob_heads):
    manifest_dir = tmp_path / "manifests" / "registry.ollama.ai" / "library" / name
    manifest_dir.mkdir(parents=True)
    manifest = {"schemaVersion": 2, "layers": [
        {"digest": f"sha256:{d}", "mediaType": mt, "size": 1} for d, mt in layers
    ]}
    (manifest_dir / tag).write_text(json.dumps(manifest), encoding="utf-8")
    blobs = tmp_path / "blobs"
    blobs.mkdir(exist_ok=True)
    for d, head in blob_heads.items():
        (blobs / f"sha256-{d}").write_bytes(head)
    return tmp_path


def _box(vram_gb: float, ram_gb: float = 32.0) -> SystemInfo:
    return SystemInfo(
        os="Windows", cpu="AMD Ryzen 5 7600", cpu_cores=6, ram_gb=ram_gb,
        gpus=[GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=vram_gb, backend="rocm")],
        total_vram_gb=vram_gb,
    )


def _model(params_b=20.0, context=65536, **kw):
    kw.setdefault("is_moe", False)
    return ModelEntry(id="m:tag", name="M", provider="p", params_b=params_b,
                      arch="custom", context=context, use_cases=["coding"], **kw)


def test_select_target_quant_picks_lower_quant_for_tight_vram():
    # 20B on 12 GB: Q4 misses at every practical context, Q3_K_M fits at 8192.
    plan = select_target_quant(_model(20.0), _box(12.0, ram_gb=0.0))
    assert isinstance(plan, QuantizePlan)
    assert plan.target_quant == "Q3_K_M"
    assert plan.context == 8192
    assert plan.quality_cost == 8.0
    assert plan.estimated_size_gb == pytest.approx(20.0 * 0.48)


def test_select_target_quant_refuses_when_model_already_fits():
    with pytest.raises(QuantizeRefusal, match="already fits"):
        select_target_quant(_model(3.0), _box(24.0))


def test_select_target_quant_refuses_below_ladder_with_math():
    # 70B on 8 GB: even Q2_K cannot fit -> honest refusal stating the bpp budget.
    with pytest.raises(QuantizeRefusal) as exc:
        select_target_quant(_model(70.0), _box(8.0, ram_gb=0.0))
    assert "bpp" in exc.value.reason


def test_vram_override_shrinks_the_context_budget():
    tight = select_target_quant(_model(20.0), _box(12.0, ram_gb=0.0))
    squeezed = select_target_quant(_model(20.0), _box(12.0, ram_gb=0.0), vram_override_gb=10.5)
    assert tight.target_quant == squeezed.target_quant == "Q3_K_M"
    assert squeezed.context < tight.context


def test_vram_override_can_make_quantize_pointless():
    # The same model that needs Q3 at 12 GB already fits at Q4 with 16 GB.
    with pytest.raises(QuantizeRefusal, match="already fits"):
        select_target_quant(_model(20.0), _box(12.0, ram_gb=0.0), vram_override_gb=16.0)


def test_select_target_quant_moe_uses_active_params_for_kv():
    moe = _model(params_b=30.0, is_moe=True, active_params_b=3.6)
    plan = select_target_quant(moe, _box(12.0, ram_gb=0.0))
    assert plan.target_quant in ("Q3_K_M", "Q2_K", "Q4_K_M")
    assert plan.estimated_size_gb == pytest.approx(30.0 * plan.target_bpp)


def test_resolve_source_gguf_finds_single_weights_blob(tmp_path):
    d = "a" * 64
    root = _fake_store(tmp_path, "qwen3", "4b",
                       [(d, "application/vnd.ollama.image.model"),
                        ("b" * 64, "application/vnd.ollama.image.license")],
                       {d: GGUF_MAGIC})
    assert resolve_source_gguf("qwen3:4b", store_root=root) == root / "blobs" / f"sha256-{d}"


def test_resolve_source_gguf_defaults_latest_tag(tmp_path):
    d = "c" * 64
    root = _fake_store(tmp_path, "llama3.2", "latest",
                       [(d, "application/vnd.ollama.image.model")], {d: GGUF_MAGIC})
    assert resolve_source_gguf("llama3.2", store_root=root).name == f"sha256-{d}"


def test_resolve_source_gguf_refuses_multipart(tmp_path):
    root = _fake_store(tmp_path, "big", "70b",
                       [("a" * 64, "application/vnd.ollama.image.model"),
                        ("b" * 64, "application/vnd.ollama.image.model")],
                       {"a" * 64: GGUF_MAGIC, "b" * 64: GGUF_MAGIC})
    with pytest.raises(MultiPartModel):
        resolve_source_gguf("big:70b", store_root=root)


def test_resolve_source_gguf_refuses_non_gguf_weights(tmp_path):
    d = "d" * 64
    root = _fake_store(tmp_path, "st", "1b",
                       [(d, "application/vnd.ollama.image.model")], {d: b"\x00" * 8})
    with pytest.raises(NonGgufWeights):
        resolve_source_gguf("st:1b", store_root=root)


def test_resolve_source_gguf_missing_manifest(tmp_path):
    with pytest.raises(ManifestNotFound):
        resolve_source_gguf("nope:1b", store_root=tmp_path)
