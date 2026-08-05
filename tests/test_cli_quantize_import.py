from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def test_sha256_file_streams_the_correct_digest(tmp_path):
    import cli as cli_mod
    f = tmp_path / "blob.gguf"
    payload = b"GGUF" + b"x" * 4096
    f.write_bytes(payload)
    assert cli_mod.sha256_file(f) == hashlib.sha256(payload).hexdigest()


def test_import_gguf_uploads_blob_then_creates_from_files(tmp_path):
    import cli as cli_mod
    f = tmp_path / "out.gguf"
    f.write_bytes(b"GGUF" + b"y" * 64)
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    calls = {}

    def fake_upload(path, digest_hex):
        calls["upload"] = (Path(path), digest_hex)

    def fake_stream(body):
        calls["create_body"] = body
        yield {"status": "success"}

    cli_mod.ollama_import_gguf("m-q3-fit", f, upload_fn=fake_upload, create_stream_fn=fake_stream)

    assert calls["upload"] == (f, digest)
    assert calls["create_body"] == {
        "model": "m-q3-fit",
        "files": {"model.gguf": f"sha256:{digest}"},
        "stream": True,
    }


def test_import_gguf_raises_on_create_error(tmp_path):
    import cli as cli_mod
    f = tmp_path / "out.gguf"
    f.write_bytes(b"GGUF")

    def fake_stream(body):
        yield {"error": "failed to validate GGUF"}

    with pytest.raises(RuntimeError, match="failed to validate GGUF"):
        cli_mod.ollama_import_gguf("m-q3-fit", f, upload_fn=lambda p, d: None,
                                   create_stream_fn=fake_stream)


def test_import_gguf_propagates_upload_failure(tmp_path):
    import cli as cli_mod
    f = tmp_path / "out.gguf"
    f.write_bytes(b"GGUF")

    def boom(path, digest_hex):
        raise RuntimeError("blob upload failed: disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        cli_mod.ollama_import_gguf("m-q3-fit", f, upload_fn=boom,
                                   create_stream_fn=lambda body: iter([]))


def test_cmd_quantize_create_seam_routes_through_blob_import(tmp_path, monkeypatch):
    import cli as cli_mod
    import backend.cookbook.quantize as quantize_mod
    captured = {}

    def fake_quantize(model_id, **kw):
        captured["create"] = kw["create_from_file"]
        from backend.cookbook.quantize import QuantizePlan, QuantizeResult
        plan = QuantizePlan(target_quant="Q3_K_M", target_bpp=0.48,
                            estimated_size_gb=1.0, quality_cost=8.0, context=2048)
        return QuantizeResult(variant="m-q3_k_m-fit", plan=plan, source=model_id,
                              catalog_id="m-q3_k_m-fit")

    imports = []
    monkeypatch.setattr(quantize_mod, "quantize_model", fake_quantize)
    monkeypatch.setattr(quantize_mod, "load_models", lambda: [
        type("M", (), {"id": "qwen3:0.6b", "name": "Qwen3 0.6B", "provider": "Qwen",
                       "params_b": 0.6, "arch": "qwen3", "context": 32768,
                       "use_cases": ["coding", "general"], "is_moe": False})()
    ])
    monkeypatch.setattr(cli_mod, "ollama_import_gguf",
                        lambda name, path: imports.append((name, Path(path))))
    monkeypatch.setattr(cli_mod, "ollama", lambda method, path, body=None, timeout=30: {
        "models": [{"name": "qwen3:0.6b:latest",
                    "details": {"quantization_level": "Q4_K_M"}}],
    })

    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["quantize", "qwen3:0.6b", "--vram", "0.8", "-y"])
    cli_mod.cmd_quantize(args)

    captured["create"]("m-q3_k_m-fit", tmp_path / "staged.gguf")
    assert imports == [("m-q3_k_m-fit", tmp_path / "staged.gguf")]
