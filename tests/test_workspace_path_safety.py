from __future__ import annotations

import pytest

from backend.cookbook.config import (
    create_workspace,
    delete_workspace,
    _workspaces_dir,
)


def test_create_workspace_rejects_path_traversal(isolated_home):
    with pytest.raises(ValueError):
        create_workspace("../../../../Temp/x")


def test_create_workspace_sane_name_still_works(isolated_home):
    ws = create_workspace("My Project")
    assert ws.id == "my-project"
    assert (_workspaces_dir() / "my-project").is_dir()


def test_delete_workspace_rejects_path_traversal(isolated_home, tmp_path):
    _workspaces_dir()  # ensure the sandbox dir exists
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (outside / "keepme.txt").write_text("still here")

    # _workspaces_dir() is tmp_path/home/.model-hub/workspaces (3 levels
    # under tmp_path) -- this purely-relative id climbs out to tmp_path
    # then back down into the planted sibling, exactly like the proven
    # exploit's shape ("../../../../Temp/x").
    result = delete_workspace("../../../outside-target")

    assert result is False
    assert outside.exists()
    assert (outside / "keepme.txt").exists()


def test_delete_workspace_still_works_for_real_workspace(isolated_home):
    ws = create_workspace("Scratch")
    assert delete_workspace(ws.id) is True
    assert not (_workspaces_dir() / ws.id).exists()


def test_api_create_workspace_traversal_returns_400(flask_app, isolated_home):
    client = flask_app.test_client()
    r = client.post("/api/workspaces", json={"name": "../../../../Temp/evil"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_cli_workspace_create_traversal_exits_clean(isolated_home):
    import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["workspace", "create", "../../../../Temp/evil"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_workspace(args)
    assert e.value.code == 1
