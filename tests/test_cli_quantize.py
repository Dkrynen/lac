from __future__ import annotations

import pytest


def _gpu_info(vram_gb: float, ram_gb: float = 32.0):
    from backend.cookbook.hardware import GPUInfo, SystemInfo
    return SystemInfo(
        os="Test", cpu="Test", cpu_cores=8, ram_gb=ram_gb,
        gpus=[GPUInfo("AMD Radeon RX 6800 XT", vram_gb, backend="rocm")],
        total_vram_gb=vram_gb,
    )


def test_parser_accepts_quantize_args():
    import cli as cli_mod
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["quantize", "qwen3:14b", "--vram", "12", "-y"])
    assert args.command == "quantize"
    assert args.model == "qwen3:14b"
    assert args.vram == 12.0
    assert args.yes is True
    assert args.requantize is False


def test_parser_accepts_requantize_flag():
    import cli as cli_mod
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["quantize", "qwen3:14b", "--requantize"])
    assert args.requantize is True


def test_cmd_quantize_refusal_prints_reason_and_exits(monkeypatch, capsys):
    import cli as cli_mod
    import backend.cookbook.hardware as hw_mod

    monkeypatch.setattr(hw_mod, "detect", lambda: _gpu_info(24.0))
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["quantize", "qwen3:4b"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_quantize(args)
    assert e.value.code == 1
    assert "already fits" in capsys.readouterr().err


def test_cmd_quantize_unknown_model_exits(capsys):
    import cli as cli_mod
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["quantize", "nope:1b", "--vram", "8"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_quantize(args)
    assert e.value.code == 1
    assert "Unknown model" in capsys.readouterr().err


def test_cmd_quantize_happy_path(monkeypatch, capsys):
    import cli as cli_mod
    import backend.cookbook.quantize as quantize_mod
    from backend.cookbook.quantize import QuantizePlan, QuantizeResult

    plan = QuantizePlan(target_quant="Q3_K_M", target_bpp=0.48,
                        estimated_size_gb=7.1, quality_cost=8.0, context=4096)
    captured = {}

    def fake_quantize(model_id, **kw):
        captured.update(kw)
        return QuantizeResult(variant="qwen3:14b-q3_k_m-fit", plan=plan,
                              source=model_id, catalog_id="qwen3:14b-q3_k_m-fit")

    monkeypatch.setattr(quantize_mod, "quantize_model", fake_quantize)
    monkeypatch.setattr(cli_mod, "ollama", lambda method, path, body=None, timeout=30: {
        "models": [{"name": "qwen3:14b:latest",
                    "details": {"quantization_level": "Q4_K_M"}}],
    })

    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["quantize", "qwen3:14b", "--vram", "8", "-y"])
    cli_mod.cmd_quantize(args)

    out = capsys.readouterr().out
    assert captured["vram_override_gb"] == 8.0
    assert "qwen3:14b-q3_k_m-fit" in out
    assert "Q3_K_M" in out


def test_recommend_model_fits_at_quant_hints_lac_quantize(monkeypatch, capsys):
    import cli as cli_mod
    import backend.cookbook.hardware as hw_mod

    monkeypatch.setattr(hw_mod, "detect", lambda: _gpu_info(12.0, ram_gb=0.0))
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["recommend", "--model", "qwen3.6:27b"])
    cli_mod.cmd_recommend(args)

    out = capsys.readouterr().out
    assert "fits at" in out
    assert "lac quantize" in out


def test_recommend_model_quantize_to_fit_states_limit(monkeypatch, capsys):
    import cli as cli_mod
    import backend.cookbook.hardware as hw_mod

    monkeypatch.setattr(hw_mod, "detect", lambda: _gpu_info(4.0, ram_gb=0.0))
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["recommend", "--model", "qwen3.6:27b"])
    cli_mod.cmd_recommend(args)

    out = capsys.readouterr().out
    assert "lac quantize qwen3.6:27b" not in out


def test_cmd_quantize_passes_requantize_flag(monkeypatch, capsys):
    import cli as cli_mod
    import backend.cookbook.quantize as quantize_mod
    from backend.cookbook.quantize import QuantizePlan, QuantizeResult

    plan = QuantizePlan(target_quant="Q3_K_M", target_bpp=0.48,
                        estimated_size_gb=7.1, quality_cost=8.0, context=4096)
    captured = {}

    def fake_quantize(model_id, **kw):
        captured.update(kw)
        return QuantizeResult(variant="qwen3:14b-q3_k_m-fit", plan=plan,
                              source=model_id, catalog_id="qwen3:14b-q3_k_m-fit")

    monkeypatch.setattr(quantize_mod, "quantize_model", fake_quantize)
    monkeypatch.setattr(cli_mod, "ollama", lambda method, path, body=None, timeout=30: {
        "models": [{"name": "qwen3:14b:latest",
                    "details": {"quantization_level": "Q4_K_M"}}],
    })

    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["quantize", "qwen3:14b", "--vram", "8", "-y", "--requantize"])
    cli_mod.cmd_quantize(args)
    assert captured["requantize"] is True
