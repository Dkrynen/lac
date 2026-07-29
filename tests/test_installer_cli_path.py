from pathlib import Path
import os
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer.iss"
PATH_HARNESS = ROOT / "tests" / "fixtures" / "installer_path_harness.iss"
ISCC = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")


def _script() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_installer_offers_default_selected_lac_path_task():
    script = _script()

    assert "ChangesEnvironment=yes" in script
    assert 'Name: "addtopath"' in script
    task_line = next(
        line for line in script.splitlines() if 'Name: "addtopath"' in line
    )
    assert "checkedonce" not in task_line
    assert "unchecked" not in task_line


def test_installer_path_lifecycle_is_owned_and_segment_safe():
    script = _script()

    assert "procedure ReconcileLacPath" in script
    assert "procedure RemoveLacFromPath" in script
    assert "LacOwnedPath" in script
    assert "RegWriteStringValue" in script
    assert "RegWriteExpandStringValue" in script
    assert "RegValueExists" in script
    assert "function TryWriteMachinePathIfUnchanged" in script
    assert "procedure RemoveLacFromPath(FailClosed: Boolean)" in script
    assert "RemoveLacFromPath(True)" in script
    assert "RemoveLacFromPath(False)" in script
    assert "procedure CurStepChanged" in script
    assert "procedure CurUninstallStepChanged" in script
    assert "CurUninstallStep = usPostUninstall" in script
    assert "uninsdeletevalue" not in script.lower()
    assert (
        "RegDeleteValue(HKEY_LOCAL_MACHINE, MachineEnvironmentKey, 'Path')"
        not in script
    )


def test_installer_packages_an_offline_getting_started_guide():
    script = _script()

    assert 'Source: "docs\\GETTING_STARTED.md"; DestDir: "{app}\\docs"' in script
    assert 'Name: "{group}\\Getting Started"' in script
    assert 'Filename: "{sys}\\notepad.exe"' in script
    assert 'Parameters: """{app}\\docs\\GETTING_STARTED.md"""' in script


@pytest.mark.skipif(
    os.name != "nt" or not ISCC.exists(),
    reason="Inno Setup 6 compiler is unavailable",
)
def test_inno_path_transformations_preserve_unrelated_segments(tmp_path):
    compile_result = subprocess.run(
        [str(ISCC), "/Qp", f"/O{tmp_path}", str(PATH_HARNESS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compile_result.returncode == 0, (
        compile_result.stdout + compile_result.stderr
    )

    harness = tmp_path / "LAC-Path-Contract.exe"
    try:
        run_result = subprocess.run(
            [
                str(harness),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 4551:
            pytest.skip("Windows Application Control blocked unsigned harness")
        raise
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
