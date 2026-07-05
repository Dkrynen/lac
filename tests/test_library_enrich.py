from __future__ import annotations

from backend.cookbook.library import enrich_library_models


def test_enrich_matches_catalog_family_and_sets_vram_q4():
    models = [{"name": "qwen3", "description": "", "capabilities": [],
               "sizes": ["8B"], "pulls": "1M", "tag_count": "5"}]
    out = enrich_library_models(models, system_vram=16.0)
    assert out[0]["vram_q4"] > 0
    assert out[0]["fit"] in ("gpu", "offload", "too_big")


def test_enrich_falls_back_to_advertised_size_when_no_catalog_match():
    models = [{"name": "totally-unknown-model", "description": "",
               "capabilities": [], "sizes": ["7B"], "pulls": "0", "tag_count": "1"}]
    out = enrich_library_models(models, system_vram=16.0)
    assert out[0]["vram_q4"] > 0
    assert out[0]["params_b"] == 7.0


def test_enrich_unknown_without_sizes_gets_unknown_fit():
    models = [{"name": "mystery", "description": "", "capabilities": [],
               "sizes": [], "pulls": "0", "tag_count": "0"}]
    out = enrich_library_models(models, system_vram=16.0)
    assert out[0]["fit"] == "unknown"
    assert "vram_q4" not in out[0]
