#!/usr/bin/env python3
"""Leaf — chat with and manage local LLMs via Ollama."""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn
from rich.syntax import Syntax
from rich import box

try:
    from . import storage
except ImportError:
    from backend import storage

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

app = typer.Typer(help="Leaf — chat with and manage local LLMs via Ollama")
console = Console()
err_console = Console(stderr=True)

HOST_ENV = "OLLAMA_HOST"

def get_host():
    return os.environ.get(HOST_ENV, "http://localhost:11434")

def _ollama(method, path, body=None, timeout=30):
    url = f"{get_host()}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except urllib.error.URLError:
        return {"error": f"Cannot connect to Ollama at {get_host()}"}
    except Exception as e:
        return {"error": str(e)}

def _stream(path, body, timeout=300):
    url = f"{get_host()}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        for line in resp:
            decoded = line.decode().strip()
            if decoded:
                yield json.loads(decoded)
    except urllib.error.HTTPError as e:
        yield {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except urllib.error.URLError:
        yield {"error": f"Cannot connect to Ollama at {get_host()}"}
    except Exception as e:
        yield {"error": str(e)}

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, host: Optional[str] = typer.Option(None, help="Ollama host")):
    if host:
        os.environ[HOST_ENV] = host
    if ctx.invoked_subcommand is None:
        cmd_shell()

# ── Chat ──────────────────────────────────────────────────────────────────────

def _chat_impl(model_name, system_prompt, timeout, session_id=None):
    models = _ollama("GET", "/api/tags")
    if "error" in models:
        err_console.print(f"[red]Error: {models['error']}[/]")
        raise typer.Exit(1)
    model_list = [m["name"] for m in models.get("models", [])]
    if not model_list:
        err_console.print("[yellow]No models installed. Pull one first: leaf pull <model>[/]")
        raise typer.Exit(1)

    default_model = storage.get_setting("default_model", "")
    chosen = model_name or (default_model if default_model in model_list else model_list[0])
    if chosen not in model_list:
        err_console.print(f"[red]Model '{chosen}' not installed. Available: {', '.join(model_list)}[/]")
        raise typer.Exit(1)

    messages = []
    sp = system_prompt or ""

    if not session_id:
        session_id = storage.create_session(name=f"{chosen} - CLI", model=chosen, system_prompt=sp).id

    # Load existing messages if resuming a session
    stored = storage.get_messages(session_id)
    for m in stored:
        messages.append({"role": m.role, "content": m.content})

    if sp and not any(m["role"] == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": sp})
        console.print(f"[dim]System: {sp[:80]}{'...' if len(sp) > 80 else ''}[/]")

    console.print(f"[green]Chatting with [bold]{chosen}[/][/]")
    console.print(f"[dim]Session: {session_id[:8]}  |  Type /help for commands, Ctrl+C or /exit to quit.[/]\n")

    state = {"model": chosen}

    try:
        import readline
    except ImportError:
        pass

    while True:
        try:
            user_input = input("[cyan]You> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        except UnicodeDecodeError:
            console.print("[red]Input error. Try again.[/]")
            continue

        if not user_input:
            continue

        if user_input.startswith("/"):
            handled = _handle_slash(user_input, state["model"], messages, sp, session_id, state)
            if handled == "exit":
                break
            continue

        messages.append({"role": "user", "content": user_input})
        storage.add_message(session_id, "user", user_input)
        console.print(f"[green]{state['model']}> [/]", end="")

        full_response = ""
        for chunk in _stream("/api/chat", {"model": state["model"], "messages": messages, "stream": True}, timeout=timeout):
            if "error" in chunk:
                console.print(f"\n[red]Error: {chunk['error']}[/]")
                break
            if chunk.get("message", {}).get("content"):
                content = chunk["message"]["content"]
                full_response += content
                try:
                    print(content, end="", flush=True)
                except UnicodeEncodeError:
                    safe = content.encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding)
                    print(safe, end="", flush=True)
            if chunk.get("done"):
                break
        print()

        if full_response:
            messages.append({"role": "assistant", "content": full_response})
            storage.add_message(session_id, "assistant", full_response)


@app.command()
def chat(
    model: Optional[str] = typer.Argument(None, help="Model to use (default: first installed)"),
    system: Optional[str] = typer.Option(None, "--system", help="System prompt"),
    timeout: int = typer.Option(300, "--timeout", help="Request timeout in seconds"),
):
    """Interactive chat with a model."""
    _chat_impl(model, system, timeout)


def _handle_slash(cmd, model, messages, system_prompt, session_id=None, state=None):
    cmd = cmd.strip().lower()

    if cmd in ("/exit", "/quit"):
        console.print("[yellow]Goodbye![/]")
        return "exit"

    if cmd in ("/help", "/h"):
        console.print("\n[bold]Commands:[/]")
        for c, desc in [
            ("/help", "Show this help"),
            ("/clear", "Clear conversation"),
            ("/model <name>", "Switch model"),
            ("/system <prompt>", "Set system prompt"),
            ("/info", "Show current model info"),
            ("/save", "Save session"),
            ("/session", "Show session info"),
            ("/exit", "Exit chat"),
        ]:
            console.print(f"  [cyan]{c:<20}[/] [dim]{desc}[/]")
        return "continue"

    if cmd == "/clear":
        messages.clear()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        console.print("[yellow]Conversation cleared.[/]")
        return "continue"

    if cmd.startswith("/model "):
        new_model = cmd[7:].strip()
        info = _ollama("GET", "/api/tags")
        available = [m["name"] for m in info.get("models", [])]
        if new_model in available:
            messages.clear()
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if session_id:
                storage.update_session(session_id, model=new_model)
            if state is not None:
                state["model"] = new_model
            console.print(f"[green]Switched to {new_model}[/]")
        else:
            console.print(f"[red]Model '{new_model}' not installed.[/]")
        return "continue"

    if cmd.startswith("/system "):
        sp = cmd[8:].strip()
        if sp:
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = sp
            else:
                messages.insert(0, {"role": "system", "content": sp})
            if session_id:
                storage.update_session(session_id, system_prompt=sp)
            console.print("[yellow]System prompt set.[/]")
        else:
            current = messages[0]["content"] if messages and messages[0]["role"] == "system" else "(none)"
            console.print(f"[yellow]Current system prompt: {current}[/]")
        return "continue"

    if cmd == "/info":
        info = _ollama("POST", "/api/show", {"name": model})
        if "error" in info:
            console.print(f"[red]{info['error']}[/]")
        else:
            console.print(f"\n[bold]Model:[/] {model}")
            details = info.get("details", {})
            console.print(f"  Parameters: {details.get('parameter_size', '?')}")
            console.print(f"  Quantization: {details.get('quantization_level', '?')}")
            console.print(f"  Family: {details.get('family', '?')}")
        return "continue"

    if cmd == "/session":
        if session_id:
            sess = storage.get_session(session_id)
            if sess:
                console.print(f"\n[bold]Session:[/] {sess.id[:8]}")
                console.print(f"  Name: {sess.name or 'Untitled'}")
                console.print(f"  Model: {sess.model}")
                console.print(f"  Messages: {sess.message_count}")
                console.print(f"  Created: {time.strftime('%Y-%m-%d %H:%M', time.localtime(sess.created_at))}")
            else:
                console.print("[yellow]No current session.[/]")
        else:
            console.print("[yellow]No session attached.[/]")
        return "continue"

    if cmd == "/save":
        if session_id:
            storage.update_session(session_id, name=f"{model} - CLI")
            count = storage.get_session(session_id).message_count
            console.print(f"[green]Session saved ({count} messages).[/]")
        else:
            console.print("[yellow]No active session to save.[/]")
        return "continue"

    console.print(f"[red]Unknown command: {cmd}[/]")
    return "continue"


# ── List ──────────────────────────────────────────────────────────────────────

@app.command("list")

@app.command("ls")

def list_models():
    """List installed models."""
    result = _ollama("GET", "/api/tags")
    if "error" in result:
        err_console.print(f"[red]{result['error']}[/]")
        raise typer.Exit(1)
    models = result.get("models", [])
    if not models:
        console.print("[yellow]No models installed. Pull one: leaf pull <model>[/]")
        return

    table = Table(title=f"Installed Models ({len(models)})", box=box.SIMPLE)
    table.add_column("Name", style="bold")
    table.add_column("Size")
    table.add_column("Modified")
    for m in sorted(models, key=lambda x: x["name"]):
        table.add_row(m["name"], f"{round(m.get('size', 0) / (1024**3), 2)} GB", (m.get("modified_at") or "")[:10])
    console.print(table)


# ── Pull ──────────────────────────────────────────────────────────────────────

@app.command()
def pull(model: str = typer.Argument(..., help="Model name (e.g. llama3.2:3b)")):
    """Download a model."""
    console.print(f"[yellow]Pulling [bold]{model}[/]...[/]")

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Downloading {model}", total=100)
        for chunk in _stream("/api/pull", {"name": model}, timeout=3600):
            if "error" in chunk:
                err_console.print(f"\n[red]Error: {chunk['error']}[/]")
                raise typer.Exit(1)
            status = chunk.get("status", "")
            completed = chunk.get("completed", 0)
            total = chunk.get("total", 0)
            if total and completed:
                progress.update(task, completed=completed / total * 100, description=f"{status}")
            elif status:
                progress.update(task, description=f"{status}")

    console.print(f"\n[green]✓ {model} installed successfully![/]")


# ── Delete ────────────────────────────────────────────────────────────────────

@app.command()
def delete(model: str = typer.Argument(..., help="Model name"), yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation")):
    """Delete a model."""
    if not yes:
        console.print(f"[yellow]Delete [bold]{model}[/]? [y/N] [/]", end="")
        resp = input().strip().lower()
        if resp != "y":
            console.print("[dim]Cancelled.[/]")
            return
    result = _ollama("DELETE", "/api/delete", {"name": model})
    if "error" in result:
        err_console.print(f"[red]{result['error']}[/]")
        raise typer.Exit(1)
    console.print(f"[green]✓ {model} deleted.[/]")


# ── PS ────────────────────────────────────────────────────────────────────────

@app.command()
def ps():
    """Show running models."""
    result = _ollama("GET", "/api/ps")
    if "error" in result:
        err_console.print(f"[red]{result['error']}[/]")
        raise typer.Exit(1)
    models = result.get("models", [])
    if not models:
        console.print("[yellow]No models currently loaded in memory.[/]")
        return
    table = Table(title=f"Running Models ({len(models)})", box=box.SIMPLE)
    table.add_column("Name", style="bold")
    table.add_column("Size")
    table.add_column("VRAM")
    for m in models:
        vram = m.get("size_vram", 0)
        table.add_row(m["name"], f"{round(m.get('size', 0) / (1024**3), 2)} GB", f"{round(vram / (1024**3), 2)} GB" if vram else "?")
    console.print(table)


# ── Inspect ────────────────────────────────────────────────────────────────────

@app.command()
def inspect(model: str = typer.Argument(..., help="Model name")):
    """Show model details."""
    result = _ollama("POST", "/api/show", {"name": model})
    if "error" in result:
        err_console.print(f"[red]{result['error']}[/]")
        raise typer.Exit(1)

    console.print(Panel(f"[bold]{model}[/]", title="Model"))
    details = result.get("details", {})
    for label, value in [
        ("Parameters", details.get("parameter_size", "?")),
        ("Quantization", details.get("quantization_level", "?")),
        ("Family", details.get("family", "?")),
        ("Format", details.get("format", "?")),
        ("Size", f"{round(result.get('size', 0) / (1024**3), 2)} GB"),
        ("Modified", result.get("modified_at", "?")),
    ]:
        console.print(f"  [bold]{label}:[/] {value}")
    modelfile = result.get("modelfile", "")
    for line in modelfile.split("\n"):
        if line.startswith("FROM "):
            console.print(f"  [bold]Base:[/] {line[5:].strip()}")
        if "CONTEXT_LENGTH" in line:
            parts = line.split()
            if len(parts) > 1:
                console.print(f"  [bold]Context:[/] {parts[1]}")
    print()


# ── Scan ──────────────────────────────────────────────────────────────────────

@app.command()
def scan():
    """Scan hardware."""
    console.print("[yellow]Scanning hardware...[/]")
    try:
        from .cookbook.hardware import detect
        info = detect()
        table = Table(title="System", box=box.SIMPLE)
        table.add_column("Property", style="bold")
        table.add_column("Value")
        table.add_row("OS", info.os)
        table.add_row("CPU", info.cpu)
        table.add_row("Cores", str(info.cpu_cores))
        table.add_row("RAM", f"{info.ram_gb} GB")
        if info.gpus:
            for i, gpu in enumerate(info.gpus):
                table.add_row(f"GPU {i+1}", f"{gpu.name} ({gpu.vram_gb} GB, {gpu.backend})")
        else:
            table.add_row("GPU", "None detected")
        if info.is_apple_silicon:
            table.add_row("Apple Silicon", "Yes")
        console.print(table)

        total_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
        if total_vram:
            console.print(f"[bold]Total VRAM:[/] {total_vram} GB")
            console.print(f"[bold]Models that fit:[/] Up to ~{int(total_vram / 0.58 * 0.9)}B params at Q4_K_M")
    except ImportError as e:
        err_console.print(f"[red]Error loading hardware scanner: {e}[/]")
        err_console.print("[dim]Run from the Leaf project directory.[/]")
        raise typer.Exit(1)


# ── Recommend ──────────────────────────────────────────────────────────────────

@app.command()
def recommend(
    use_case: str = typer.Option("coding", "--use-case", help="Use case"),
    top_k: int = typer.Option(10, "--top-k", help="Number of recommendations"),
):
    """Get model recommendations."""
    try:
        from .cookbook.hardware import detect
        from .cookbook.recommend import recommend as _recommend

        console.print("[yellow]Scanning hardware and computing recommendations...[/]")
        info = detect()
        default_use_case = storage.get_setting("default_use_case", "coding")
        uc = use_case or default_use_case
        recs = _recommend(info, use_case=uc, top_k=top_k)

        if not recs:
            console.print("[yellow]No models fit your hardware.[/]")
            return

        table = Table(title=f"Top {len(recs)} Recommendations for '{uc}'", box=box.SIMPLE)
        table.add_column("#")
        table.add_column("Model", style="bold")
        table.add_column("Quant")
        table.add_column("Score")
        table.add_column("VRAM")
        table.add_column("Ctx")
        table.add_column("Mode")
        for i, r in enumerate(recs, 1):
            mode = f"[green]GPU[/]" if r.run_mode == "gpu" else f"[yellow]Offload[/]"
            table.add_row(str(i), r.model.name, r.quant, str(r.score), f"{r.vram_gb} GB", str(r.context_used), mode)
        console.print(table)

    except ImportError as e:
        err_console.print(f"[red]Error: {e}[/]")
        err_console.print("[dim]Run from the Leaf project directory.[/]")
        raise typer.Exit(1)


# ── Browse ─────────────────────────────────────────────────────────────────────

@app.command()
def browse(
    query: Optional[str] = typer.Argument(None, help="Search query"),
    sort: str = typer.Option("pulls", "--sort", help="Sort order"),
    limit: int = typer.Option(30, "--limit", help="Max results"),
):
    """Browse model library."""
    try:
        cache_path = Path(__file__).parent / "backend" / "cookbook" / "data" / "library_cache.json"
        if not cache_path.exists():
            err_console.print("[red]Library cache not found. Run the web server first to populate it.[/]")
            raise typer.Exit(1)
        with open(cache_path) as f:
            data = json.load(f)
        models = data.get("models", [])
    except Exception as e:
        err_console.print(f"[red]Error loading library: {e}[/]")
        raise typer.Exit(1)

    if query:
        q = query.lower()
        models = [m for m in models if q in m.get("display", m.get("name", "")).lower() or q in m.get("description", "").lower()]

    models = [m for m in models if m.get("vram_q4", 0) > 0]

    if sort == "pulls":
        def parse_pulls(p):
            try:
                p = p.replace("M", "e6").replace("B", "e9").replace("K", "e3")
                return float(p)
            except (ValueError, TypeError):
                return 0
        models.sort(key=lambda m: parse_pulls(m.get("pulls", "0")), reverse=True)
    elif sort == "vram":
        models.sort(key=lambda m: m.get("vram_q4", 999))
    elif sort == "params":
        models.sort(key=lambda m: m.get("params_b", 0), reverse=True)
    elif sort == "name":
        models.sort(key=lambda m: m.get("display", m.get("name", "")))

    console.print(f"[bold]Model Library ({len(models)} variants)[/]")
    for m in models[:limit]:
        vram_q4 = m.get("vram_q4", 0)
        if vram_q4:
            color = "green" if vram_q4 <= 16 else ("yellow" if vram_q4 <= 32 else "red")
            console.print(f"  [bold]{m.get('display', m.get('name', '?')):<40}[/] [{color}]{vram_q4:>5.1f}GB Q4[/]  [dim]{m.get('vram_q8', 0):>5.1f}GB Q8  {m.get('params_b', 0):>5.1f}B  ctx={m.get('context', 0):<6}  pulls={m.get('pulls', '0')}[/]")

    remaining = len(models) - limit
    if remaining > 0:
        console.print(f"\n[dim]... and {remaining} more. Use --limit to show more.[/]")


# ── Sessions ───────────────────────────────────────────────────────────────────

@app.command()
def sessions(
    sub: str = typer.Argument("list", help="Sub-command: list, load, delete"),
    session_id: Optional[str] = typer.Argument(None, help="Session ID"),
):
    """Manage chat sessions."""
    if sub == "list":
        all_sessions = storage.list_sessions()
        if not all_sessions:
            console.print("[yellow]No saved sessions.[/]")
            return
        table = Table(title=f"Sessions ({len(all_sessions)})", box=box.SIMPLE)
        table.add_column("ID", style="bold")
        table.add_column("Name")
        table.add_column("Model")
        table.add_column("Messages")
        table.add_column("Updated")
        for s in all_sessions:
            table.add_row(s.id[:8], (s.name[:30] or "Untitled"), (s.model[:20] or "—"), str(s.message_count),
                          time.strftime("%Y-%m-%d %H:%M", time.localtime(s.updated_at)))
        console.print(table)

    elif sub == "load":
        if not session_id:
            err_console.print("[red]Usage: sessions load <session_id>[/]")
            return
        sess = storage.get_session(session_id)
        if not sess:
            err_console.print("[red]Session not found.[/]")
            return
        console.print(f"[green]Loaded session: {sess.name or 'Untitled'}[/]")
        console.print(f"[dim]Model: {sess.model}  Messages: {sess.message_count}[/]")
        _chat_impl(model_name=sess.model, system_prompt=sess.system_prompt, timeout=300, session_id=session_id)

    elif sub == "delete":
        if not session_id:
            err_console.print("[red]Usage: sessions delete <session_id>[/]")
            return
        sess = storage.get_session(session_id)
        if not sess:
            err_console.print("[red]Session not found.[/]")
            return
        console.print(f"[yellow]Delete session '{sess.name or session_id[:8]}'? [y/N] [/]", end="")
        resp = input().strip().lower()
        if resp == "y":
            storage.delete_session(session_id)
            console.print("[green]Session deleted.[/]")

    else:
        err_console.print(f"[red]Unknown sub-command: {sub}. Use list, load, or delete.[/]")


# ── Settings ───────────────────────────────────────────────────────────────────

@app.command()
def settings(
    sub: str = typer.Argument("list", help="Sub-command: list, set"),
    key_value: Optional[str] = typer.Argument(None, help="key=value"),
):
    """View or change settings."""
    if sub == "list":
        all_settings = storage.get_settings_with_defaults()
        table = Table(title="Settings", box=box.SIMPLE)
        table.add_column("Key", style="bold")
        table.add_column("Value")
        for k, v in all_settings.items():
            table.add_row(k, str(v))
        console.print(table)
    elif sub == "set":
        if not key_value or "=" not in key_value:
            err_console.print("[red]Usage: settings set <key>=<value>[/]")
            return
        key, value = key_value.split("=", 1)
        value = value.strip()
        for t in (int, float):
            try:
                value = t(value)
                break
            except (ValueError, TypeError):
                continue
        if value == "true": value = True
        elif value == "false": value = False
        storage.set_setting(key.strip(), value)
        console.print(f"[green]Set {key.strip()} = {value}[/]")
    else:
        err_console.print(f"[red]Unknown sub-command: {sub}. Use list or set.[/]")


# ── Interactive Shell ─────────────────────────────────────────────────────────

def cmd_shell():
    settings_data = storage.get_settings_with_defaults()
    host = settings_data.get("ollama_host", "http://localhost:11434")
    os.environ[HOST_ENV] = host

    console.print(Panel("[bold green]Leaf Interactive Shell[/]\n[dim]Type /help for commands, Ctrl+C or /exit to quit.[/]"))

    while True:
        try:
            cmd = input("[bold green]leaf> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue
        if cmd in ("/exit", "/quit"):
            break
        if cmd == "/help":
            console.print("\n[bold]Commands:[/]")
            for c, desc in [
                ("chat [model]", "Start chat"),
                ("list", "List installed models"),
                ("pull <model>", "Download a model"),
                ("delete <model>", "Delete a model"),
                ("ps", "Show running models"),
                ("inspect <model>", "Show model details"),
                ("scan", "Scan hardware"),
                ("recommend", "Get recommendations"),
                ("browse [q]", "Browse model library"),
                ("sessions", "List sessions"),
                ("sessions load <id>", "Load session"),
                ("settings", "Show settings"),
                ("settings set <k>=<v>", "Set setting"),
                ("/exit", "Exit shell"),
            ]:
                console.print(f"  [cyan]{c:<30}[/] [dim]{desc}[/]")
            continue

        parts = cmd.split()
        base = parts[0]
        rest = parts[1:]

        if base == "chat":
            ns_model = rest[0] if rest else None
            chat(model=ns_model)
        elif base in ("list", "ls"):
            list_models()
        elif base == "pull":
            if not rest:
                err_console.print("[red]Specify a model to pull.[/]")
            else:
                pull(model=rest[0])
        elif base in ("delete", "rm"):
            if not rest:
                err_console.print("[red]Specify a model to delete.[/]")
            else:
                delete(model=rest[0], yes=False)
        elif base == "ps":
            ps()
        elif base == "inspect":
            if not rest:
                err_console.print("[red]Specify a model to inspect.[/]")
            else:
                inspect(model=rest[0])
        elif base == "scan":
            scan()
        elif base in ("recommend", "rec"):
            uc = rest[1] if len(rest) > 1 and rest[0] == "--use-case" else "coding"
            recommend(use_case=uc, top_k=10)
        elif base == "browse":
            q = " ".join(rest) if rest else None
            browse(query=q, sort="pulls", limit=30)
        elif base == "sessions":
            sub_cmd = rest[0] if rest else "list"
            sid = rest[1] if len(rest) > 1 else None
            sessions(sub=sub_cmd, session_id=sid)
        elif base == "settings":
            sub_cmd = rest[0] if rest else "list"
            kv = rest[1] if len(rest) > 1 else None
            settings(sub=sub_cmd, key_value=kv)
        else:
            err_console.print(f"[red]Unknown command: {base}[/]")


def main():
    app()

if __name__ == "__main__":
    app()
