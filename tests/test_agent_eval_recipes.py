from __future__ import annotations

import json

import backend.agent_eval.recipes as recipes_mod
from backend.cookbook.hardware import GPUInfo, SystemInfo
from backend.agent_eval.ledger import LedgerVerification
from backend.agent_eval.recipes import (
    RecipeCard,
    aggregate_recipes,
    hardware_class,
    lookup_recipe,
    proven_for,
)


def _info(gpu_name="", vram=0.0, backend="cuda", apple=False, ram=32.0):
    return SystemInfo(
        os="Test", cpu="c", cpu_cores=8, ram_gb=ram,
        gpus=[GPUInfo(name=gpu_name, vram_gb=vram, backend=backend)] if gpu_name else [],
        total_vram_gb=vram,
        is_apple_silicon=apple,
    )


def test_hardware_class_amd():
    assert hardware_class(_info("AMD Radeon RX 6800 XT", 16.0, "rocm")) == "amd-16gb"


def test_hardware_class_nvidia():
    assert hardware_class(_info("NVIDIA GeForce RTX 4090", 24.0, "cuda")) == "nvidia-24gb"


def test_hardware_class_apple_unified_memory():
    assert hardware_class(_info("Apple M2 Max", 32.0, "metal", apple=True)) == "apple-32gb"


def test_hardware_class_cpu_only_uses_ram():
    assert hardware_class(_info("", 0.0, "", ram=16.0)) == "cpu-16gb"


def test_hardware_class_vram_tier_rounds_to_nearest():
    assert hardware_class(_info("AMD Radeon RX 6800 XT", 15.9, "rocm")) == "amd-16gb"


def test_recipe_card_defaults_and_fields():
    card = RecipeCard(
        model_id="gpt-oss:20b", hardware_class="amd-16gb", tokens_per_second=94.67,
        quant="Q4_K_M", context=65536, trials=3, evidence_run="2026-07-28-eval",
    )
    assert card.use_case == "agent"
    assert card.tokens_per_second == 94.67
    assert card.trials == 3


def _make_run(root, name, hw_class, model, tok_s_list, quant="Q4_K_M", context=65536, manifest=True):
    run_root = root / name
    for i, tok_s in enumerate(tok_s_list):
        trial_dir = run_root / "trials" / f"{i:03d}" / "raw_ollama"
        trial_dir.mkdir(parents=True, exist_ok=True)
        (trial_dir / "result.json").write_text(json.dumps({
            "arm": "raw_ollama", "model": model, "runtime": "ollama",
            "completed": True, "timed_out": False, "response": "ok",
            "wall_time_ms": 1000.0, "metrics": {"tokens_per_second": tok_s},
        }), encoding="utf-8")
    if manifest:
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "run_manifest.json").write_text(json.dumps({
            "hardware_class": hw_class, "quant": quant, "context": context, "use_case": "agent",
        }), encoding="utf-8")
    return run_root


def _seal_all(monkeypatch):
    monkeypatch.setattr(recipes_mod, "verify_evidence", lambda root: LedgerVerification(True))


def test_aggregate_projects_card_from_sealed_runs(tmp_path, monkeypatch):
    _seal_all(monkeypatch)
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    _make_run(tmp_path, "r3", "amd-16gb", "gpt-oss:20b", [92.0])
    cards = aggregate_recipes(tmp_path, min_trials=3)
    card = cards[("gpt-oss:20b", "amd-16gb")]
    assert card.trials == 3
    assert card.tokens_per_second == 92.0  # median of 90/92/94
    assert card.quant == "Q4_K_M"
    assert card.hardware_class == "amd-16gb"


def test_aggregate_skips_unsealed_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(recipes_mod, "verify_evidence", lambda root: LedgerVerification(root.name == "r1"))
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    _make_run(tmp_path, "r3", "amd-16gb", "gpt-oss:20b", [92.0])
    cards = aggregate_recipes(tmp_path, min_trials=3)
    assert ("gpt-oss:20b", "amd-16gb") not in cards  # only 1 sealed run


def test_aggregate_skips_runs_without_manifest(tmp_path, monkeypatch):
    _seal_all(monkeypatch)
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    _make_run(tmp_path, "r3", "amd-16gb", "gpt-oss:20b", [92.0], manifest=False)
    cards = aggregate_recipes(tmp_path, min_trials=3)
    assert ("gpt-oss:20b", "amd-16gb") not in cards  # only 2 with a manifest


def test_aggregate_requires_min_trials(tmp_path, monkeypatch):
    _seal_all(monkeypatch)
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    assert aggregate_recipes(tmp_path, min_trials=3) == {}


def test_aggregate_ignores_failed_results(tmp_path, monkeypatch):
    _seal_all(monkeypatch)
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    run_root = _make_run(tmp_path, "r3", "amd-16gb", "gpt-oss:20b", [])
    failed_dir = run_root / "trials" / "000" / "raw_ollama"
    failed_dir.mkdir(parents=True, exist_ok=True)
    (failed_dir / "result.json").write_text(json.dumps({
        "arm": "raw_ollama", "model": "gpt-oss:20b", "runtime": "ollama",
        "completed": False, "timed_out": True, "response": "",
        "wall_time_ms": 9999.0, "metrics": {},
    }), encoding="utf-8")
    cards = aggregate_recipes(tmp_path, min_trials=3)
    assert ("gpt-oss:20b", "amd-16gb") not in cards  # only 2 completed


def test_lookup_recipe_by_model_and_class(tmp_path, monkeypatch):
    _seal_all(monkeypatch)
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    _make_run(tmp_path, "r3", "amd-16gb", "gpt-oss:20b", [92.0])
    cards = aggregate_recipes(tmp_path, min_trials=3)
    amd16 = _info("AMD Radeon RX 6800 XT", 16.0, "rocm")
    assert lookup_recipe(cards, "gpt-oss:20b", amd16) is not None
    assert lookup_recipe(cards, "gpt-oss:20b", _info("NVIDIA GeForce RTX 4090", 24.0, "cuda")) is None
    assert lookup_recipe(cards, "qwen3.6:27b", amd16) is None


def test_proven_for_returns_card_when_evidence_exists(tmp_path, monkeypatch):
    _seal_all(monkeypatch)
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    _make_run(tmp_path, "r3", "amd-16gb", "gpt-oss:20b", [92.0])
    card = proven_for(_info("AMD Radeon RX 6800 XT", 16.0, "rocm"), "gpt-oss:20b", root=tmp_path)
    assert card is not None
    assert card.tokens_per_second == 92.0


def test_proven_for_returns_none_when_no_evidence_root(tmp_path):
    assert proven_for(_info("AMD Radeon RX 6800 XT", 16.0, "rocm"), "gpt-oss:20b", root=tmp_path / "missing") is None


def test_proven_for_returns_none_for_unproven_model(tmp_path, monkeypatch):
    _seal_all(monkeypatch)
    _make_run(tmp_path, "r1", "amd-16gb", "gpt-oss:20b", [90.0])
    _make_run(tmp_path, "r2", "amd-16gb", "gpt-oss:20b", [94.0])
    _make_run(tmp_path, "r3", "amd-16gb", "gpt-oss:20b", [92.0])
    assert proven_for(_info("AMD Radeon RX 6800 XT", 16.0, "rocm"), "qwen3.6:27b", root=tmp_path) is None
