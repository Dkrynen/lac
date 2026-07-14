# tests/test_manual_session.py
from __future__ import annotations

import sqlite3

import pytest


def test_create_session_defaults_origin_chat(isolated_home):
    from backend.cookbook.persistence import create_session, get_session

    sid = create_session(name="t", model="m", workspace="default")
    assert get_session(sid)["origin"] == "chat"


def test_get_or_create_manual_session_creates_once(isolated_home, tmp_path):
    from backend.cookbook.persistence import (
        create_project,
        get_or_create_manual_session,
        get_session,
    )

    root = tmp_path / "proj"
    root.mkdir()
    project = create_project("default", "proj", str(root), "")
    sid1 = get_or_create_manual_session(project["id"])
    sid2 = get_or_create_manual_session(project["id"])
    assert sid1 == sid2
    session = get_session(sid1)
    assert session["origin"] == "editor"
    assert session["project_id"] == project["id"]
    assert session["name"] == "Manual edits"


def test_get_or_create_manual_session_unknown_project(isolated_home):
    from backend.cookbook.persistence import get_or_create_manual_session

    with pytest.raises(ValueError):
        get_or_create_manual_session("0" * 14)


def test_manual_session_unique_per_project(isolated_home, tmp_path):
    from backend.cookbook import persistence

    root = tmp_path / "proj"
    root.mkdir()
    project = persistence.create_project("default", "proj", str(root), "")
    persistence.get_or_create_manual_session(project["id"])
    with pytest.raises(sqlite3.IntegrityError):
        persistence.create_session(
            name="dup", workspace="default",
            project_id=project["id"], origin="editor",
        )


def test_list_sessions_excludes_editor_origin(isolated_home, tmp_path):
    from backend.cookbook import persistence

    root = tmp_path / "proj"
    root.mkdir()
    project = persistence.create_project("default", "proj", str(root), "")
    chat_sid = persistence.create_session(
        name="chat", workspace="default", project_id=project["id"]
    )
    persistence.get_or_create_manual_session(project["id"])
    rows = persistence.list_sessions(workspace="default", project_id=project["id"])
    assert [r["id"] for r in rows] == [chat_sid]
    rows_all = persistence.list_sessions(workspace="default")
    assert all(r["id"] != "" and r.get("origin", "chat") != "editor" for r in rows_all)
