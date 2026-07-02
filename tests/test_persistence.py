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
