from pathlib import Path
from types import SimpleNamespace
from backend.agent_launch.launcher import launch_agent


def _rec(model_id="qwen3:8b", ctx=32768, warning=None):
    return SimpleNamespace(
        model=SimpleNamespace(id=model_id, name=model_id, params_b=8.0),
        context_used=ctx,
        details=({"agent_warning": warning} if warning else {}),
        speed_source="estimated",
        score=88,
    )


def _prov():
    """Fake Ollama provider: list_models() returns ModelInfo-like objects with .name."""
    calls = {}
    return SimpleNamespace(
        list_models=lambda: [SimpleNamespace(name="qwen3:8b")],
        create=lambda name, frm, params: calls.setdefault("create", (name, frm, params)),
        _calls=calls,
    )


def _base_kwargs(events, tmp_path, recs, *, ensure=None):
    def default_ensure(base, num_ctx, *, list_names, create):
        events["ensure"] = (base, num_ctx)
        list(list_names())            # exercise the names callable
        return f"{base}-agent"

    def fake_write_config(pd, model, host):
        events["config"] = (Path(pd), model, host)
        return Path(pd) / ".opencode/opencode.json"

    def fake_write_commands(pd):
        events["commands"] = Path(pd)
        return []

    def fake_launch(argv, cwd):
        events["launch"] = (argv, cwd)
        return SimpleNamespace(returncode=0)

    return dict(
        detect_fn=lambda: SimpleNamespace(),
        recommend_fn=lambda info, use_case, top_k: recs,
        ensure_variant_fn=ensure or default_ensure,
        write_config_fn=fake_write_config,
        write_commands_fn=fake_write_commands,
        resolve_bin_fn=lambda: Path("opencode"),
        provider_factory=_prov,
        config_fn=lambda start=None: SimpleNamespace(ollama_host="http://localhost:11434"),
        launch_fn=fake_launch,
        out=lambda *a, **k: None,
    )


def test_launch_happy_path_wires_everything(tmp_path):
    events = {}
    rc = launch_agent(tmp_path, **_base_kwargs(events, tmp_path, [_rec("qwen3:8b", 32768)]))
    assert rc == 0
    assert events["ensure"] == ("qwen3:8b", 32768)
    assert events["config"][1] == "qwen3:8b-agent"          # variant, not base
    assert events["config"][2] == "http://localhost:11434"
    assert events["commands"] == tmp_path.resolve()
    assert events["launch"][0] == ["opencode"]
    assert Path(events["launch"][1]) == tmp_path.resolve()  # launched in the project dir


def test_launch_floors_num_ctx_at_32k(tmp_path):
    events = {}
    def ensure(base, num_ctx, *, list_names, create):
        events["ctx"] = num_ctx
        return f"{base}-agent"
    launch_agent(tmp_path, **_base_kwargs(events, tmp_path, [_rec("qwen3:8b", 8192)], ensure=ensure))
    assert events["ctx"] == 32768                            # floored up from 8192


def test_launch_returns_1_when_no_model_fits(tmp_path):
    events = {}
    rc = launch_agent(tmp_path, **_base_kwargs(events, tmp_path, []))
    assert rc == 1
    assert "launch" not in events, "must not launch OpenCode when no model fits"
