from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from typing import Any


DEFAULT_PROMPT = (
    "In one short sentence, confirm this LAC smoke test is working and mention local model streaming."
)


def request_json(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str | None = None,
    timeout: int = 60,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method or ("POST" if payload is not None else "GET"),
        headers={"User-Agent": "LAC-runtime-smoke/1"},
    )
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


def stream_chat(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: int = 700,
) -> dict[str, Any]:
    body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/ollama/chat",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "LAC-runtime-smoke/1",
        },
    )

    start = time.perf_counter()
    first_chunk_ms: float | None = None
    chunks: list[str] = []
    errors: list[str] = []
    final: dict[str, Any] = {}
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        content_type = resp.headers.get("content-type", "")
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            event = json.loads(payload)
            if "error" in event:
                errors.append(str(event["error"]))
                continue
            content = ((event.get("message") or {}).get("content") or "")
            if content:
                if first_chunk_ms is None:
                    first_chunk_ms = (time.perf_counter() - start) * 1000
                chunks.append(content)
            if event.get("done"):
                final = event

    total_wall_ms = (time.perf_counter() - start) * 1000
    eval_count = int(final.get("eval_count") or 0)
    eval_duration_ns = int(final.get("eval_duration") or 0)
    tokens_per_second = (
        eval_count / (eval_duration_ns / 1e9)
        if eval_count and eval_duration_ns
        else None
    )
    return {
        "status": status,
        "content_type": content_type,
        "first_chunk_ms": round(first_chunk_ms, 1) if first_chunk_ms is not None else None,
        "total_wall_ms": round(total_wall_ms, 1),
        "eval_count": eval_count,
        "eval_duration_ms": round(eval_duration_ns / 1e6, 1) if eval_duration_ns else None,
        "tokens_per_second": round(tokens_per_second, 2) if tokens_per_second else None,
        "response": "".join(chunks).strip(),
        "errors": errors,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    _, version = request_json(args.app_url, "/api/system/version", timeout=args.timeout)
    name = args.session_name or f"QA Smoke Test {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    status, session = request_json(
        args.app_url,
        "/api/sessions",
        {
            "name": name,
            "model": args.model,
            "system_prompt": args.system_prompt,
            "workspace": args.workspace,
        },
        timeout=args.timeout,
    )
    session_id = session.get("id")

    warm: dict[str, Any] | None = None
    if not args.skip_warm:
        _, warm = request_json(
            args.app_url,
            "/api/ollama/warm",
            {"model": args.model, "wait": True},
            timeout=max(args.timeout, 700),
        )

    messages = [
        {"role": "system", "content": args.system_prompt},
        {"role": "user", "content": args.prompt},
    ]
    chat = stream_chat(args.app_url, args.model, messages, timeout=max(args.timeout, 700))
    if chat["response"]:
        messages.append({"role": "assistant", "content": chat["response"]})
    _, saved = request_json(
        args.app_url,
        f"/api/sessions/{session_id}",
        {
            "name": name,
            "model": args.model,
            "messages": messages,
            "workspace": args.workspace,
        },
        method="PUT",
        timeout=args.timeout,
    )
    _, fetched = request_json(args.app_url, f"/api/sessions/{session_id}", timeout=args.timeout)

    deleted = False
    if args.delete_session:
        _, delete_result = request_json(
            args.app_url,
            f"/api/sessions/{session_id}",
            method="DELETE",
            timeout=args.timeout,
        )
        deleted = bool(delete_result.get("success"))

    ok = (
        version.get("version")
        and status == 201
        and chat.get("status") == 200
        and not chat.get("errors")
        and bool(chat.get("response"))
        and bool(saved.get("success"))
    )
    return {
        "ok": bool(ok),
        "app": version,
        "model": args.model,
        "session": {
            "id": session_id,
            "name": name,
            "created_status": status,
            "saved": bool(saved.get("success")),
            "fetched_message_count": len(fetched.get("messages") or []),
            "deleted": deleted,
        },
        "warm": warm,
        "chat": chat,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a live LAC installed-app chat smoke test.")
    p.add_argument("--app-url", default="http://127.0.0.1:5050", help="Running LAC app base URL.")
    p.add_argument("--model", default="qwen2.5:0.5b", help="Installed Ollama model to test.")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt sent through LAC chat streaming.")
    p.add_argument("--system-prompt", default="You are a concise local QA assistant.")
    p.add_argument("--workspace", default="")
    p.add_argument("--session-name", default="")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--skip-warm", action="store_true", help="Do not preload the model before chat.")
    p.add_argument("--delete-session", action="store_true", help="Delete the QA session after the smoke test.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
