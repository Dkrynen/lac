from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_smoke import request_json, stream_chat


TERMINAL_IMPORT_STATES = {"done", "failed", "cancelled", "not_licensed"}
DEFAULT_REPO_ID = "bartowski/Qwen2.5-0.5B-Instruct-GGUF"
DEFAULT_QUANT = "Q4_K_M"
DEFAULT_FILENAME = "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"


def model_names(payload: Any) -> set[str]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("models") or payload.get("value") or []
    else:
        rows = []
    return {
        str(row.get("name"))
        for row in rows
        if isinstance(row, dict) and row.get("name")
    }


def get_models(base_url: str, timeout: int) -> set[str]:
    _, payload = request_json(base_url, "/api/ollama/models", timeout=timeout)
    return model_names(payload)


def import_status_path(repo_id: str) -> str:
    return "/api/pro/import-status?" + urllib.parse.urlencode({"repo_id": repo_id})


def preflight_path(target: str) -> str:
    return "/api/model/install-preflight?" + urllib.parse.urlencode({"target": target})


def wait_for_import(args: argparse.Namespace) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + args.import_timeout
    final: dict[str, Any] = {"state": "timeout"}
    while time.monotonic() < deadline:
        time.sleep(args.poll_interval)
        _, status = request_json(args.app_url, import_status_path(args.repo_id), timeout=args.timeout)
        final = status
        events.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "state": status.get("state"),
            "stage": status.get("stage"),
            "current_file": status.get("current_file"),
            "bytes_done": status.get("bytes_done"),
            "bytes_total": status.get("bytes_total"),
            "model_name": status.get("model_name"),
            "quant": status.get("quant"),
            "error_type": status.get("error_type"),
            "message": status.get("message"),
        })
        if status.get("state") in TERMINAL_IMPORT_STATES:
            break
    return {"final": final, "events": events, "event_count": len(events)}


def run_delete_check(args: argparse.Namespace) -> dict[str, Any]:
    ollama = shutil.which("ollama")
    scratch_name = args.delete_model or f"lac-delete-smoke-{datetime.now().strftime('%Y%m%d%H%M%S')}:latest"
    if not ollama:
        return {"ok": False, "skipped": True, "reason": "ollama CLI not found", "model": scratch_name}

    copied = subprocess.run(
        [ollama, "cp", args.delete_from_model, scratch_name],
        capture_output=True,
        text=True,
        timeout=args.timeout,
        check=False,
    )
    before = get_models(args.app_url, args.timeout)
    delete_status = 0
    delete_payload: dict[str, Any] = {}
    exists_after = scratch_name in before
    cleanup_error = None
    try:
        delete_status, delete_payload = request_json(
            args.app_url,
            "/api/ollama/delete",
            {"model": scratch_name},
            timeout=max(args.timeout, 60),
        )
        time.sleep(1)
        after = get_models(args.app_url, args.timeout)
        exists_after = scratch_name in after
    finally:
        if exists_after:
            cleanup = subprocess.run(
                [ollama, "rm", scratch_name],
                capture_output=True,
                text=True,
                timeout=args.timeout,
                check=False,
            )
            if cleanup.returncode != 0:
                cleanup_error = (cleanup.stderr or cleanup.stdout or "").strip()

    ok = (
        copied.returncode == 0
        and scratch_name in before
        and delete_status == 200
        and bool(delete_payload.get("success"))
        and not exists_after
    )
    return {
        "ok": ok,
        "model": scratch_name,
        "source_model": args.delete_from_model,
        "copy": {
            "returncode": copied.returncode,
            "stdout": copied.stdout.strip(),
            "stderr": copied.stderr.strip(),
        },
        "existed_before_delete": scratch_name in before,
        "delete": delete_payload,
        "exists_after_delete": exists_after,
        "cleanup_error": cleanup_error,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    _, version = request_json(args.app_url, "/api/system/version", timeout=args.timeout)
    _, plugins = request_json(args.app_url, "/api/plugins", timeout=args.timeout)
    before_models = get_models(args.app_url, args.timeout)

    target = args.target or f"hf.co/{args.repo_id}:{args.quant}"
    _, preflight = request_json(args.app_url, preflight_path(target), timeout=max(args.timeout, 30))
    kickoff_status, kickoff = request_json(
        args.app_url,
        "/api/pro/import-model",
        {"repo_id": args.repo_id, "quant": args.quant, "filename": args.filename},
        timeout=args.timeout,
    )
    import_run = wait_for_import(args)
    final = import_run["final"]
    model_name = final.get("model_name")
    after_import_models = get_models(args.app_url, args.timeout)

    warm: dict[str, Any] | None = None
    chat: dict[str, Any] | None = None
    if model_name:
        _, warm = request_json(
            args.app_url,
            "/api/ollama/warm",
            {"model": model_name, "wait": True},
            timeout=max(args.timeout, 700),
        )
        chat = stream_chat(
            args.app_url,
            model_name,
            [
                {"role": "system", "content": "You are a concise local QA assistant."},
                {"role": "user", "content": "In one short sentence, confirm this imported model can respond."},
            ],
            timeout=max(args.timeout, 700),
        )

    delete_check = None if args.skip_delete_check else run_delete_check(args)

    import_ok = (
        kickoff_status == 200
        and bool(kickoff.get("accepted"))
        and final.get("state") == "done"
        and bool(model_name)
        and model_name in after_import_models
    )
    chat_ok = bool(chat and chat.get("status") == 200 and not chat.get("errors") and chat.get("response"))
    delete_ok = bool(args.skip_delete_check or (delete_check and delete_check.get("ok")))
    preflight_ok = preflight.get("state") not in {"blocked", "error"}
    ok = bool(version.get("version") and preflight_ok and import_ok and chat_ok and delete_ok)

    return {
        "ok": ok,
        "app": version,
        "plugins": plugins,
        "preflight": preflight,
        "import": {
            "kickoff_status": kickoff_status,
            "kickoff": kickoff,
            "final": final,
            "event_count": import_run["event_count"],
            "events_tail": import_run["events"][-8:],
            "model_preexisted": bool(model_name and model_name in before_models),
            "model_present_after": bool(model_name and model_name in after_import_models),
        },
        "warm": warm,
        "chat": chat,
        "delete_check": delete_check,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run live LAC HF import + delete stress verification.")
    p.add_argument("--app-url", default="http://127.0.0.1:5050", help="Running LAC app base URL.")
    p.add_argument("--repo-id", default=DEFAULT_REPO_ID, help="Hugging Face GGUF repo to import.")
    p.add_argument("--quant", default=DEFAULT_QUANT, help="GGUF quant to import.")
    p.add_argument("--filename", default=DEFAULT_FILENAME, help="Exact GGUF filename to import.")
    p.add_argument("--target", default="", help="Optional pasted target for install preflight.")
    p.add_argument("--delete-from-model", default="qwen2.5:0.5b", help="Existing local model to clone for delete testing.")
    p.add_argument("--delete-model", default="", help="Optional scratch model name to use for delete testing.")
    p.add_argument("--skip-delete-check", action="store_true", help="Skip disposable clone/delete verification.")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--import-timeout", type=int, default=600)
    p.add_argument("--poll-interval", type=float, default=2.0)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
