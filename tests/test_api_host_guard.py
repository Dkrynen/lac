from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:5050",
        "localhost.:5050",
        "127.0.0.1",
        "127.0.0.1:5050",
        "[::1]",
        "[::1]:5050",
        "192.168.1.50:5050",
    ],
)
def test_trusted_local_and_literal_ip_hosts_are_allowed(flask_app, host):
    response = flask_app.test_client().get(
        "/api/system/version",
        headers={"Host": host},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example:5050",
        "127.0.0.1.attacker.example:5050",
        "attacker.example@127.0.0.1:5050",
        "",
    ],
)
def test_untrusted_or_malformed_host_is_rejected_app_wide(
    flask_app, isolated_home, tmp_path, host
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "secret.txt").write_text("do not leak", encoding="utf-8")

    response = flask_app.test_client().get(
        "/api/agent/file",
        query_string={"cwd": str(project), "path": "secret.txt"},
        headers={"Host": host},
    )
    assert response.status_code == 403
    assert b"do not leak" not in response.data


def test_invalid_port_is_rejected_by_authority_parser():
    from backend.api import _trusted_authority

    assert _trusted_authority("localhost:99999") is None


def test_untrusted_origin_is_rejected_even_with_loopback_host(flask_app):
    response = flask_app.test_client().get(
        "/api/system/version",
        headers={
            "Host": "localhost:5050",
            "Origin": "https://attacker.example",
        },
    )
    assert response.status_code == 403


def test_cross_alias_origin_is_rejected(flask_app):
    response = flask_app.test_client().get(
        "/api/system/version",
        headers={
            "Host": "localhost:5050",
            "Origin": "http://127.0.0.1:5050",
        },
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("localhost:5050", "http://localhost:5050"),
        ("127.0.0.1:5050", "http://127.0.0.1:5050"),
        ("[::1]:5050", "http://[::1]:5050"),
        ("192.168.1.50:5050", "http://192.168.1.50:5050"),
    ],
)
def test_trusted_origin_is_allowed(flask_app, host, origin):
    response = flask_app.test_client().get(
        "/api/system/version",
        headers={"Host": host, "Origin": origin},
    )
    assert response.status_code == 200


def test_same_host_vite_development_origin_is_allowed(flask_app):
    response = flask_app.test_client().get(
        "/api/system/version",
        headers={
            "Host": "localhost:5174",
            "Origin": "http://localhost:5174",
        },
    )
    assert response.status_code == 200


def test_cross_port_origin_is_rejected(flask_app):
    response = flask_app.test_client().post(
        "/api/app/relaunch",
        headers={
            "Host": "localhost:5050",
            "Origin": "http://localhost:9999",
        },
    )
    assert response.status_code == 403


def test_null_origin_is_rejected(flask_app):
    response = flask_app.test_client().get(
        "/api/system/version",
        headers={"Host": "localhost:5050", "Origin": "null"},
    )
    assert response.status_code == 403
