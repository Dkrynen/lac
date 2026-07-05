from __future__ import annotations

import pytest


def test_cli_browse_returns_results_against_real_cache(monkeypatch, capsys):
    """The headline bug: cmd_browse filtered models = [m for m in models if
    m.get("vram_q4", 0) > 0] against the RAW scraped library_cache.json,
    which has no vram_q4 field at all -- nuking the entire 236-model
    result set every time. This runs against the real on-disk cache."""
    import cli as cli_mod
    import backend.cookbook.hardware as hw_mod
    from backend.cookbook.hardware import SystemInfo, GPUInfo

    monkeypatch.setattr(hw_mod, "detect", lambda: SystemInfo(
        os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
        gpus=[GPUInfo("Test GPU", 16.0, backend="cuda")], total_vram_gb=16.0,
    ))

    parser = cli_mod.build_parser()
    args = parser.parse_args(["browse", "qwen"])
    cli_mod.cmd_browse(args)

    out = capsys.readouterr().out
    assert "Model Library (0 variants)" not in out
    assert "GB Q4" in out


def test_cli_recommend_rejects_zero_top_k(capsys):
    import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["recommend", "--top-k", "0"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_recommend(args)
    assert e.value.code == 1
    assert "--top-k must be a positive integer" in capsys.readouterr().err


def test_cli_recommend_rejects_negative_top_k(capsys):
    import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["recommend", "--top-k", "-5"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_recommend(args)
    assert e.value.code == 1
