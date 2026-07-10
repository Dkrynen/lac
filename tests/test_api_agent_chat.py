from __future__ import annotations

import json
from pathlib import Path


def _events(response) -> list[dict]:
    out: list[dict] = []
    for line in response.get_data(as_text=True).splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            continue
        out.append(json.loads(data))
    return out


def test_agent_chat_streams_and_persists_tool_events(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.cookbook import persistence

    captured: dict = {}

    class FakeProvider:
        name = "ollama"

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, ctx=None, max_iterations=None, **kwargs):
            captured["provider"] = provider
            captured["agent"] = agent
            captured["ctx"] = ctx
            captured["max_iterations"] = max_iterations
            captured["chat_options"] = kwargs.get("chat_options")

        async def run_stream(self, user_text, history=None):
            captured["user_text"] = user_text
            captured["history"] = history
            yield {"type": "delta", "content": "hello"}
            yield {"type": "tool_call", "name": "list_files", "args": {"path": "."}}
            yield {"type": "tool_result", "name": "list_files", "ok": True, "result": "f api.py"}
            yield {
                "type": "done",
                "content": "hello",
                "messages": [
                    {"role": "user", "content": user_text},
                    {"role": "tool", "name": "list_files", "content": "f api.py"},
                    {"role": "assistant", "content": "hello"},
                ],
                "iterations": 1,
            }

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda: FakeProvider())

    project = tmp_path / "project"
    project.mkdir()
    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "List this project",
            "cwd": str(project),
            "messages": [{"role": "system", "content": "stay brief"}],
        },
    )

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    events = _events(response)
    assert [e["type"] for e in events] == ["session", "status", "delta", "tool_call", "tool_result", "done"]
    assert captured["agent"].model == "mock:1b"
    assert captured["agent"].tools == ["read_file", "list_files"]
    assert not captured["agent"].permissions.can_write()
    assert not captured["agent"].permissions.can_run_bash()
    assert Path(captured["ctx"]["cwd"]) == project.resolve()
    assert captured["chat_options"]["keep_alive"] == "30m"
    assert captured["chat_options"]["options"]["num_ctx"] > 0
    assert captured["history"] == [{"role": "system", "content": "stay brief"}]

    session = persistence.get_session(events[0]["session_id"])
    assert session is not None
    assert [m["role"] for m in session["messages"]] == ["system", "user", "assistant"]
    assert session["messages"][-1]["content"] == "hello"
    assert [e["type"] for e in session["events"]] == ["tool_call", "tool_result"]


def test_agent_chat_ignores_project_agent_override(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod

    captured: dict = {}

    class FakeRunner:
        def __init__(self, provider, agent, handlers, schemas, ctx=None, max_iterations=None, **kwargs):
            captured["agent"] = agent
            captured["schemas"] = schemas

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "safe", "messages": [], "iterations": 1}

    project = tmp_path / "project"
    agent_dir = project / ".apt" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "plan.json").write_text(
        json.dumps(
            {
                "name": "plan",
                "permissions": {
                    "filesystem": {"read": True, "write": True, "delete": True},
                    "bash": {"run": True},
                    "network": {"fetch": True, "post": True},
                    "mcp": {"connect": True},
                },
                "tools": ["read_file", "list_files", "write_file", "run_bash"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "inspect", "cwd": str(project)},
    )

    assert response.status_code == 200
    _events(response)
    assert captured["agent"].tools == ["read_file", "list_files"]
    assert not captured["agent"].permissions.can_write()
    assert not captured["agent"].permissions.can_run_bash()


def test_agent_chat_preserves_saved_workspace_when_client_omits_it(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.cookbook import persistence

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    sid = persistence.create_session(name="t", model="mock:1b", workspace="saved-workspace")
    project = tmp_path / "project"
    project.mkdir()

    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={
            "agent": "plan",
            "model": "mock:1b",
            "message": "continue",
            "session_id": sid,
            "messages": [{"role": "user", "content": "earlier"}],
            "cwd": str(project),
        },
    )

    assert response.status_code == 200
    _events(response)
    assert persistence.get_session(sid)["workspace"] == "saved-workspace"


def test_agent_chat_rejects_build_until_approval_ui_exists(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    response = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "build", "model": "mock:1b", "message": "edit files", "cwd": str(project)},
    )

    assert response.status_code == 403
    assert "approval UI" in response.get_json()["error"]


def test_agent_chat_requires_message(flask_app, isolated_home):
    response = flask_app.test_client().post("/api/agent/chat", json={"model": "mock:1b"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Message required"
