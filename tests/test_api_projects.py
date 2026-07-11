from __future__ import annotations


def _workspace(name: str = "Client Alpha"):
    from backend.cookbook.config import create_workspace

    return create_workspace(name)


def test_project_registration_lists_and_reads_canonical_record(
    flask_app, isolated_home, tmp_path
):
    workspace = _workspace()
    root = tmp_path / "client-project"
    root.mkdir()
    client = flask_app.test_client()

    created = client.post(
        f"/api/workspaces/{workspace.id}/projects",
        json={"name": "Website", "description": "Main site", "root": str(root)},
    )

    assert created.status_code == 201
    project = created.get_json()
    assert project["id"]
    assert project["workspace"] == workspace.id
    assert project["name"] == "Website"
    assert project["description"] == "Main site"
    assert project["root"] == str(root.resolve())
    assert project["status"] == "active"
    assert "root_key" not in project
    assert "root_device" not in project
    assert "root_inode" not in project

    listed = client.get(f"/api/workspaces/{workspace.id}/projects")
    assert listed.status_code == 200
    assert listed.get_json() == [project]

    fetched = client.get(f"/api/projects/{project['id']}")
    assert fetched.status_code == 200
    assert fetched.get_json() == project


def test_project_registration_is_loopback_only_and_does_not_mutate_on_reject(
    flask_app, isolated_home, tmp_path
):
    workspace = _workspace()
    root = tmp_path / "project"
    root.mkdir()
    client = flask_app.test_client()

    response = client.post(
        f"/api/workspaces/{workspace.id}/projects",
        json={"name": "Blocked", "root": str(root)},
        headers={"Host": "192.168.1.20:5050"},
        environ_base={"REMOTE_ADDR": "192.168.1.21"},
    )

    assert response.status_code == 403
    from backend.cookbook.persistence import list_projects

    assert list_projects(workspace.id) == []
    assert root.is_dir()


def test_project_registration_rejects_missing_workspace_and_duplicate_root(
    flask_app, isolated_home, tmp_path
):
    workspace = _workspace()
    root = tmp_path / "project"
    root.mkdir()
    client = flask_app.test_client()

    missing = client.post(
        "/api/workspaces/does-not-exist/projects",
        json={"name": "Nope", "root": str(root)},
    )
    assert missing.status_code == 404

    first = client.post(
        f"/api/workspaces/{workspace.id}/projects",
        json={"name": "First", "root": str(root)},
    )
    assert first.status_code == 201
    duplicate = client.post(
        f"/api/workspaces/{workspace.id}/projects",
        json={"name": "Second", "root": str(root)},
    )
    assert duplicate.status_code == 409
    assert "already registered" in duplicate.get_json()["error"].lower()


def test_workspace_delete_is_blocked_while_project_is_registered(
    flask_app, isolated_home, tmp_path
):
    from backend.cookbook.config import get_workspace

    workspace = _workspace()
    root = tmp_path / "external-project"
    root.mkdir()
    client = flask_app.test_client()
    created = client.post(
        f"/api/workspaces/{workspace.id}/projects",
        json={"name": "External", "root": str(root)},
    )
    assert created.status_code == 201

    response = client.delete(f"/api/workspaces/{workspace.id}")

    assert response.status_code == 409
    assert "registered projects" in response.get_json()["error"].lower()
    assert get_workspace(workspace.id) is not None
    assert root.is_dir()


def test_session_api_derives_project_workspace_and_filters_threads(
    flask_app, isolated_home, tmp_path
):
    from backend.cookbook import persistence

    workspace = _workspace()
    root = tmp_path / "project"
    root.mkdir()
    client = flask_app.test_client()
    project = client.post(
        f"/api/workspaces/{workspace.id}/projects",
        json={"name": "Project", "root": str(root)},
    ).get_json()

    created = client.post(
        "/api/sessions",
        json={"name": "Bound", "model": "mock:1b", "project_id": project["id"]},
    )
    assert created.status_code == 201
    bound_id = created.get_json()["id"]
    bound = persistence.get_session(bound_id)
    assert bound["project_id"] == project["id"]
    assert bound["workspace"] == workspace.id

    mismatch = client.post(
        "/api/sessions",
        json={
            "name": "Wrong",
            "model": "mock:1b",
            "workspace": "default",
            "project_id": project["id"],
        },
    )
    assert mismatch.status_code == 409

    legacy_id = persistence.create_session(
        name="Legacy", model="mock:1b", workspace=workspace.id
    )
    filtered = client.get(
        "/api/sessions",
        query_string={"workspace": workspace.id, "project_id": project["id"]},
    )
    assert [row["id"] for row in filtered.get_json()] == [bound_id]
    unassigned = client.get(
        "/api/sessions",
        query_string={"workspace": workspace.id, "project_id": "unassigned"},
    )
    assert [row["id"] for row in unassigned.get_json()] == [legacy_id]

    missing_filter = client.get(
        "/api/sessions",
        query_string={"workspace": workspace.id, "project_id": "missing-project"},
    )
    assert missing_filter.status_code == 404
    mismatched_filter = client.get(
        "/api/sessions",
        query_string={"workspace": "default", "project_id": project["id"]},
    )
    assert mismatched_filter.status_code == 409

    move_workspace = client.put(
        f"/api/sessions/{bound_id}",
        json={
            "name": "Moved",
            "model": "mock:1b",
            "messages": [],
            "workspace": "default",
        },
    )
    assert move_workspace.status_code == 409
    move_project = client.put(
        f"/api/sessions/{bound_id}",
        json={
            "name": "Moved",
            "model": "mock:1b",
            "messages": [],
            "project_id": "another-project",
        },
    )
    assert move_project.status_code == 409
    unchanged = persistence.get_session(bound_id)
    assert unchanged["workspace"] == workspace.id
    assert unchanged["project_id"] == project["id"]


def test_put_new_session_id_preserves_validated_project_binding(
    flask_app, isolated_home, tmp_path
):
    from backend.cookbook import persistence

    workspace = _workspace("Put Client")
    root = tmp_path / "put-project"
    root.mkdir()
    project = persistence.create_project(
        workspace.id, "PUT Project", str(root)
    )
    client = flask_app.test_client()

    created = client.put(
        "/api/sessions/caller-owned-id",
        json={
            "name": "Bound via PUT",
            "model": "mock:1b",
            "messages": [{"role": "user", "content": "hello"}],
            "project_id": project["id"],
        },
    )

    assert created.status_code == 200
    saved = persistence.get_session("caller-owned-id")
    assert saved["workspace"] == workspace.id
    assert saved["project_id"] == project["id"]

    missing = client.put(
        "/api/sessions/missing-project-id",
        json={
            "name": "Invalid",
            "model": "mock:1b",
            "messages": [],
            "project_id": "does-not-exist",
        },
    )
    assert missing.status_code == 404
    assert persistence.get_session("missing-project-id") is None


def test_remote_session_api_hides_project_threads_and_persisted_root_targets(
    flask_app, isolated_home, tmp_path
):
    from backend.cookbook import persistence

    workspace = _workspace("Remote Boundary")
    root = tmp_path / "private-project"
    root.mkdir()
    project = persistence.create_project(
        workspace.id, "Private Project", str(root)
    )
    bound_id = persistence.create_session(
        name="Bound", model="mock:1b", project_id=project["id"]
    )
    legacy_id = persistence.create_session(
        name="Legacy", model="mock:1b", workspace=workspace.id
    )
    persistence.add_session_event(
        bound_id,
        "ask",
        {
            "tool": "run_task",
            "target": {
                "kind": "sandbox_task",
                "name": "test",
                "root": str(root.resolve()),
            },
        },
    )
    remote = {
        "headers": {"Host": "192.168.1.20:5050"},
        "environ_base": {"REMOTE_ADDR": "192.168.1.21"},
    }
    client = flask_app.test_client()

    listed = client.get(
        "/api/sessions",
        query_string={"workspace": workspace.id},
        **remote,
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.get_json()] == [legacy_id]
    filtered = client.get(
        "/api/sessions",
        query_string={"workspace": workspace.id, "project_id": project["id"]},
        **remote,
    )
    assert filtered.status_code == 403

    detail = client.get(f"/api/sessions/{bound_id}", **remote)
    assert detail.status_code == 403
    assert str(root.resolve()) not in detail.get_data(as_text=True)
    assert client.get(f"/api/sessions/{legacy_id}", **remote).status_code == 200
    assert client.put(
        f"/api/sessions/{bound_id}",
        json={"name": "Changed", "model": "mock:1b", "messages": []},
        **remote,
    ).status_code == 403
    assert client.delete(f"/api/sessions/{bound_id}", **remote).status_code == 403
    assert client.post(
        "/api/sessions",
        json={"name": "Remote", "model": "mock:1b", "project_id": project["id"]},
        **remote,
    ).status_code == 403
    assert persistence.get_session(bound_id) is not None
