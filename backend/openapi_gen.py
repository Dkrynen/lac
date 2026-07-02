from __future__ import annotations

import re
from typing import Any

try:
    from .version import __version__ as APP_VERSION
    from .version import __description__ as APP_DESCRIPTION
except Exception:
    APP_VERSION = "0.0.0"
    APP_DESCRIPTION = "Apt — local LLM management toolkit"

_PARAM_RE = re.compile(r"<(?:(?:string|int|float|path|uuid):)?([a-zA-Z_][a-zA-Z0-9_]*)>")

_OPERATION_TAGS = {
    "api_ollama": "ollama",
    "api_sessions": "sessions",
    "api_workspaces": "workspaces",
    "api_system": "system",
    "api_library": "library",
    "api_config": "config",
    "api_scan": "hardware",
    "api_models": "models",
    "api_recommend": "hardware",
}


def _rule_to_path(rule: str) -> tuple[str, list[dict]]:
    params = []

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        params.append({"name": name, "in": "path", "required": True, "schema": {"type": "string"}})
        return "{" + name + "}"

    path = _PARAM_RE.sub(_sub, rule)
    return path, params


def _summary_for(endpoint: str, view_func: Any) -> str:
    doc = (view_func.__doc__ or "").strip().splitlines()
    if doc:
        return doc[0][:120]
    name = endpoint.replace("_", " ").strip()
    return name[:80].capitalize() if name else endpoint


def _tag_for(endpoint: str) -> str:
    for prefix, tag in _OPERATION_TAGS.items():
        if endpoint.startswith(prefix):
            return tag
    return "apt"


def _slug(path: str) -> str:
    s = re.sub(r"[{}]", "", path)
    s = re.sub(r"[^a-zA-Z0-9_/]", "_", s)
    s = s.replace("/", "_").strip("_")
    return s or "root"


def generate_openapi(app, server_url: str = "http://127.0.0.1:5050") -> dict:
    paths: dict[str, dict] = {}
    seen_ids: set[str] = set()
    for rule in app.url_map.iter_rules():
        endpoint = rule.endpoint or ""
        if endpoint == "static" or endpoint == "openapi_spec":
            continue
        path, params = _rule_to_path(rule.rule)
        item = paths.setdefault(path, {})
        view_func = app.view_functions.get(endpoint)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            method_lower = method.lower()
            base_id = f"{method_lower}_{_slug(path)}"
            op_id = base_id
            n = 2
            while op_id in seen_ids:
                op_id = f"{base_id}_{n}"
                n += 1
            seen_ids.add(op_id)
            operation = {
                "summary": _summary_for(endpoint, view_func),
                "operationId": op_id,
                "tags": [_tag_for(endpoint)],
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
            if params:
                operation["parameters"] = params
            if method in ("POST", "PUT"):
                operation["requestBody"] = {
                    "required": False,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            if path in ("", "/", "/docs", "/docs/api", "/docs/guide"):
                operation["responses"]["200"]["content"] = {"text/html": {"schema": {"type": "string"}}}
            item[method_lower] = operation

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Apt API",
            "version": APP_VERSION,
            "description": APP_DESCRIPTION,
        },
        "servers": [{"url": server_url}],
        "paths": {p: paths[p] for p in sorted(paths)},
    }


def write_openapi(app, path: str, server_url: str = "http://127.0.0.1:5050") -> str:
    import json

    spec = generate_openapi(app, server_url)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)
    return path
