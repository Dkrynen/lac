from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent_launch.launcher import launch_agent
from backend.agent_launch.project_profile import (
    ProjectProfile,
    load_profile,
    save_profile,
)


def _rec(model_id="qwen3:8b", ctx=65536):
    return SimpleNamespace(
        model=SimpleNamespace(id=model_id, name=model_id, params_b=8.0),
        context_used=ctx,
        details={},
        speed_source="estimated",
        score=88,
    )


def _prov(installed=("qwen3:8b",)):
    return SimpleNamespace(
        list_models=lambda: [SimpleNamespace(name=n) for n in installed],
        create=lambda name, frm, params: None,
    )


def _kwargs(events, recs, *, installed=("qwen3:8b",), catalog=("qwen3:8b",)):
    def fake_write_config(pd, model, host, permission=None):
        events["config"] = (Path(pd), model, host, permission)
        return Path(pd) / ".opencode/opencode.json"

    return dict(
        detect_fn=lambda: SimpleNamespace(),
        recommend_fn=lambda info, use_case, top_k: recs,
        ensure_variant_fn=lambda base, num_ctx, *, list_names, create: (
            events.__setitem__("ensure", (base, num_ctx)) or f"{base}-agent"
        ),
        write_config_fn=fake_write_config,
        write_commands_fn=lambda pd, pro_available=False, **kw: [],
        resolve_bin_fn=lambda: Path("opencode"),
        provider_factory=lambda: _prov(installed),
        config_fn=lambda start=None: SimpleNamespace(ollama_host="http://localhost:11434"),
        launch_fn=lambda argv, cwd: (events.__setitem__("launch", (argv, cwd))
                                     or SimpleNamespace(returncode=0)),
        catalog_fn=lambda: [SimpleNamespace(id=m) for m in catalog],
        out=lambda *a, **k: events.setdefault("out", []).append(" ".join(str(x) for x in a)),
    )


def test_profile_honored_without_rescanning(tmp_path):
    save_profile(tmp_path, ProjectProfile(model="qwen3:8b", context=65536))
    events = {}
    recs_called = []

    def spy_recommend(info, use_case, top_k):
        recs_called.append(True)
        return []

    kwargs = _kwargs(events, [])
    kwargs["recommend_fn"] = spy_recommend
    kwargs["detect_fn"] = lambda: (_ for _ in ()).throw(AssertionError("profile path must not rescan"))
    rc = launch_agent(tmp_path, **kwargs)

    assert rc == 0
    assert recs_called == []
    assert events["ensure"] == ("qwen3:8b", 65536)
    assert "project profile" in "\n".join(events["out"]).lower()


def test_profile_model_not_installed_refuses_honestly(tmp_path):
    save_profile(tmp_path, ProjectProfile(model="qwen3:30b-a3b"))
    events = {}
    rc = launch_agent(tmp_path, **_kwargs(events, [_rec()]))

    assert rc == 1
    assert "ensure" not in events
    assert "launch" not in events
    text = "\n".join(events["out"])
    assert "lac pull qwen3:30b-a3b" in text
    assert "--reselect" in text


def test_profile_context_below_floor_is_clamped_with_note(tmp_path):
    from backend.cookbook.recommend import AGENT_MIN_CONTEXT
    save_profile(tmp_path, ProjectProfile(model="qwen3:8b", context=100))
    events = {}
    rc = launch_agent(tmp_path, **_kwargs(events, []))

    assert rc == 0
    assert events["ensure"][1] == AGENT_MIN_CONTEXT
    assert "floor" in "\n".join(events["out"])


def test_first_run_records_a_profile(tmp_path):
    events = {}
    rc = launch_agent(tmp_path, **_kwargs(events, [_rec("qwen3:8b", 65536)]))

    assert rc == 0
    profile = load_profile(tmp_path)
    assert profile is not None
    assert profile.model == "qwen3:8b"
    assert profile.context == 65536
    assert profile.preset == "strict"
    assert "recorded project profile" in "\n".join(events["out"]).lower()


def test_reselect_repicks_and_updates_the_profile(tmp_path):
    save_profile(tmp_path, ProjectProfile(model="qwen3:4b", preset="dev"))
    events = {}
    rc = launch_agent(tmp_path, reselect=True, **_kwargs(events, [_rec("qwen3:8b", 65536)]))

    assert rc == 0
    assert events["ensure"][0] == "qwen3:8b"
    profile = load_profile(tmp_path)
    assert profile.model == "qwen3:8b"
    assert profile.preset == "dev"


def test_model_pin_launches_and_records(tmp_path):
    events = {}
    kwargs = _kwargs(events, [], installed=("qwen3:14b",), catalog=("qwen3:14b",))
    rc = launch_agent(tmp_path, model_pin="qwen3:14b", **kwargs)

    assert rc == 0
    assert events["ensure"][0] == "qwen3:14b"
    assert load_profile(tmp_path).model == "qwen3:14b"


def test_model_pin_unknown_model_refuses(tmp_path):
    events = {}
    kwargs = _kwargs(events, [], installed=("qwen3:14b",), catalog=("qwen3:8b",))
    rc = launch_agent(tmp_path, model_pin="qwen3:14b", **kwargs)

    assert rc == 1
    assert "unknown model" in "\n".join(events["out"]).lower()
    assert "launch" not in events


def test_model_pin_not_installed_refuses_with_pull_hint(tmp_path):
    events = {}
    kwargs = _kwargs(events, [], installed=("qwen3:8b",), catalog=("qwen3:14b",))
    rc = launch_agent(tmp_path, model_pin="qwen3:14b", **kwargs)

    assert rc == 1
    text = "\n".join(events["out"])
    assert "lac pull qwen3:14b" in text
    assert "launch" not in events


def test_profile_preset_flows_into_the_written_config(tmp_path):
    save_profile(tmp_path, ProjectProfile(model="qwen3:8b", preset="dev"))
    events = {}
    rc = launch_agent(tmp_path, **_kwargs(events, []))

    assert rc == 0
    permission = events["config"][3]
    assert permission["edit"] == "allow"
    assert permission["bash"] == "allow"
    assert permission["read"]["*.env"] == "deny"
    assert permission["external_directory"] == "deny"


def test_auto_path_keeps_fail_closed_permissions(tmp_path):
    events = {}
    rc = launch_agent(tmp_path, **_kwargs(events, [_rec("qwen3:8b", 65536)]))

    assert rc == 0
    permission = events["config"][3]
    assert permission["edit"] == "ask"
    assert permission["bash"] == "ask"
