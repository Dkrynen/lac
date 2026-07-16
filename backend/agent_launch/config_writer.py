"""Emit the on-disk OpenCode configuration LAC drives it with: an Ollama provider
pointed at the LAC-chosen model, plus LAC hardware slash-commands. Written into the
project's `.opencode/` dir. We never edit OpenCode itself -- only its config."""
import json
from pathlib import Path

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
    project_dir = Path(project_dir)
    oc_dir = project_dir / ".opencode"
    oc_dir.mkdir(parents=True, exist_ok=True)
    base_url = ollama_host.rstrip("/") + "/v1"
    cfg = {
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
