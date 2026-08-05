"""Emit the on-disk OpenCode configuration LAC drives it with: an Ollama provider
pointed at the LAC-chosen model, plus LAC hardware slash-commands. Written into the
project's `.opencode/` dir. We never edit OpenCode itself -- only its config."""
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from backend import self_invoke
from backend.agent_eval.schedule import GenerationSettings


def _evaluation_loopback_host(host: str) -> bool:
    parsed = urlsplit(host)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and not parsed.username
        and not parsed.password
        and not parsed.path.rstrip("/")
        and not parsed.query
        and not parsed.fragment
    )

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
!`{lac} scan`
"""

_RECOMMEND_MD = """\
---
description: Recommend the best agent-capable local model for this machine (LAC)
---
Here are LAC's agent-capable model recommendations for this machine:
!`{lac} recommend --use-case agent`
"""

_TUNE_MD = """\
---
description: Tune a model for this machine (LAC Pro)
---
Tuning the model for this machine:
!`{lac} pro tune --apply $ARGUMENTS`
"""

_LAC_PLUGIN_TS = """\
import { type Plugin, tool } from "@opencode-ai/plugin"
import { execSync } from "child_process"

const LAC_CLI: string[] = {lac_cli_json}

function lacCommand(args: string): string {
  return LAC_CLI.map((part) => '"' + part + '"').join(" ") + " " + args
}

export const LacPlugin: Plugin = async (ctx) => {
  return {
    tool: {
      lac_rescan: tool({
        description: "Rescan this machine's hardware and return the LAC hardware report",
        args: {},
        async execute(args, context) {
          try {
            return execSync(lacCommand("scan"), { encoding: "utf-8", cwd: context.directory })
          } catch (e) {
            return `LAC scan failed: ${e}`
          }
        },
      }),
      lac_retune: tool({
        description: "Re-recommend the best agent-capable local model for this machine using LAC",
        args: {},
        async execute(args, context) {
          try {
            return execSync(lacCommand("recommend --use-case agent"), { encoding: "utf-8", cwd: context.directory })
          } catch (e) {
            return `LAC recommend failed: ${e}`
          }
        },
      }),
    },
  }
}
"""


def _resolve_cli_prefix(cli_prefix) -> list[str]:
    if cli_prefix is None:
        return list(self_invoke.cli_prefix())
    return list(cli_prefix)


def _quoted_cli(cli_prefix) -> str:
    return " ".join('"' + part + '"' for part in _resolve_cli_prefix(cli_prefix))


def write_opencode_config(project_dir, model: str, ollama_host: str, *,
                          permission: dict | None = None) -> Path:
    cfg = build_opencode_config(
        model, ollama_host,
        permission=_FAIL_CLOSED_PERMISSIONS if permission is None else permission,
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
    generation: GenerationSettings | None = None,
    seed: int | None = None,
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
    if (generation is None) != (seed is None):
        raise ValueError("generation and seed must be provided together")
    if generation is not None:
        if not isinstance(generation, GenerationSettings):
            raise TypeError("generation must be GenerationSettings")
        if type(seed) is not int or seed < 0 or seed > 0x7FFFFFFF:
            raise ValueError("seed must be a 31-bit non-negative integer")
        config["agent"] = {
            "build": {
                "temperature": generation.temperature,
                "options": {
                    "temperature": generation.temperature,
                    "seed": seed,
                    "max_tokens": generation.max_output_tokens,
                },
                "steps": 3,
            },
            "title": {"disable": True},
        }
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
    generation: GenerationSettings | None = None,
    seed: int | None = None,
) -> Path:
    """Write an explicit config path without creating a project `.opencode`.

    The evaluation runner uses this to keep runtime bootstrap files outside the
    immutable task workspace.
    """

    return write_opencode_config_payload(
        config_path,
        build_opencode_config(
            model,
            ollama_host,
            permission=permission,
            tools=tools,
            evaluation=True,
            generation=generation,
            seed=seed,
        ),
    )


def write_opencode_config_payload(
    config_path,
    config: dict,
) -> Path:
    out = Path(config_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return out


def _write_config(project_dir, cfg: dict) -> Path:
    project_dir = Path(project_dir)
    oc_dir = project_dir / ".opencode"
    oc_dir.mkdir(parents=True, exist_ok=True)
    out = oc_dir / "opencode.json"
    out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return out


def write_commands_into(cmd_dir, pro_available: bool = False, cli_prefix=None) -> list[Path]:
    cmd_dir = Path(cmd_dir)
    cmd_dir.mkdir(parents=True, exist_ok=True)
    lac = _quoted_cli(cli_prefix)
    commands = [
        ("scan.md", _SCAN_MD.format(lac=lac)),
        ("recommend.md", _RECOMMEND_MD.format(lac=lac)),
    ]
    if pro_available:
        commands.append(("tune.md", _TUNE_MD.format(lac=lac)))
    written = []
    for name, body in commands:
        p = cmd_dir / name
        p.write_text(body, encoding="utf-8")
        written.append(p)
    return written


def write_plugin_into(plugins_dir, cli_prefix=None) -> Path:
    plugins_dir = Path(plugins_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    out = plugins_dir / "lac.ts"
    body = _LAC_PLUGIN_TS.replace(
        "{lac_cli_json}", json.dumps(_resolve_cli_prefix(cli_prefix))
    )
    out.write_text(body, encoding="utf-8")
    return out


def write_agent_commands(project_dir, pro_available: bool = False, cli_prefix=None) -> list[Path]:
    return write_commands_into(
        Path(project_dir) / ".opencode" / "commands", pro_available, cli_prefix
    )


def write_agent_plugin(project_dir, cli_prefix=None) -> Path:
    return write_plugin_into(Path(project_dir) / ".opencode" / "plugins", cli_prefix)


_LAC_LOCAL_AGENT_MD = """\
---
description: Local-model coding agent prepared by LAC for this machine
mode: primary
model: ollama/{model}
temperature: 0.2
steps: 20
permission:
  edit: ask
  bash: ask
  webfetch: ask
  websearch: ask
  external_directory: deny
  task: deny
color: success
---
You are a coding agent running entirely on this machine, on a local model
prepared by LAC (hardware-scanned, context-raised, optionally tuned).

Local models do targeted work well and long open-ended loops badly:
- Keep changes small and targeted; one concern per edit.
- Verify with tools (read the file, run the check) instead of guessing.
- If a task grows past a few steps, stop and summarize progress and the
  next steps.
- Never claim work is done without a tool result that proves it.
"""

_LAC_REVIEW_AGENT_MD = """\
---
description: Read-only code and plan review on the local model (LAC)
mode: subagent
temperature: 0.1
permission:
  edit: deny
  bash: deny
  webfetch: deny
  websearch: deny
  external_directory: deny
  task: deny
---
You review code and plans on this machine's local model. You cannot change
files or run commands. Read what you need, then report: what is correct,
what is risky, and the smallest concrete fix for each issue.
"""


_PROFILES_MANIFEST = ".lac-profiles.json"


def _profile_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _read_profiles_manifest(agents_dir: Path) -> dict:
    try:
        data = json.loads(
            (agents_dir / _PROFILES_MANIFEST).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_agent_profiles_into(agents_dir, model) -> dict:
    """Write LAC's agent profiles, never clobbering user edits.

    A sidecar manifest records the hash of every profile LAC wrote. A
    profile whose on-disk content still matches the manifest is
    LAC-managed and gets refreshed (model updates propagate); anything
    else is treated as user-edited and preserved untouched.
    """
    agents_dir = Path(agents_dir)
    agents_dir.mkdir(parents=True, exist_ok=True)
    manifest = _read_profiles_manifest(agents_dir)
    profiles = [
        ("lac-local.md", _LAC_LOCAL_AGENT_MD.format(model=model)),
        ("lac-review.md", _LAC_REVIEW_AGENT_MD.format(model=model)),
    ]
    written: list[Path] = []
    preserved: list[Path] = []
    for name, body in profiles:
        path = agents_dir / name
        if path.exists():
            current = path.read_text(encoding="utf-8")
            lac_managed = manifest.get(name) == _profile_digest(current)
            if current != body and not lac_managed:
                preserved.append(path)
                continue
        path.write_text(body, encoding="utf-8")
        manifest[name] = _profile_digest(body)
        written.append(path)
    (agents_dir / _PROFILES_MANIFEST).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return {"written": written, "preserved": preserved}


def write_agent_profiles(project_dir, model) -> dict:
    return write_agent_profiles_into(
        Path(project_dir) / ".opencode" / "agents", model
    )
