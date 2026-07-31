from __future__ import annotations

import backend.cookbook.fit as fit_mod
from backend.cookbook.hardware import GPUInfo, SystemInfo
from backend.cookbook.recommend import load_models
from backend.cookbook.fit import fit_verdict, recommend_distill


def _box(vram_gb: float, ram_gb: float = 32.0) -> SystemInfo:
    return SystemInfo(
        os="Windows", cpu="AMD Ryzen 5 7600", cpu_cores=6, ram_gb=ram_gb,
        gpus=[GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=vram_gb, backend="rocm")],
        total_vram_gb=vram_gb,
    )


def _model(model_id: str):
    return next(m for m in load_models() if m.id == model_id)


def test_fits_at_generous_vram():
    v = fit_verdict(_model("qwen3:4b"), _box(16.0))
    assert v.kind == "fits"
    assert v.quant == "Q4_K_M"


def test_fits_at_quant_without_spill():
    # No RAM spill (discrete-only, like the web UI's allow_spill=0): a 27.8B
    # dense model on 12GB fits only below Q4 (~16GB at Q4).
    v = fit_verdict(_model("qwen3.6:27b"), _box(12.0, ram_gb=0.0))
    assert v.kind == "fits_at_quant"
    assert v.quality_cost > 0


def test_tiny_box_forces_quantize_or_too_big():
    v = fit_verdict(_model("qwen3.6:27b"), _box(4.0, ram_gb=0.0))
    assert v.kind in ("quantize_to_fit", "too_big")


def test_too_big_when_kv_alone_exceeds_budget():
    v = fit_verdict(_model("deepseek-v3:671b"), _box(1.0, ram_gb=0.0))
    assert v.kind == "too_big"


def test_distill_routing_finds_verified_relative():
    sugg = recommend_distill(_box(16.0), "deepseek-r1:70b")
    assert sugg is not None
    assert sugg.verified is True
    assert sugg.model.id != "deepseek-r1:70b"


def test_distill_routing_none_when_target_fits():
    assert recommend_distill(_box(24.0), "qwen3.6:27b") is None


def test_unverified_lineage_never_asserts_certainty(monkeypatch):
    target_id = "yi:34b"  # no curated distill_of/family relatives
    fake = [(_model("qwen3.6:27b"), "shares a base (from upstream metadata)")]
    monkeypatch.setattr(fit_mod, "_unverified_relatives", lambda model_id: fake)
    sugg = recommend_distill(_box(12.0, ram_gb=0.0), target_id)
    assert sugg is not None
    assert sugg.verified is False
    assert "is a distill of" not in sugg.note.lower()
    assert "upstream metadata" in sugg.note.lower()


def test_fits_verdict_carries_a_context():
    v = fit_verdict(_model("qwen3.6:27b"), _box(24.0))
    assert v.kind == "fits"
    assert v.context is not None and v.context > 0
