"""`lac agent --customize` (tweakability 3c): open the project's LAC agent
profile for editing. The profiles are written first if missing, and the
existing no-clobber manifest guarantees user edits survive every later
`lac agent` run — this module only gets the file into the user's editor."""
import os
import shlex
from pathlib import Path

from .config_writer import write_agent_profiles


def resolve_editor() -> list[str]:
    """EDITOR, then VISUAL, then the platform's plain-text fallback."""
    for var in ("EDITOR", "VISUAL"):
        value = os.environ.get(var, "").strip()
        if value:
            return shlex.split(value)
    if os.name == "nt":
        return ["notepad"]
    return ["vi"]


def open_agent_profile(project_dir, model: str, *,
                       write_profiles_fn=write_agent_profiles,
                       editor_fn=None,
                       run=None,
                       out=print) -> int:
    project_dir = Path(project_dir).resolve()
    write_profiles_fn(project_dir, model)
    profile = project_dir / ".opencode" / "agents" / "lac-local.md"
    if editor_fn is not None:
        editor_fn(profile)
    else:
        from backend.cookbook.proc import run_interactive
        runner = run if run is not None else run_interactive
        runner(resolve_editor() + [str(profile)])
    out("Opened %s — LAC never clobbers profiles you have edited." % profile)
    return 0
