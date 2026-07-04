# LAC Plugins

LAC discovers plugins through the `lac.plugins` entry-point group.
(The `lac.tools` group is separate — it belongs to the TUI agent-tool
plugin system in `backend/plugin/`.)

## Writing a plugin
`pyproject.toml`:

    [project.entry-points."lac.plugins"]
    myplugin = "my_pkg.plugin:PLUGIN"

`PLUGIN` is any object with:
- `name: str`, `version: str`
- optional `register_cli(subparsers)` — add argparse subcommands (use `set_defaults(func=...)`)
- optional `register_api(app)` — add Flask routes

Errors in a plugin never break LAC: load and registration are isolated per
plugin (`backend/plugins.py`), and a failure in discovery itself degrades to
a warning. Inspect with `lac plugins` or `GET /api/plugins`.
