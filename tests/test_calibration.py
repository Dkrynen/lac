# tests/test_calibration.py
from __future__ import annotations
from backend.cookbook.calibration import parse_model_tag


def test_parse_bare_tag_defaults_to_q4km():
    assert parse_model_tag("qwen3:30b-a3b") == ("qwen3:30b-a3b", "Q4_K_M")

def test_parse_quant_suffix():
    assert parse_model_tag("qwen3:30b-a3b-q8_0") == ("qwen3:30b-a3b", "Q8")
    assert parse_model_tag("qwen3:30b-a3b-q4_K_M") == ("qwen3:30b-a3b", "Q4_K_M")
    assert parse_model_tag("qwen3:30b-a3b-fp16") == ("qwen3:30b-a3b", "F16")

def test_parse_hf_sub4bit():
    assert parse_model_tag("hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit") == (
        "hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit", "1.58bit")

def test_parse_unknown_returns_none():
    assert parse_model_tag("totally-made-up:99b") is None


from backend.cookbook.calibration import machine_fingerprint
from backend.cookbook.hardware import SystemInfo, GPUInfo


def _info():
    return SystemInfo(os="Windows", cpu="Ryzen 5 7600", cpu_cores=6, ram_gb=30.9,
                      gpus=[GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="rocm")],
                      total_vram_gb=16.0)

def test_fingerprint_stable():
    s = {"ollama_version": "0.31.1", "backend": "vulkan"}
    assert machine_fingerprint(_info(), s) == machine_fingerprint(_info(), s)

def test_fingerprint_changes_with_ollama_version():
    a = machine_fingerprint(_info(), {"ollama_version": "0.31.1", "backend": "vulkan"})
    b = machine_fingerprint(_info(), {"ollama_version": "0.32.0", "backend": "vulkan"})
    assert a != b

def test_fingerprint_changes_with_gpu():
    info2 = _info(); info2.gpus = [GPUInfo("NVIDIA RTX 4090", 24.0, backend="cuda")]
    s = {"ollama_version": "0.31.1", "backend": "vulkan"}
    assert machine_fingerprint(_info(), s) != machine_fingerprint(info2, s)


import json
from backend.cookbook.calibration import load_calibration, detect_stack

_STACK = {"ollama_version": "0.31.1", "backend": "vulkan"}

def _write_results(path, info, rows):
    from backend.cookbook.calibration import machine_fingerprint
    fp = machine_fingerprint(info, _STACK)
    with open(path, "w") as f:
        for tag, tps, extra in rows:
            e = {"model": tag, "tokens_per_second": tps, "eval_count": 128}
            if extra != "no-fp":
                e["fingerprint"] = fp if extra == "match" else "deadbeef0000"
            f.write(json.dumps(e) + "\n")

def test_load_calibration_measured_override(tmp_path):
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [("falcon3:3b", 178.0, "match")])
    cal = load_calibration(info, _STACK, str(p))
    assert cal.measured[("falcon3:3b", "Q4_K_M")].median_tps == 178.0

def test_load_calibration_regime_factor(tmp_path):
    info = _info()
    p = tmp_path / "results.jsonl"
    # falcon = gpu regime; if theoretical ~186 and real 178, factor ~0.96
    _write_results(p, info, [("falcon3:3b", 178.0, "match")])
    cal = load_calibration(info, _STACK, str(p))
    assert 0.7 <= cal.regime_factor["gpu"] <= 1.2

def test_load_calibration_ignores_foreign_fingerprint(tmp_path):
    info = _info()
    p = tmp_path / "results.jsonl"
    _write_results(p, info, [("falcon3:3b", 999.0, "foreign")])
    cal = load_calibration(info, _STACK, str(p))
    assert ("falcon3:3b", "Q4_K_M") not in cal.measured
    assert cal.regime_factor.get("gpu", 1.0) == 1.0  # no matching data -> default

def test_load_calibration_empty(tmp_path):
    cal = load_calibration(_info(), _STACK, str(tmp_path / "none.jsonl"))
    assert cal.n == 0 and cal.regime_factor == {}
