from __future__ import annotations

import time

from backend.cookbook import persistence


def test_create_and_get_session(isolated_home):
    sid = persistence.create_session(name="t", model="llama3.2:3b")
    assert sid and len(sid) == 14
    data = persistence.get_session(sid)
    assert data is not None
    assert data["model"] == "llama3.2:3b"
    assert data["messages"] == []


def test_save_and_list_messages(isolated_home):
    sid = persistence.create_session(model="m")
    msgs = [
        {"role": "user", "content": "hi", "timestamp": time.time()},
        {"role": "assistant", "content": "hello", "timestamp": time.time()},
    ]
    persistence.save_session(sid, model="m", messages=msgs)
    data = persistence.get_session(sid)
    assert len(data["messages"]) == 2
    assert data["messages"][0]["content"] == "hi"
    assert data["messages"][1]["role"] == "assistant"


def test_list_sessions(isolated_home):
    a = persistence.create_session(name="a", model="m1")
    b = persistence.create_session(name="b", model="m2")
    sessions = persistence.list_sessions()
    ids = [s["id"] for s in sessions]
    assert a in ids and b in ids
    assert sessions[0]["updated_at"] >= sessions[-1]["updated_at"]


def test_list_sessions_limit_returns_recent_rows(isolated_home):
    a = persistence.create_session(name="a", model="m1")
    time.sleep(0.001)
    b = persistence.create_session(name="b", model="m2")
    time.sleep(0.001)
    c = persistence.create_session(name="c", model="m3")

    sessions = persistence.list_sessions(limit=2)

    assert [s["id"] for s in sessions] == [c, b]
    assert a not in [s["id"] for s in sessions]


def test_delete_session(isolated_home):
    sid = persistence.create_session(model="m")
    persistence.save_session(sid, model="m", messages=[{"role": "user", "content": "x"}])
    persistence.delete_session(sid)
    assert persistence.get_session(sid) is None


def test_save_upsert_new_session(isolated_home):
    sid = "fixed12345abc"
    persistence.save_session(sid, model="m", messages=[{"role": "user", "content": "y"}])
    data = persistence.get_session(sid)
    assert data is not None
    assert data["model"] == "m"


def test_get_missing_session_returns_none(isolated_home):
    assert persistence.get_session("doesnotexist") is None


def test_session_events_are_persisted_separately(isolated_home):
    sid = persistence.create_session(model="m")
    event_id = persistence.add_session_event(
        sid,
        "tool_result",
        {"name": "list_files", "ok": True, "result": "f api.py"},
    )

    events = persistence.list_session_events(sid)
    session = persistence.get_session(sid)

    assert event_id > 0
    assert events[0]["type"] == "tool_result"
    assert events[0]["payload"]["name"] == "list_files"
    assert session["events"][0]["payload"]["result"] == "f api.py"
