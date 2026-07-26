"""Emit the on-disk OpenCode configuration LAC drives it with: an Ollama provider
pointed at the LAC-chosen model, plus LAC hardware slash-commands. Written into the
project's `.opencode/` dir. We never edit OpenCode itself -- only its config."""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit


def _evaluation_loopback_host(host: str) -> bool:
    parsed = urlsplit(host)
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"} and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment

_FAIL_CLOSED_PERMISSIONS = {
    "*": "ask",
    "read": {
        "*": "allow",
        "*.env": "deny",
        "*.env.*": "deny",
        "*credentials.json": "deny",
        "*token.json": "deny",
        "*.pem": "deny",
        "*.key": "deny",
    },
    "glob": "allow",
    "grep": "ask",
    "list": "allow",
    "lsp": "allow",
    "edit": "ask",
    "bash": "ask",
    "webfetch": "ask",
    "websearch": "ask",
    "external_directory": "deny",
    "task": "deny",
}

_SCAN_MD = """\
---
description: Scan this machine's hardware (LAC)
---
Here is the current hardware scan:
!`lac scan`
"""

_RECOMMEND_MD = """\
---
description: Recommend the best agent-capable local model for this machine (LAC)
---
Here are LAC's agent-capable model recommendations for this machine:
!`lac recommend --use-case agent`
"""


def write_opencode_config(project_dir, model: str, ollama_host: str) -> Path:
    cfg = build_opencode_config(
        model, ollama_host, permission=_FAIL_CLOSED_PERMISSIONS
    )
    return _write_config(project_dir, cfg)


def write_stock_opencode_config(
    project_dir, model: str, ollama_host: str
) -> Path:
    """Write only the provider/model wiring used by the stock baseline arm.

    This deliberately excludes every LAC permission, command, and harness
    setting so the comparison does not quietly relabel LAC behavior as stock.
    """

    return _write_config(project_dir, build_opencode_config(model, ollama_host))


def build_opencode_config(
    model: str,
    ollama_host: str,
    *,
    permission: dict | None = None,
    tools: dict | None = None,
    evaluation: bool = False,
) -> dict:
    base_url = ollama_host.rstrip("/") + "/v1"
    if evaluation and not _evaluation_loopback_host(ollama_host):
        raise ValueError("evaluation config requires an unauthenticated loopback Ollama host")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (LAC)",
                "options": {"baseURL": base_url},
                "models": {model: {"name": model}},
            }
        },
        "model": f"ollama/{model}",
    }
    if permission is not None:
        config["permission"] = copy.deepcopy(permission)
    if tools is not None:
        config["tools"] = copy.deepcopy(tools)
    if evaluation:
        config.pop("$schema")
        config.update(
            {
                "autoupdate": False,
                "share": "disabled",
                "enabled_providers": ["ollama"],
                "plugin": [],
                "mcp": {},
                "instructions": [],
                "formatter": False,
                "snapshot": False,
            }
        )
    return config


def write_opencode_config_file(
    config_path,
    model: str,
    ollama_host: str,
    *,
    permission: dict | None = None,
    tools: dict | None = None,
) -> Path:
    """Write an explicit config path without creating a project `.opencode`.

    The evaluation runner uses this to keep runtime bootstrap files outside the
    immutable task workspace.
    """

    out = Path(config_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            build_opencode_config(
                model, ollama_host, permission=permission, tools=tools, evaluation=True
            ),
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def _write_config(project_dir, cfg: dict) -> Path:
    project_dir = Path(project_dir)
    oc_dir = project_dir / ".opencode"
    oc_dir.mkdir(parents=True, exist_ok=True)
    out = oc_dir / "opencode.json"
    out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return out


def write_agent_commands(project_dir) -> list[Path]:
    cmd_dir = Path(project_dir) / ".opencode" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in (("scan.md", _SCAN_MD), ("recommend.md", _RECOMMEND_MD)):
        p = cmd_dir / name
        p.write_text(body, encoding="utf-8")
        written.append(p)
    return written
