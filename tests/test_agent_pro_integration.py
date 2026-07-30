from pathlib import Path
from types import SimpleNamespace

from backend.agent_launch.launcher import launch_agent
from backend.agent_launch.config_writer import write_agent_commands
from backend.plugins import LoadedPlugin, get_agent_pro_variant


def _rec(model_id="gpt-oss:20b", ctx=131072):
    return SimpleNamespace(
        model=SimpleNamespace(id=model_id, name=model_id, params_b=20.0),
        context_used=ctx,
        details={},
        speed_source="estimated",
        score=88,
    )


def _prov():
    return SimpleNamespace(
        list_models=lambda: [{"name": "gpt-oss:20b"}],
        create=lambda name, frm, params: None,
    )


def test_pro_variant_fn_used_when_it_returns_a_name(tmp_path):
    events = {}

    def fake_pro_variant(model, list_names):
        events["pro_called"] = model
        return "gpt-oss-20b-tuned"

    def fake_ensure(base, num_ctx, **kw):
        events["ensure_called"] = base
        return f"{base}-agent"

    rc = launch_agent(
        tmp_path,
        detect_fn=lambda: SimpleNamespace(),
        recommend_fn=lambda info, use_case, top_k: [_rec()],
        ensure_variant_fn=fake_ensure,
        write_config_fn=lambda pd, model, host: Path(pd),
        write_commands_fn=lambda pd, pro_available=False: [],
        resolve_bin_fn=lambda: Path("opencode"),
        provider_factory=_prov,
        config_fn=lambda start=None: SimpleNamespace(ollama_host="http://localhost:11434"),
        launch_fn=lambda cmd, **kw: SimpleNamespace(returncode=0),
        pro_variant_fn=fake_pro_variant,
        out=lambda *a, **k: None,
    )
    assert rc == 0
    assert events["pro_called"] == "gpt-oss:20b"
    assert "ensure_called" not in events


def test_pro_variant_fn_falls_back_to_agent_variant_when_none(tmp_path):
    events = {}

    def fake_ensure(base, num_ctx, **kw):
        events["ensure_called"] = base
        return f"{base}-agent"

    rc = launch_agent(
        tmp_path,
        detect_fn=lambda: SimpleNamespace(),
        recommend_fn=lambda info, use_case, top_k: [_rec()],
        ensure_variant_fn=fake_ensure,
        write_config_fn=lambda pd, model, host: Path(pd),
        write_commands_fn=lambda pd, pro_available=False: [],
        resolve_bin_fn=lambda: Path("opencode"),
        provider_factory=_prov,
        config_fn=lambda start=None: SimpleNamespace(ollama_host="http://localhost:11434"),
        launch_fn=lambda cmd, **kw: SimpleNamespace(returncode=0),
        pro_variant_fn=lambda model, list_names: None,
        out=lambda *a, **k: None,
    )
    assert rc == 0
    assert events["ensure_called"] == "gpt-oss:20b"


def test_write_agent_commands_includes_tune_when_pro_available(tmp_path):
    prefix = [r"C:\Tools\LAC\lac.exe"]
    paths = write_agent_commands(tmp_path, pro_available=True, cli_prefix=prefix)
    names = {p.name for p in paths}
    assert "tune.md" in names
    tune = (tmp_path / ".opencode" / "commands" / "tune.md").read_text(encoding="utf-8")
    assert '!`"C:\\Tools\\LAC\\lac.exe" pro tune --apply $ARGUMENTS`' in tune


def test_write_agent_commands_excludes_tune_when_pro_unavailable(tmp_path):
    paths = write_agent_commands(tmp_path, pro_available=False)
    names = {p.name for p in paths}
    assert "tune.md" not in names
    assert names == {"scan.md", "recommend.md"}


def test_get_agent_pro_variant_returns_tuned_name_from_plugin():
    class FakePro:
        def agent_pro_variant(self, model, list_names):
            return "gpt-oss-20b-tuned"

    plugins = [LoadedPlugin(name="pro", version="0.1.0", obj=FakePro())]
    result = get_agent_pro_variant(plugins, "gpt-oss:20b", lambda: ["gpt-oss:20b"])
    assert result == "gpt-oss-20b-tuned"


def test_get_agent_pro_variant_returns_none_when_no_plugin():
    plugins = [LoadedPlugin(name="other", version="1.0", obj=object())]
    result = get_agent_pro_variant(plugins, "gpt-oss:20b", lambda: ["gpt-oss:20b"])
    assert result is None


def test_get_agent_pro_variant_skips_broken_plugin():
    class BrokenPro:
        def agent_pro_variant(self, model, list_names):
            raise RuntimeError("license server down")

    plugins = [LoadedPlugin(name="pro", version="0.1.0", obj=BrokenPro())]
    result = get_agent_pro_variant(plugins, "gpt-oss:20b", lambda: ["gpt-oss:20b"])
    assert result is None


def test_get_agent_pro_variant_skips_errored_plugin():
    plugins = [LoadedPlugin(name="pro", version="?", obj=None, error="load failed")]
    result = get_agent_pro_variant(plugins, "gpt-oss:20b", lambda: ["gpt-oss:20b"])
    assert result is None
