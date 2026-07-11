from __future__ import annotations

import os
import stat
import subprocess
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable

from backend.cookbook import proc
from backend.project_security import (
    SensitiveProjectPathError,
    list_project_directory,
    read_project_text,
    resolve_project_path,
)
from backend.version import __version__

ToolHandler = Callable[[dict, dict], str]


def _project_tool_error(exc: SensitiveProjectPathError) -> str:
    if exc.code in {"invalid_project_path", "unsafe_project_path"}:
        return "error: path outside workspace or unsafe project path"
    return f"error: {exc.message}"


def _read_file(args: dict, ctx: dict) -> str:
    try:
        _relative, content = read_project_text(
            ctx.get("cwd", "."), args.get("path", "")
        )
        return content
    except SensitiveProjectPathError as exc:
        return _project_tool_error(exc)


def _write_file(args: dict, ctx: dict) -> str:
    content = args.get("content", "")
    if not isinstance(content, str):
        return "error: content must be text"
    try:
        target, rel = resolve_project_path(
            ctx.get("cwd", "."), args.get("path", "")
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target, rel = resolve_project_path(ctx.get("cwd", "."), rel)
        if target.exists():
            target_st = target.lstat()
            if (
                not stat.S_ISREG(target_st.st_mode)
                or int(getattr(target_st, "st_nlink", 1) or 1) > 1
            ):
                return "error: project file target is unsafe"
        payload = content.encode("utf-8")
        tmp = target.parent / f".{target.name}.{uuid.uuid4().hex}.lac-tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= int(getattr(os, "O_BINARY", 0))
        fd: int | None = None
        try:
            fd = os.open(tmp, flags, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            revalidated, revalidated_rel = resolve_project_path(
                ctx.get("cwd", "."), rel
            )
            if revalidated != target or revalidated_rel != rel:
                return "error: project file target changed before write"
            os.replace(tmp, target)
        finally:
            if fd is not None:
                os.close(fd)
            tmp.unlink(missing_ok=True)
    except SensitiveProjectPathError as exc:
        return _project_tool_error(exc)
    except OSError:
        return "error: project file could not be written"
    return f"wrote {len(content)} bytes to {rel}"


def _list_files(args: dict, ctx: dict) -> str:
    try:
        _relative, safe_entries, truncated = list_project_directory(
            ctx.get("cwd", "."), args.get("path", ".")
        )
    except SensitiveProjectPathError as exc:
        return _project_tool_error(exc)
    entries = [
        f"{'d' if entry.is_dir else 'f'} {entry.size:>10} {entry.name}"
        for entry in safe_entries
    ]
    if truncated:
        entries.append("... (listing truncated)")
    return "\n".join(entries) if entries else "(empty)"


def _run_bash(args: dict, ctx: dict) -> str:
    cmd = args.get("command", "")
    if not cmd:
        return "error: no command"
    cwd = ctx.get("cwd", ".")
    try:
        proc_result = proc.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=60
        )
        out = (proc_result.stdout or "") + (proc_result.stderr or "")
        return f"[exit {proc_result.returncode}]\n{out.strip()}"
    except subprocess.TimeoutExpired:
        return "error: command timed out (60s)"
    except Exception as e:
        return f"error: {e}"


def _web_search(args: dict, ctx: dict) -> str:
    query = args.get("query", "")
    if not query:
        return "error: no query"
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": f"LAC/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode(errors="replace")
        import re

        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html)[:8]
        clean = [re.sub(r"<[^>]+>", "", t).strip() for t in titles]
        return "\n".join(f"- {t}" for t in clean) if clean else "(no results)"
    except Exception as e:
        return f"error: {e}"


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a text file relative to the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path relative to workspace."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories at a path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a shell command and return stdout+stderr. Use for build, test, git.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via DuckDuckGo and return result titles.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "read_file": _read_file,
    "write_file": _write_file,
    "list_files": _list_files,
    "run_bash": _run_bash,
    "web_search": _web_search,
}

WRITE_TOOLS = {"write_file", "run_bash"}
DELETE_TOOLS = set()
NETWORK_TOOLS = {"web_search"}


def setup(host) -> None:
    if host is None:
        return
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        handler = TOOL_HANDLERS.get(name)
        if handler is not None:
            try:
                host.register_tool(name, schema["function"]["description"], schema["function"]["parameters"], handler)
            except Exception:
                pass
