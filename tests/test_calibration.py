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
