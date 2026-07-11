from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _events(response):
    return [
        json.loads(line[6:])
        for line in response.get_data(as_text=True).splitlines()
        if line.startswith("data: {")
    ]


def _registered_project(tmp_path):
    from backend.cookbook.config import create_workspace
    from backend.cookbook.persistence import create_project

    workspace = create_workspace("Bound Client")
    root = tmp_path / "bound-project"
    root.mkdir()
    project = create_project(
        workspace=workspace.id,
        name="Bound Project",
        root=str(root),
    )
    return workspace, root, project


class _FakeRunner:
    captured: list[dict] = []

    def __init__(self, provider, agent, handlers, schemas, **kwargs):
        self.captured.append(
            {"provider": provider, "handlers": handlers, "ctx": kwargs.get("ctx")}
        )

    async def run_stream(self, user_text, history=None):
        yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}


def test_project_chat_derives_root_workspace_and_provider_config_start(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    workspace, root, project = _registered_project(tmp_path)
    provider_starts = []
    _FakeRunner.captured = []
    monkeypatch.setattr(api_mod, "AgentRunner", _FakeRunner)
    monkeypatch.setattr(
        api_mod,
        "default_provider",
        lambda start=None: provider_starts.append(Path(start).resolve()) or object(),
    )

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Inspect this project",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 200
    events = _events(response)
    session = persistence.get_session(events[0]["session_id"])
    assert session["project_id"] == project["id"]
    assert session["workspace"] == workspace.id
    assert Path(_FakeRunner.captured[0]["ctx"]["cwd"]) == root.resolve()
    assert provider_starts == [root.resolve()]


def test_project_chat_uses_bound_project_default_model_when_omitted(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    _, root, project = _registered_project(tmp_path)
    (root / ".apt").mkdir()
    (root / ".apt" / "apt.jsonc").write_text(
        '{"default_model":"project-model:1b"}', encoding="utf-8"
    )
    _FakeRunner.captured = []
    captured_agents = []

    class CapturingRunner(_FakeRunner):
        def __init__(self, provider, agent, handlers, schemas, **kwargs):
            captured_agents.append(agent)
            super().__init__(provider, agent, handlers, schemas, **kwargs)

    monkeypatch.setattr(api_mod, "AgentRunner", CapturingRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda start=None: object())

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "message": "Inspect",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 200
    _events(response)
    assert captured_agents[0].model == "project-model:1b"


def test_project_chat_rejects_raw_cwd_and_workspace_mismatch_before_provider(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    workspace, root, project = _registered_project(tmp_path)
    provider_calls = []
    monkeypatch.setattr(
        api_mod, "default_provider", lambda start=None: provider_calls.append(start) or object()
    )

    for supplied_cwd in (str(root), None):
        with_cwd = flask_app.test_client().post(
            "/api/agent/chat",
            json={
                "agent": "plan",
                "model": "mock:1b",
                "message": "Inspect",
                "project_id": project["id"],
                "cwd": supplied_cwd,
            },
        )
        assert with_cwd.status_code == 400
        assert "cwd" in with_cwd.get_json()["error"]

    mismatch = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Inspect",
            "project_id": project["id"],
            "workspace": "default",
        },
    )
    assert mismatch.status_code == 409
    assert provider_calls == []


def test_bound_thread_can_resume_same_project_but_cannot_move_project_or_workspace(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    workspace, root, project = _registered_project(tmp_path)
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    other = persistence.create_project(
        workspace=workspace.id, name="Other", root=str(other_root)
    )
    session_id = persistence.create_session(
        name="Thread", model="mock:1b", project_id=project["id"]
    )
    provider_calls = []
    _FakeRunner.captured = []
    monkeypatch.setattr(api_mod, "AgentRunner", _FakeRunner)
    monkeypatch.setattr(
        api_mod, "default_provider", lambda start=None: provider_calls.append(start) or object()
    )

    resumed = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Continue",
            "session_id": session_id,
        },
    )
    assert resumed.status_code == 200
    _events(resumed)

    moved = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Move",
            "session_id": session_id,
            "project_id": other["id"],
        },
    )
    assert moved.status_code == 409
    wrong_workspace = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Move",
            "session_id": session_id,
            "workspace": "default",
        },
    )
    assert wrong_workspace.status_code == 409
    assert len(provider_calls) == 1
    assert Path(provider_calls[0]).resolve() == root.resolve()


def test_bound_project_identity_drift_fails_before_provider(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    _, root, project = _registered_project(tmp_path)
    moved = tmp_path / "moved"
    root.rename(moved)
    root.mkdir()
    provider_calls = []
    monkeypatch.setattr(
        api_mod, "default_provider", lambda start=None: provider_calls.append(start) or object()
    )

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Inspect",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 409
    assert "drift" in response.get_json()["error"].lower()
    assert provider_calls == []


def test_bound_project_identity_drift_during_provider_construction_fails_before_runner(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    _, root, project = _registered_project(tmp_path)
    runner_calls: list[object] = []

    def replacing_provider(start=None):
        root.rename(tmp_path / "moved-during-provider")
        root.mkdir()
        return object()

    class Runner:
        def __init__(self, *args, **kwargs):
            runner_calls.append(object())

    monkeypatch.setattr(api_mod, "default_provider", replacing_provider)
    monkeypatch.setattr(api_mod, "AgentRunner", Runner)

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Inspect",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 409
    assert "identity" in response.get_json()["error"].lower()
    assert runner_calls == []
    assert persistence.list_sessions(project_id=project["id"]) == []


def test_bound_project_identity_is_rechecked_before_background_provider_call(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    _, root, project = _registered_project(tmp_path)
    runner_calls: list[str] = []

    class Runner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            runner_calls.append(user_text)
            yield {"type": "done", "content": "should not run", "messages": []}

    monkeypatch.setattr(api_mod, "default_provider", lambda start=None: object())
    monkeypatch.setattr(api_mod, "AgentRunner", Runner)
    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Do not disclose",
            "project_id": project["id"],
        },
        buffered=False,
    )

    root.rename(tmp_path / "moved-before-stream")
    root.mkdir()
    events = _events(response)

    assert runner_calls == []
    errors = [event for event in events if event.get("type") == "error"]
    assert errors
    assert "identity" in errors[0]["message"].lower()


def test_project_bound_read_tools_revalidate_identity_at_each_tool_call(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    _, root, project = _registered_project(tmp_path)
    (root / "notes.txt").write_text("original", encoding="utf-8")
    _FakeRunner.captured = []
    monkeypatch.setattr(api_mod, "AgentRunner", _FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda start=None: object())

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Inspect later",
            "project_id": project["id"],
        },
    )
    assert response.status_code == 200
    _events(response)
    captured = _FakeRunner.captured[0]

    root.rename(tmp_path / "moved-project")
    root.mkdir()
    (root / "notes.txt").write_text("replacement private data", encoding="utf-8")
    result = captured["handlers"]["read_file"](
        {"path": "notes.txt"}, captured["ctx"]
    )

    assert result.startswith("error:")
    assert "replacement private data" not in result


def test_project_bound_run_task_revalidates_identity_before_prepare_and_execute(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.agent.sandbox import SandboxError

    _, root, project = _registered_project(tmp_path)
    (root / ".apt").mkdir()
    (root / ".apt" / "apt.jsonc").write_text("{}", encoding="utf-8")
    captured: dict = {}
    prepare_calls: list[str] = []
    execute_calls: list[str] = []

    class Capability:
        available = True
        tasks = ("test",)
        image = "example/lac@sha256:" + ("a" * 64)

    class Frozen:
        permission_target = "test"
        approval_target = {"kind": "sandbox_task", "name": "test"}

        def execute_outcome(self):
            execute_calls.append("test")
            return True, "[exit 0]\npassed"

    class Broker:
        def __init__(self, root, session_id, run_id, cancel_event, *, capability):
            self.root = Path(root)

        def prepare_task(self, name):
            prepare_calls.append(name)
            return Frozen()

    class CapturingRunner:
        def __init__(self, provider, agent, handlers, schemas, **kwargs):
            captured.update(kwargs)

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "probe_project_sandbox", lambda root: Capability())
    monkeypatch.setattr(api_mod, "DockerTaskBroker", Broker)
    monkeypatch.setattr(api_mod, "AgentRunner", CapturingRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda start=None: object())

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "build",
            "model": "mock:1b",
            "message": "Run tests",
            "project_id": project["id"],
        },
    )
    assert response.status_code == 200
    _events(response)
    preparer = captured["tool_preparers"]["run_task"]
    prepared = preparer({"name": "test"}, captured["ctx"])
    assert prepare_calls == ["test"]

    root.rename(tmp_path / "moved-project")
    root.mkdir()
    (root / ".apt").mkdir()
    (root / ".apt" / "apt.jsonc").write_text("{}", encoding="utf-8")

    ok, result = prepared.execute()
    assert ok is False
    assert "project_identity_drift" in result
    assert execute_calls == []
    with pytest.raises(SandboxError, match="identity"):
        preparer({"name": "test"}, captured["ctx"])
    assert prepare_calls == ["test"]


def test_project_bound_agent_rejects_hardlinked_config_before_provider(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    _, root, project = _registered_project(tmp_path)
    (root / ".apt").mkdir()
    outside_config = tmp_path / "outside-apt.jsonc"
    outside_config.write_text(
        '{"default_model":"outside-provider:1b"}', encoding="utf-8"
    )
    try:
        os.link(outside_config, root / ".apt" / "apt.jsonc")
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")
    provider_calls: list[Path | None] = []
    monkeypatch.setattr(
        api_mod,
        "default_provider",
        lambda start=None: provider_calls.append(start) or object(),
    )

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "message": "Inspect",
            "project_id": project["id"],
        },
    )

    assert response.status_code == 409
    assert "config" in response.get_json()["error"].lower()
    assert provider_calls == []


def test_project_sandbox_uses_registered_root_and_rejects_cwd_mix(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    _, root, project = _registered_project(tmp_path)
    seen = []

    class Capability:
        def to_dict(self):
            return {"available": False, "code": "test", "message": "test", "tasks": []}

    monkeypatch.setattr(
        api_mod, "probe_project_sandbox", lambda candidate: seen.append(candidate) or Capability()
    )
    client = flask_app.test_client()
    response = client.get(
        "/api/agent/sandbox", query_string={"project_id": project["id"]}
    )
    assert response.status_code == 200
    assert seen == [root.resolve()]

    mixed = client.get(
        "/api/agent/sandbox",
        query_string={"project_id": project["id"], "cwd": str(root)},
    )
    assert mixed.status_code == 400


def test_project_bound_agent_runs_are_loopback_only_before_provider(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod
    from backend.cookbook import persistence

    _, _, project = _registered_project(tmp_path)
    session_id = persistence.create_session(
        name="Thread", model="mock:1b", project_id=project["id"]
    )
    provider_calls = []
    monkeypatch.setattr(
        api_mod, "default_provider", lambda start=None: provider_calls.append(start) or object()
    )
    remote = {
        "headers": {"Host": "192.168.1.20:5050"},
        "environ_base": {"REMOTE_ADDR": "192.168.1.21"},
    }
    explicit = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Inspect",
            "project_id": project["id"],
        },
        **remote,
    )
    resumed = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Continue",
            "session_id": session_id,
        },
        **remote,
    )

    assert explicit.status_code == 403
    assert resumed.status_code == 403
    assert provider_calls == []


def test_legacy_raw_cwd_agent_runs_are_loopback_only_before_provider(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.api as api_mod

    root = tmp_path / "private-project"
    root.mkdir()
    provider_calls = []
    monkeypatch.setattr(
        api_mod, "default_provider", lambda start=None: provider_calls.append(start) or object()
    )

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "Read local files",
            "cwd": str(root),
        },
        headers={"Host": "192.168.1.20:5050"},
        environ_base={"REMOTE_ADDR": "192.168.1.21"},
    )

    assert response.status_code == 403
    assert provider_calls == []
