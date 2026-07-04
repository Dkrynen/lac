from __future__ import annotations

import os
import subprocess
import sys


def test_cli_survives_a_narrow_console_codepage(tmp_path):
    """Windows' default console codepage (cp1252) can't encode the '✓'
    glyph cli.py prints on every success line (config set, workspace
    create/delete/switch, delete) or the progress-bar block glyphs
    ('█'/'░') during `lac pull`. Force PYTHONIOENCODING=cp1252 (simulating
    an un-reconfigured console) and confirm `lac config set` still exits 0
    and prints its success line -- the crash the audit found happens AFTER
    the action already succeeded, so a raw crash here would be doubly bad."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)

    r = subprocess.run(
        [sys.executable, "-m", "cli", "config", "set", "theme", "dark"],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=15, env=env,
    )
    assert r.returncode == 0
    assert "Set theme = dark" in r.stdout
