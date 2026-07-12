from __future__ import annotations

import hashlib


def _project(isolated_home, tmp_path):
    from backend.cookbook.persistence import create_project

    root = tmp_path / "browser-project"
    root.mkdir()
    project = create_project(workspace="default", name="Browser", root=str(root))
    return root, project


def _assert_no_store(response) -> None:
    assert response.headers["Cache-Control"] == "no-store"


def test_project_file_routes_expose_only_relative_read_only_contract(
    flask_app, isolated_home, tmp_path
):
    root, project = _project(isolated_home, tmp_path)
    (root / "src").mkdir()
    payload = b"export {}\n"
    (root / "src" / "index.ts").write_bytes(payload)
    client = flask_app.test_client()

    root_listing = client.get(f"/api/projects/{project['id']}/files")
    listing = client.get(
        f"/api/projects/{project['id']}/files", query_string={"path": "src"}
    )
    read = client.get(
        f"/api/projects/{project['id']}/file",
        query_string={"path": "src/index.ts"},
    )

    assert root_listing.status_code == 200
    assert root_listing.get_json() == {
        "path": "",
        "entries": [{"name": "src", "type": "dir", "size": 0}],
        "truncated": False,
    }
    assert listing.status_code == 200
    assert listing.get_json() == {
        "path": "src",
        "entries": [{"name": "index.ts", "type": "file", "size": len(payload)}],
        "truncated": False,
    }
    assert read.status_code == 200
    assert read.get_json() == {
        "path": "src/index.ts",
        "content": payload.decode("utf-8"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    for response in (root_listing, listing, read):
        _assert_no_store(response)
        assert str(root).encode("utf-8") not in response.data


def test_project_file_routes_reject_every_query_except_one_path(
    flask_app, isolated_home, tmp_path
):
    _root, project = _project(isolated_home, tmp_path)
    client = flask_app.test_client()
    list_url = f"/api/projects/{project['id']}/files"
    file_url = f"/api/projects/{project['id']}/file"

    responses = [
        client.get(list_url, query_string={"cwd": "C:/private"}),
        client.get(list_url, query_string={"root": "C:/private"}),
        client.get(list_url, query_string={"unexpected": "value"}),
        client.get(list_url, query_string={"project_id": project["id"]}),
        client.get(list_url, query_string=[("path", "src"), ("path", "other")]),
        client.get(file_url),
    ]

    assert [response.status_code for response in responses] == [400] * len(responses)
    for response in responses:
        _assert_no_store(response)
        assert response.get_json()["error"]

    method_not_allowed = client.post(list_url)
    assert method_not_allowed.status_code == 405
    _assert_no_store(method_not_allowed)


def test_project_file_routes_apply_no_store_to_security_errors(
    flask_app, isolated_home, tmp_path
):
    root, project = _project(isolated_home, tmp_path)
    (root / ".env").write_text("SECRET=never", encoding="utf-8")
    (root / "large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    (root / "binary.dat").write_bytes(b"\xff\xfe")
    client = flask_app.test_client()
    file_url = f"/api/projects/{project['id']}/file"

    responses = [
        client.get(file_url, query_string={"path": "../escape"}),
        client.get(file_url, query_string={"path": ".env"}),
        client.get(file_url, query_string={"path": "missing.txt"}),
        client.get(file_url, query_string={"path": "large.txt"}),
        client.get(file_url, query_string={"path": "binary.dat"}),
        client.get(f"/api/projects/{'f' * 14}/files"),
    ]

    assert [response.status_code for response in responses] == [
        400,
        403,
        404,
        413,
        415,
        404,
    ]
    for response in responses:
        _assert_no_store(response)
        assert b"SECRET=never" not in response.data


def test_project_file_routes_reject_malformed_identity_generated_trees_and_control_text(
    flask_app, isolated_home, tmp_path
):
    root, project = _project(isolated_home, tmp_path)
    (root / "node_modules").mkdir()
    (root / "node_modules" / "package.js").write_text("export {};", encoding="utf-8")
    (root / "control.txt").write_bytes(b"ordinary\x00hidden")
    (root / "deceptive.txt").write_text("left\u202eright", encoding="utf-8")
    (root / "bom.txt").write_text("\ufeffordinary", encoding="utf-8")
    (root / "interior-bom.txt").write_text("ordinary\ufeffhidden", encoding="utf-8")
    deceptive_name = "safe\u202etxt.js"
    (root / deceptive_name).write_text("ordinary", encoding="utf-8")
    client = flask_app.test_client()
    project_url = f"/api/projects/{project['id']}"

    responses = [
        client.get("/api/projects/not-a-project/files"),
        client.get(f"{project_url}/files", query_string={"path": "node_modules"}),
        client.get(f"{project_url}/file", query_string={"path": "node_modules/package.js"}),
        client.get(f"{project_url}/file", query_string={"path": "control.txt"}),
        client.get(f"{project_url}/file", query_string={"path": "deceptive.txt"}),
        client.get(f"{project_url}/file", query_string={"path": "interior-bom.txt"}),
        client.get(f"{project_url}/file", query_string={"path": deceptive_name}),
    ]

    assert [response.status_code for response in responses] == [
        400,
        403,
        403,
        415,
        415,
        415,
        400,
    ]
    for response in responses:
        _assert_no_store(response)
        assert b"ordinary" not in response.data

    listing = client.get(f"{project_url}/files")
    assert listing.status_code == 200
    _assert_no_store(listing)
    assert deceptive_name not in [entry["name"] for entry in listing.get_json()["entries"]]

    bom = client.get(f"{project_url}/file", query_string={"path": "bom.txt"})
    assert bom.status_code == 200
    _assert_no_store(bom)
    assert bom.get_json()["content"] == "\ufeffordinary"


def test_project_file_routes_apply_no_store_to_locality_rejection(
    flask_app, isolated_home, tmp_path
):
    _root, project = _project(isolated_home, tmp_path)
    url = f"/api/projects/{project['id']}/files"
    client = flask_app.test_client()
    responses = [
        client.get(
            url,
            headers={"Host": "192.168.1.20:5050"},
            environ_base={"REMOTE_ADDR": "192.168.1.21"},
        ),
        client.get(
            url,
            headers={
                "Host": "localhost:5050",
                "Origin": "https://attacker.example",
            },
        ),
        client.get(url, headers={"Host": "attacker.example:5050"}),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    for response in responses:
        _assert_no_store(response)


def test_project_listing_discards_results_when_root_drifts_after_access(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.project_security as project_security

    root, project = _project(isolated_home, tmp_path)
    (root / "visible.txt").write_text("visible", encoding="utf-8")
    original = project_security.list_project_directory

    def drift_after_list(*args, **kwargs):
        result = original(*args, **kwargs)
        root.rename(tmp_path / "listing-old-root")
        root.mkdir()
        return result

    monkeypatch.setattr(project_security, "list_project_directory", drift_after_list)
    response = flask_app.test_client().get(
        f"/api/projects/{project['id']}/files"
    )

    assert response.status_code == 409
    _assert_no_store(response)
    assert b"visible.txt" not in response.data


def test_project_read_discards_content_when_root_drifts_after_access(
    flask_app, isolated_home, monkeypatch, tmp_path
):
    import backend.project_security as project_security

    root, project = _project(isolated_home, tmp_path)
    (root / "visible.txt").write_text("never disclose", encoding="utf-8")
    original = project_security.read_project_text

    def drift_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        root.rename(tmp_path / "read-old-root")
        root.mkdir()
        return result

    monkeypatch.setattr(project_security, "read_project_text", drift_after_read)
    response = flask_app.test_client().get(
        f"/api/projects/{project['id']}/file",
        query_string={"path": "visible.txt"},
    )

    assert response.status_code == 409
    _assert_no_store(response)
    assert b"never disclose" not in response.data
