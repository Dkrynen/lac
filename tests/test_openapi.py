from __future__ import annotations

from backend.api import app
from backend.openapi_gen import generate_openapi


def test_spec_has_basic_structure():
    spec = generate_openapi(app)
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["title"] == "LAC API"
    assert spec["info"]["version"] != "0.0.0"
    assert "paths" in spec and len(spec["paths"]) > 0


def test_spec_excludes_itself_and_static():
    spec = generate_openapi(app)
    assert "/api/openapi.json" not in spec["paths"]
    assert "static" not in {op for path in spec["paths"].values() for op in path}


def test_session_path_has_params_and_methods():
    spec = generate_openapi(app)
    path = spec["paths"].get("/api/sessions/{session_id}")
    assert path is not None
    assert set(path) >= {"get", "put", "delete"}
    params = path["get"]["parameters"]
    assert any(p["name"] == "session_id" for p in params)


def test_post_routes_have_request_body():
    spec = generate_openapi(app)
    create = spec["paths"].get("/api/sessions", {})
    assert "post" in create
    assert "requestBody" in create["post"]


def test_operation_ids_unique():
    spec = generate_openapi(app)
    ids = [
        op["operationId"]
        for path in spec["paths"].values()
        for op in path.values()
        if "operationId" in op
    ]
    assert len(ids) == len(set(ids))
