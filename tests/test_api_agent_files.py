from __future__ import annotations

import hashlib


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
