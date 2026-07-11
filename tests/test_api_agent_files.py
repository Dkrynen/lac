from __future__ import annotations

import hashlib


def _project(isolated_home, tmp_path):
    from backend.cookbook.persistence import create_project

    root = tmp_path / "registered-project"
    root.mkdir()
    project = create_project(workspace="default", name="Registered", root=str(root))
    return root, project


def test_registered_project_file_routes_derive_root_and_are_loopback_only(
    flask_app, isolated_home, tmp_path
):
    root, project = _project(isolated_home, tmp_path)
    (root / "hello.txt").write_text("hello", encoding="utf-8")
    client = flask_app.test_client()

    listed = client.get(
        "/api/agent/files", query_string={"project_id": project["id"]}
    )
    assert listed.status_code == 200
    assert [row["name"] for row in listed.get_json()["entries"]] == ["hello.txt"]
    read = client.get(
        "/api/agent/file",
        query_string={"project_id": project["id"], "path": "hello.txt"},
    )
    assert read.status_code == 200
    assert read.get_json()["content"] == "hello"

    mixed = client.get(
        "/api/agent/file",
        query_string={
            "project_id": project["id"],
            "cwd": str(root),
            "path": "hello.txt",
        },
    )
    assert mixed.status_code == 400
    remote = client.get(
        "/api/agent/file",
        query_string={"project_id": project["id"], "path": "hello.txt"},
        headers={"Host": "192.168.1.20:5050"},
        environ_base={"REMOTE_ADDR": "192.168.1.21"},
    )
    assert remote.status_code == 403


def test_registered_project_file_routes_fail_closed_on_identity_drift(
    flask_app, isolated_home, tmp_path
):
    root, project = _project(isolated_home, tmp_path)
    root.rename(tmp_path / "old-project")
    root.mkdir()

    response = flask_app.test_client().get(
        "/api/agent/files", query_string={"project_id": project["id"]}
    )

    assert response.status_code == 409
    assert "drift" in response.get_json()["error"].lower()


def test_legacy_raw_cwd_file_routes_are_loopback_only(
    flask_app, isolated_home, tmp_path
):
    root = tmp_path / "private-files"
    root.mkdir()
    (root / "client-notes.txt").write_text(
        "private client material", encoding="utf-8"
    )
    remote = {
        "headers": {"Host": "192.168.1.20:5050"},
        "environ_base": {"REMOTE_ADDR": "192.168.1.21"},
    }
    client = flask_app.test_client()

    listed = client.get(
        "/api/agent/files", query_string={"cwd": str(root)}, **remote
    )
    read = client.get(
        "/api/agent/file",
        query_string={"cwd": str(root), "path": "client-notes.txt"},
        **remote,
    )

    assert [listed.status_code, read.status_code] == [403, 403]
    assert b"private client material" not in listed.data + read.data


def test_agent_files_lists_one_level(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "deep.py").write_text("x", encoding="utf-8")
    (project / "a.txt").write_text("hello", encoding="utf-8")
    (project / ".git").mkdir()
    (project / ".env").write_text("SECRET=1", encoding="utf-8")
    (project / "node_modules").mkdir()

    client = flask_app.test_client()
    resp = client.get("/api/agent/files", query_string={"cwd": str(project)})
    assert resp.status_code == 200
    body = resp.get_json()
    names = [e["name"] for e in body["entries"]]
    assert names == ["src", "a.txt"]  # dirs first; dotfiles + node_modules skipped
    assert body["entries"][0]["type"] == "dir"
    assert body["entries"][1] == {"name": "a.txt", "type": "file", "size": 5}

    sub = client.get("/api/agent/files", query_string={"cwd": str(project), "path": "src"})
    assert [e["name"] for e in sub.get_json()["entries"]] == ["deep.py"]


def test_agent_files_rejects_escape_and_missing(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    client = flask_app.test_client()
    assert client.get("/api/agent/files", query_string={"cwd": str(project), "path": ".."}).status_code == 400
    assert client.get("/api/agent/files", query_string={"cwd": str(project), "path": "nope"}).status_code == 404
    assert client.get("/api/agent/files", query_string={"cwd": str(project / "missing")}).status_code == 400


def test_agent_file_returns_content_and_hash(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_bytes(b"hello")
    client = flask_app.test_client()
    resp = client.get("/api/agent/file", query_string={"cwd": str(project), "path": "a.txt"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["content"] == "hello"
    assert body["size"] == 5
    assert body["sha256"] == hashlib.sha256(b"hello").hexdigest()


def test_agent_file_413_over_1mb_and_404_and_400(flask_app, isolated_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "big.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    client = flask_app.test_client()
    assert client.get("/api/agent/file", query_string={"cwd": str(project), "path": "big.bin"}).status_code == 413
    assert client.get("/api/agent/file", query_string={"cwd": str(project), "path": "nope.txt"}).status_code == 404
    assert client.get("/api/agent/file", query_string={"cwd": str(project), "path": "../etc"}).status_code == 400


def test_agent_file_hard_denies_secrets_and_non_utf8(
    flask_app, isolated_home, tmp_path
):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("SECRET=do-not-disclose", encoding="utf-8")
    (project / "credentials.json").write_text('{"token":"nope"}', encoding="utf-8")
    (project / "binary.dat").write_bytes(b"\xff\xfe\x00")
    client = flask_app.test_client()

    for path in (".env", "credentials.json"):
        response = client.get(
            "/api/agent/file", query_string={"cwd": str(project), "path": path}
        )
        assert response.status_code == 403
        assert "do-not-disclose" not in response.get_data(as_text=True)
        assert "token" not in response.get_data(as_text=True).lower()

    non_utf8 = client.get(
        "/api/agent/file",
        query_string={"cwd": str(project), "path": "binary.dat"},
    )
    assert non_utf8.status_code == 415
    assert "content" not in non_utf8.get_json()


def _stage(tmp_path, path="a.txt", content="staged", session_id=None):
    from backend.cookbook import persistence

    sid = session_id or persistence.create_session(name="t", model="m", workspace="default")
    row = persistence.stage_change(sid, "run1", str(tmp_path), path, content)
    return sid, row


def test_session_changes_listing_omits_bodies(flask_app, isolated_home, tmp_path):
    sid, row = _stage(tmp_path)
    client = flask_app.test_client()
    resp = client.get(f"/api/agent/sessions/{sid}/changes")
    assert resp.status_code == 200
    changes = resp.get_json()["changes"]
    assert len(changes) == 1
    assert changes[0]["id"] == row["id"]
    assert "new_content" not in changes[0] and "old_content" not in changes[0]
    assert changes[0]["new_size"] == len("staged")
    assert client.get("/api/agent/sessions/nosuch/changes").status_code == 404
    filtered = client.get(f"/api/agent/sessions/{sid}/changes", query_string={"status": "applied"})
    assert filtered.get_json()["changes"] == []


def test_change_detail_and_apply_reject_revert(flask_app, isolated_home, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"original")
    sid, row = _stage(tmp_path, content="changed")
    client = flask_app.test_client()

    detail = client.get(f"/api/agent/changes/{row['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["old_content"] == "original"
    assert detail.get_json()["new_content"] == "changed"
    assert client.get("/api/agent/changes/nosuch").status_code == 404

    applied = client.post(f"/api/agent/changes/{row['id']}/apply")
    assert applied.status_code == 200
    assert (tmp_path / "a.txt").read_bytes() == b"changed"
    # re-apply a non-pending row -> 409
    assert client.post(f"/api/agent/changes/{row['id']}/apply").status_code == 409

    reverted = client.post(f"/api/agent/changes/{row['id']}/revert")
    assert reverted.status_code == 200
    assert (tmp_path / "a.txt").read_bytes() == b"original"
    # revert again -> 409 (row now 'reverted')
    assert client.post(f"/api/agent/changes/{row['id']}/revert").status_code == 409

    sid2, row2 = _stage(tmp_path, path="b.txt", session_id=sid)
    rejected = client.post(f"/api/agent/changes/{row2['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.get_json()["status"] == "rejected"
    assert client.post(f"/api/agent/changes/{row2['id']}/reject").status_code == 409
    assert client.post("/api/agent/changes/nosuch/apply").status_code == 404
    assert client.post("/api/agent/changes/nosuch/reject").status_code == 404
    assert client.post("/api/agent/changes/nosuch/revert").status_code == 404


def test_apply_conflict_maps_to_409(flask_app, isolated_home, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"original")
    sid, row = _stage(tmp_path, content="changed")
    (tmp_path / "a.txt").write_bytes(b"hand-edited")
    client = flask_app.test_client()
    resp = client.post(f"/api/agent/changes/{row['id']}/apply")
    assert resp.status_code == 409
    assert resp.get_json()["status"] == "conflict"


def test_batch_apply_continues_past_conflicts(flask_app, isolated_home, tmp_path):
    from backend.cookbook import persistence

    sid, row1 = _stage(tmp_path, path="one.txt", content="1")
    (tmp_path / "two.txt").write_bytes(b"original")
    row2 = persistence.stage_change(sid, "run1", str(tmp_path), "two.txt", "2")
    row3 = persistence.stage_change(sid, "run1", str(tmp_path), "three.txt", "3")
    (tmp_path / "two.txt").write_bytes(b"conflicted meanwhile")

    client = flask_app.test_client()
    resp = client.post(f"/api/agent/sessions/{sid}/changes/apply", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["applied"] == [row1["id"], row3["id"]]  # created_at order, conflict skipped not aborted
    assert body["conflicts"] == [row2["id"]]
    assert body["errors"] == []
    assert (tmp_path / "three.txt").read_bytes() == b"3"

    # ids subset + unknown id lands in errors
    sid2, row4 = _stage(tmp_path, path="four.txt", content="4")
    resp2 = client.post(
        f"/api/agent/sessions/{sid2}/changes/apply", json={"ids": [row4["id"], "nosuch"]}
    )
    body2 = resp2.get_json()
    assert body2["applied"] == [row4["id"]]
    assert body2["errors"] == [{"id": "nosuch", "error": "not pending"}]
    assert client.post("/api/agent/sessions/nosuch/changes/apply", json={}).status_code == 404


def test_project_bound_staged_routes_reject_corrupted_cross_root_row(
    flask_app, isolated_home, tmp_path
):
    from backend.cookbook import persistence

    project_root = tmp_path / "project"
    other_root = tmp_path / "other"
    project_root.mkdir()
    other_root.mkdir()
    project = persistence.create_project(
        workspace="default", name="Bound", root=str(project_root)
    )
    session_id = persistence.create_session(
        name="Thread", model="m", project_id=project["id"]
    )
    row = persistence.stage_change(
        session_id, "run1", str(project_root), "safe.txt", "never applied"
    )
    conn = persistence._ensure_db()
    conn.execute(
        "UPDATE staged_changes SET root = ? WHERE id = ?",
        (str(other_root.resolve()), row["id"]),
    )
    conn.commit()
    conn.close()

    client = flask_app.test_client()
    responses = [
        client.get(f"/api/agent/sessions/{session_id}/changes"),
        client.get(f"/api/agent/changes/{row['id']}"),
        client.post(f"/api/agent/changes/{row['id']}/apply"),
        client.post(f"/api/agent/changes/{row['id']}/reject"),
        client.post(f"/api/agent/changes/{row['id']}/revert"),
        client.post(f"/api/agent/sessions/{session_id}/changes/apply", json={}),
    ]

    assert [response.status_code for response in responses] == [409] * len(responses)
    assert all(
        "project root" in response.get_json()["error"].lower()
        for response in responses
    )
    assert not (other_root / "safe.txt").exists()
    assert not (project_root / "safe.txt").exists()


def test_project_bound_staged_routes_are_loopback_only(
    flask_app, isolated_home, tmp_path
):
    from backend.cookbook import persistence

    project_root = tmp_path / "project"
    project_root.mkdir()
    project = persistence.create_project(
        workspace="default", name="Bound", root=str(project_root)
    )
    session_id = persistence.create_session(model="m", project_id=project["id"])
    row = persistence.stage_change(
        session_id, "run1", str(project_root), "safe.txt", "not remote"
    )
    remote = {
        "headers": {"Host": "192.168.1.20:5050"},
        "environ_base": {"REMOTE_ADDR": "192.168.1.21"},
    }
    client = flask_app.test_client()

    listed = client.get(f"/api/agent/sessions/{session_id}/changes", **remote)
    detailed = client.get(f"/api/agent/changes/{row['id']}", **remote)
    applied = client.post(f"/api/agent/changes/{row['id']}/apply", **remote)

    assert [listed.status_code, detailed.status_code, applied.status_code] == [403, 403, 403]
    assert not (project_root / "safe.txt").exists()


def test_legacy_staged_routes_are_loopback_only(
    flask_app, isolated_home, tmp_path
):
    sid, row = _stage(tmp_path, path="legacy.txt", content="must stay staged")
    remote = {
        "headers": {"Host": "192.168.1.20:5050"},
        "environ_base": {"REMOTE_ADDR": "192.168.1.21"},
    }
    client = flask_app.test_client()

    responses = [
        client.get(f"/api/agent/sessions/{sid}/changes", **remote),
        client.get(f"/api/agent/changes/{row['id']}", **remote),
        client.post(f"/api/agent/changes/{row['id']}/apply", **remote),
        client.post(f"/api/agent/changes/{row['id']}/reject", **remote),
        client.post(f"/api/agent/changes/{row['id']}/revert", **remote),
        client.post(f"/api/agent/sessions/{sid}/changes/apply", json={}, **remote),
    ]

    assert [response.status_code for response in responses] == [403] * len(responses)
    assert not (tmp_path / "legacy.txt").exists()
