from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.cookbook.hardware import GPUInfo, SystemInfo, build_compute_tiers
from backend.cookbook.recommend import ModelEntry
from backend.cookbook.quantize import (
    InsufficientDiskSpace,
    ManifestNotFound,
    MultiPartModel,
    NonGgufWeights,
    QuantizePlan,
    QuantizeRefusal,
    QuantizerNotFound,
    QuantizeRunFailed,
    check_disk_space,
    find_quantizer,
    required_space_gb,
    resolve_source_gguf,
    run_quantize,
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


def test_find_quantizer_explicit_override_wins(tmp_path):
    tool = tmp_path / "llama-quantize.exe"
    tool.write_bytes(b"MZ")
    assert find_quantizer(override=str(tool)) == tool


def test_find_quantizer_missing_override_refuses(tmp_path):
    with pytest.raises(QuantizerNotFound):
        find_quantizer(override=str(tmp_path / "absent.exe"))


def test_find_quantizer_env_var(tmp_path, monkeypatch):
    tool = tmp_path / "llama-quantize"
    tool.write_bytes(b"#")
    monkeypatch.setenv("LAC_LLAMA_QUANTIZE", str(tool))
    assert find_quantizer() == tool


def test_find_quantizer_path_discovery(monkeypatch):
    monkeypatch.delenv("LAC_LLAMA_QUANTIZE", raising=False)
    seen = []

    def fake_which(name):
        seen.append(name)
        return "/usr/bin/llama-quantize" if name == "llama-quantize" else None

    monkeypatch.setattr("backend.cookbook.quantize.shutil.which", fake_which)
    assert find_quantizer() == Path("/usr/bin/llama-quantize")
    assert seen[0] == "llama-quantize"


def test_find_quantizer_not_found_has_install_guidance(monkeypatch):
    monkeypatch.delenv("LAC_LLAMA_QUANTIZE", raising=False)
    monkeypatch.setattr("backend.cookbook.quantize.shutil.which", lambda name: None)
    with pytest.raises(QuantizerNotFound) as exc:
        find_quantizer()
    assert "llama.cpp" in exc.value.reason


class _FakeRun:
    def __init__(self, returncode, write_dst=False, stdout="", stderr=""):
        self.returncode = returncode
        self.write_dst = write_dst
        self.stdout = stdout
        self.stderr = stderr
        self.cmd = None

    def __call__(self, cmd, **kw):
        self.cmd = cmd
        if self.write_dst:
            Path(cmd[2]).write_bytes(b"partial")
        return self


def test_run_quantize_success_keeps_output(tmp_path):
    src = tmp_path / "src.gguf"
    src.write_bytes(GGUF_MAGIC)
    dst = tmp_path / "out.gguf"
    fake = _FakeRun(0, write_dst=True, stdout="done")
    run_quantize(src, dst, "Q3_K_M", quantizer=Path("llama-quantize"), run=fake)
    assert dst.exists()
    assert fake.cmd == ["llama-quantize", str(src), str(dst), "Q3_K_M"]


def test_run_quantize_failure_deletes_partial_output(tmp_path):
    src = tmp_path / "src.gguf"
    src.write_bytes(GGUF_MAGIC)
    dst = tmp_path / "out.gguf"
    fake = _FakeRun(1, write_dst=True, stderr="boom")
    with pytest.raises(QuantizeRunFailed) as exc:
        run_quantize(src, dst, "Q3_K_M", quantizer=Path("llama-quantize"), run=fake)
    assert not dst.exists()
    assert "boom" in exc.value.reason


def _plan(size_gb=10.0):
    return QuantizePlan(target_quant="Q3_K_M", target_bpp=0.48,
                        estimated_size_gb=size_gb, quality_cost=8.0, context=8192)


def test_required_space_covers_staging_plus_import_copy():
    assert required_space_gb(_plan(10.0)) == pytest.approx(22.0)


def _usage(free_gb_by_path):
    def fake(path):
        for key, free in free_gb_by_path.items():
            if key in str(path):
                return type("U", (), {"free": int(free * 1024**3)})()
        return type("U", (), {"free": int(1000 * 1024**3)})()
    return fake


def test_check_disk_space_passes_when_both_volumes_have_room(tmp_path):
    staging = tmp_path / "staging"
    store = tmp_path / "store"
    staging.mkdir()
    store.mkdir()
    check_disk_space(staging, store, 22.0, disk_usage=_usage({}))


def test_check_disk_space_refuses_short_staging_volume(tmp_path):
    staging = tmp_path / "staging"
    store = tmp_path / "store"
    staging.mkdir()
    store.mkdir()
    with pytest.raises(InsufficientDiskSpace, match="staging"):
        check_disk_space(staging, store, 22.0, disk_usage=_usage({"staging": 5.0}))


def test_check_disk_space_refuses_short_store_volume(tmp_path):
    staging = tmp_path / "staging"
    store = tmp_path / "store"
    staging.mkdir()
    store.mkdir()
    with pytest.raises(InsufficientDiskSpace, match="store"):
        check_disk_space(staging, store, 22.0, disk_usage=_usage({"store": 5.0}))


import backend.cookbook.quantize as quantize_mod
from backend.cookbook.quantize import (
    QuantizeResult,
    quantize_model,
    quantize_variant_name,
)


def test_quantize_variant_name_follows_agent_variant_convention():
    assert quantize_variant_name("qwen3:14b", "Q3_K_M") == "qwen3:14b-q3_k_m-fit"


def _catalog_model():
    return ModelEntry(id="qwen3:14b", name="Qwen3 14B", provider="Qwen",
                      params_b=14.0, arch="qwen3", context=32768,
                      use_cases=["coding", "general"], is_moe=False)


def _orchestra(tmp_path, monkeypatch, *,
               names=("qwen3:14b",), levels=None, store_layers=None,
               run=None, create=None, registered=None,
               disk_free_gb=1000.0, quantizer_file=True):
    monkeypatch.setattr(quantize_mod, "load_models", lambda: [_catalog_model()])
    monkeypatch.setattr(quantize_mod, "register_custom_model",
                        lambda entry: registered.append(entry) if registered is not None else None)
    monkeypatch.setattr(quantize_mod, "STAGING_DIR", tmp_path / "staging")
    store = tmp_path / "store"
    d = "a" * 64
    layers = store_layers if store_layers is not None else [(d, "application/vnd.ollama.image.model")]
    _fake_store(store, "qwen3", "14b", layers, {d: GGUF_MAGIC})
    quantizer = None
    if quantizer_file:
        quantizer = tmp_path / "llama-quantize.exe"
        quantizer.write_bytes(b"MZ")
    return dict(
        list_names=lambda: list(names),
        quant_levels=lambda: (levels if levels is not None else {"qwen3:14b": "Q4_K_M"}),
        create_from_file=create if create is not None else (lambda name, path: None),
        store_root=store,
        quantizer=str(quantizer) if quantizer else None,
        run=run if run is not None else _FakeRun(0, write_dst=True),
        disk_usage=_usage({}) if disk_free_gb > 500 else _usage({"": disk_free_gb}),
    )


def test_quantize_model_happy_path(tmp_path, monkeypatch):
    created = []
    registered = []
    kw = _orchestra(tmp_path, monkeypatch,
                    create=lambda name, path: created.append((name, Path(path))),
                    registered=registered)
    result = quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)
    assert isinstance(result, QuantizeResult)
    assert result.variant == "qwen3:14b-q3_k_m-fit"
    assert result.plan.target_quant == "Q3_K_M"
    assert result.source == "qwen3:14b"
    assert created == [(result.variant, kw["run"].cmd and Path(kw["run"].cmd[2]))]
    assert registered[0]["id"] == result.variant
    assert registered[0]["quantized_from"] == "qwen3:14b"
    assert not Path(kw["run"].cmd[2]).exists()


def test_quantize_model_refuses_unknown_model(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch)
    with pytest.raises(QuantizeRefusal, match="Unknown model"):
        quantize_model("nope:1b", vram_override_gb=8.0, **kw)


def test_quantize_model_refuses_uninstalled_source(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch, names=("other:1b",))
    with pytest.raises(QuantizeRefusal, match="lac pull"):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)


def test_quantize_model_refuses_when_model_already_fits(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch)
    with pytest.raises(QuantizeRefusal, match="already fits"):
        quantize_model("qwen3:14b", vram_override_gb=48.0, **kw)


def test_quantize_model_refuses_when_source_already_at_target(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch, levels={"qwen3:14b": "Q3_K_M"})
    with pytest.raises(QuantizeRefusal, match="already"):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)


def test_quantize_model_refuses_upward_requant(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch, levels={"qwen3:14b": "Q2_K"})
    with pytest.raises(QuantizeRefusal, match="cannot"):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)


def test_quantize_model_refuses_existing_variant(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch,
                    names=("qwen3:14b", "qwen3:14b-q3_k_m-fit"))
    with pytest.raises(QuantizeRefusal, match="exists"):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)


def test_quantize_model_refuses_multipart_store(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch, store_layers=[
        ("a" * 64, "application/vnd.ollama.image.model"),
        ("b" * 64, "application/vnd.ollama.image.model"),
    ])
    with pytest.raises(MultiPartModel):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)


def test_quantize_model_refuses_insufficient_disk(tmp_path, monkeypatch):
    kw = _orchestra(tmp_path, monkeypatch, disk_free_gb=1.0)
    with pytest.raises(InsufficientDiskSpace):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)


def test_quantize_model_refuses_missing_quantizer(tmp_path, monkeypatch):
    monkeypatch.delenv("LAC_LLAMA_QUANTIZE", raising=False)
    monkeypatch.setattr("backend.cookbook.quantize.shutil.which", lambda name: None)
    kw = _orchestra(tmp_path, monkeypatch, quantizer_file=False)
    with pytest.raises(QuantizerNotFound, match="llama.cpp"):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)


def test_quantize_model_runner_failure_cleans_staging(tmp_path, monkeypatch):
    created = []
    kw = _orchestra(tmp_path, monkeypatch,
                    run=_FakeRun(1, write_dst=True, stderr="boom"),
                    create=lambda name, path: created.append(name))
    with pytest.raises(QuantizeRunFailed):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)
    assert created == []
    staging = tmp_path / "staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_quantize_model_import_failure_cleans_staging(tmp_path, monkeypatch):
    def boom(name, path):
        raise RuntimeError("ollama import failed")
    kw = _orchestra(tmp_path, monkeypatch, create=boom)
    with pytest.raises(RuntimeError, match="ollama import failed"):
        quantize_model("qwen3:14b", vram_override_gb=8.0, **kw)
    staging = tmp_path / "staging"
    assert not staging.exists() or not any(staging.iterdir())
