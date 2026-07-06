from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_spec_uses_onedir_collect():
    """One-file re-extracts its whole bundle to a temp dir on every launch
    (~4.5s). One-dir (EXE with exclude_binaries=True + COLLECT) ships the exe
    next to its deps in a folder, cutting steady-state launch to ~1.5s with
    zero code change. Guard against silently reverting to one-file.
    """
    text = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert "COLLECT(" in text
    assert "exclude_binaries=True" in text


def test_build_spec_drops_lac_console_debug_exe():
    """lac-console was a one-file debug variant; a second EXE stanza
    complicates the EXE->COLLECT one-dir conversion and isn't needed."""
    text = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert "lac-console" not in text
    assert "exe_debug" not in text
