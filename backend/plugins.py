"""Open-core plugin seam.

Plugins are Python packages exposing an entry point in the ``lac.plugins``
group. The entry point resolves to a plugin object with:

- ``name: str``            display name (falls back to the entry-point name)
- ``version: str``         plugin version (falls back to "?")
- ``register_cli(subparsers)``  optional — add argparse subcommands
- ``register_api(app)``         optional — add Flask routes

A plugin that raises during load or registration must never break core:
every call is isolated and errors are captured on the LoadedPlugin record.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path

GROUP = "lac.plugins"


def _ensure_plugin_dir_on_path() -> None:
    """Make the bootstrap plugin dir (`lac unlock` installs there) visible to
    entry-point discovery by prepending it to ``sys.path`` BEFORE the
    entry-point read. If the dir doesn't exist, skip cleanly — zero behavior
    change. Never raises: the seam must never break core."""
    try:
        from backend import pro_install  # call-time read so tests can patch PLUGIN_DIR

        plugin_dir = Path(pro_install.PLUGIN_DIR)
        if not plugin_dir.is_dir():
            return
        path_str = str(plugin_dir)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
        importlib.invalidate_caches()
    except Exception:  # noqa: BLE001 — discovery plumbing must never break core
        return


def _entry_points():
    """Indirection so tests can substitute fake entry points."""
    return list(entry_points(group=GROUP))


@dataclass
class LoadedPlugin:
    name: str
    version: str
    obj: object | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def discover() -> list[LoadedPlugin]:
    """Load all ``apt.plugins`` entry points, isolating per-plugin failures."""
    _ensure_plugin_dir_on_path()
    out: list[LoadedPlugin] = []
    for ep in _entry_points():
        try:
            obj = ep.load()
            # getattr is inside the guard: a raising name/version property
            # must not break core either.
            name = getattr(obj, "name", None) or ep.name
            version = getattr(obj, "version", None) or "?"
        except Exception as exc:  # noqa: BLE001 — a plugin must never break core
            out.append(LoadedPlugin(name=ep.name, version="?", obj=None, error=str(exc)))
            continue
        out.append(LoadedPlugin(name=name, version=version, obj=obj))
    return out
