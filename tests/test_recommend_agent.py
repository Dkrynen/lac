from backend.cookbook.hardware import SystemInfo, GPUInfo
from backend.cookbook.recommend import (
    recommend, _is_agent_capable, _agent_warning, ModelEntry,
    AGENT_MIN_CONTEXT, AGENT_RELIABLE_PARAMS_B,
)


def _box16() -> SystemInfo:
    # dGPU box with enough VRAM to fit a mid model at Q4.
    return SystemInfo(
        os="Windows", cpu="AMD Ryzen 5 7600", cpu_cores=6, ram_gb=30.9,
        gpus=[GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=16.0, backend="rocm")],
        total_vram_gb=16.0,
    )


def _entry(**kw) -> ModelEntry:
    base = dict(id="x", name="X", provider="P", params_b=8.0, arch="qwen3",
                context=131072, use_cases=["coding"], is_moe=False)
    base.update(kw)
    return ModelEntry(**base)


def test_is_agent_capable_requires_capable_arch_and_min_params():
    assert _is_agent_capable(_entry(arch="qwen3", params_b=8.0)) is True
    assert _is_agent_capable(_entry(arch="qwen3", params_b=0.6)) is False   # too small
    assert _is_agent_capable(_entry(arch="totally-unknown-arch", params_b=8.0)) is False


def test_agent_warning_flags_small_but_usable_models():
    assert _agent_warning(_entry(params_b=4.0)) is not None                 # 3<=p<7 -> warn
    assert "small" in _agent_warning(_entry(params_b=4.0)).lower()
    assert _agent_warning(_entry(params_b=AGENT_RELIABLE_PARAMS_B)) is None  # >=7 -> no warn


def test_agent_recs_enforce_32k_context_floor():
    recs = recommend(_box16(), use_case="agent", top_k=91)
    assert recs, "agent path returned no recommendations on a 16GB box"
    assert all(r.context_used >= AGENT_MIN_CONTEXT for r in recs)


def test_agent_path_excludes_tiny_and_includes_capable_real_models():
    ids = {r.model.id for r in recommend(_box16(), use_case="agent", top_k=91)}
    assert "qwen3:0.6b" not in ids, "0.6B is below the agent params floor"
    assert "qwen3:30b-a3b" in ids, "a capable qwen3 model should be agent-eligible"
