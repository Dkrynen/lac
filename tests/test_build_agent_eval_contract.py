from __future__ import annotations

import runpy
from pathlib import Path, PurePosixPath

import PyInstaller.utils.hooks


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "build.spec"
EVALS = ROOT / "evals" / "agent"


def _spec_analysis(monkeypatch):
    captured = {}

    class FakeAnalysis:
        def __init__(self, *args, **kwargs):
            self.pure = []
            self.zipped_data = []
            self.scripts = []
            self.binaries = []
            self.zipfiles = []
            self.datas = kwargs["datas"]
            captured["datas"] = self.datas
            captured["hiddenimports"] = kwargs["hiddenimports"]

    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(
        PyInstaller.utils.hooks,
        "collect_all",
        lambda _package: ([], [], []),
    )
    runpy.run_path(
        str(SPEC),
        init_globals={
            "Analysis": FakeAnalysis,
            "PYZ": lambda *args, **kwargs: object(),
            "EXE": lambda *args, **kwargs: object(),
            "COLLECT": lambda *args, **kwargs: object(),
        },
    )
    return captured


def test_build_spec_packages_agent_eval_tasks_and_command(monkeypatch):
    analysis = _spec_analysis(monkeypatch)
    packaged = {
        (
            Path(source).resolve(),
            PurePosixPath(destination.replace("\\", "/")) / Path(source).name,
        )
        for source, destination in analysis["datas"]
    }
    expected = {
        (
            source.resolve(),
            PurePosixPath(source.relative_to(ROOT).as_posix()),
        )
        for source in EVALS.rglob("*")
        if source.is_file()
    }

    assert expected <= packaged
    assert "backend.agent_eval.command" in analysis["hiddenimports"]
    assert "backend.agent_eval.windows_wfp" in analysis["hiddenimports"]
    assert "backend.agent_eval.windows_job" in analysis["hiddenimports"]


def test_evidence_docs_state_the_bounded_truth():
    combined = (
        (ROOT / "README.md").read_text(encoding="utf-8")
        + "\n"
        + (ROOT / "docs" / "GETTING_STARTED.md").read_text(encoding="utf-8")
    ).lower()

    assert "elevated windows powershell" in combined
    assert "diagnostic artifacts are invalid" in combined
    assert "no downloads occur" in combined
    assert "nine bounded arm runs require operator approval" in combined
    assert "one smoke is not a competitive capability claim" in combined
