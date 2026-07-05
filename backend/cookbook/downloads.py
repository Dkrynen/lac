"""Shared download-history logging, used by both the CLI (`lac pull`) and
the web API's install path (POST /api/ollama/pull) so both entry points
feed the same ~/.model-hub/downloads/history.jsonl file. Before this
module existed, only the CLI's own copy of this logic ever wrote to that
file -- the web UI's install button never called it, so the Downloads
page was permanently empty for anyone who only ever installs via the web
UI."""
from __future__ import annotations

import json
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".model-hub"


def log_download(model_name: str, status: str = "completed", size_gb: float = 0) -> None:
    try:
        log_dir = CONFIG_DIR / "downloads"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "history.jsonl"
        entry = {
            "model": model_name,
            "status": status,
            "size_gb": size_gb,
            "timestamp": time.time(),
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def download_history() -> list[dict]:
    log_file = CONFIG_DIR / "downloads" / "history.jsonl"
    if not log_file.exists():
        return []
    history = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return history
