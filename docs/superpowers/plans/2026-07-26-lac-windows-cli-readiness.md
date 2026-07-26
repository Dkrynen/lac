# LAC Windows CLI Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fresh Windows installer expose the packaged `lac.exe` as the `lac` command, cleanly reverse only that PATH change on uninstall, and guide a new user to the verified `doctor -> inspect -> agent` path.

**Architecture:** Keep the existing one-directory PyInstaller payload and Inno Setup installer. Add a default-selected installer task whose Pascal lifecycle code deduplicates the exact `{app}` directory in the machine PATH, records ownership in LAC's private registry key, and removes only that owned entry during uninstall. Package the existing Getting Started guide and make the Windows install flow explicit in public documentation.

**Tech Stack:** Inno Setup 6 Pascal Script, PyInstaller, Python/pytest, Markdown.

## Global Constraints

- Preserve every unrelated dirty change on `feat/2026-07-15-lac-terminal-agent-p1`.
- Do not install, uninstall, publish, push, deploy, commit, or modify the live registry.
- PATH editing must be idempotent and must never delete or rewrite unrelated entries.
- The installer remains the same signed `lac.exe` payload; no second CLI binary or hand-built editor is introduced.
- Setup remains explicit: no automatic model download, OpenCode install, Ollama start, or repository execution.
- Public documentation may claim only behavior backed by source checks, compilation, and eventual clean-machine install evidence.

---

### Task 1: Fail-closed installer PATH contract

**Files:**
- Create: `tests/test_installer_cli_path.py`
- Modify: `installer.iss`

**Interfaces:**
- Consumes: the existing `{app}\lac.exe` PyInstaller payload and the Inno Setup `addtopath` task.
- Produces: idempotent exact-segment PATH addition plus marker-owned exact-segment removal.

- [ ] **Step 1: Write the failing packaging-contract tests**

Add tests that parse `installer.iss` and prove the user-visible safety contract:

```python
def test_installer_offers_default_selected_lac_path_task():
    script = INSTALLER.read_text(encoding="utf-8")
    assert "ChangesEnvironment=yes" in script
    assert 'Name: "addtopath"' in script
    assert 'Flags: checkedonce' in script


def test_installer_path_lifecycle_is_owned_and_segment_safe():
    script = INSTALLER.read_text(encoding="utf-8")
    assert "AddLacToPath" in script
    assert "RemoveLacFromPath" in script
    assert "LacPathInstalled" in script
    assert "CurStepChanged" in script
    assert "CurUninstallStepChanged" in script
    assert "uninsdeletevalue" not in script.lower()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-installer-path-red" tests/test_installer_cli_path.py
```

Expected: FAIL because the installer has no `ChangesEnvironment`, `addtopath`, or owned PATH lifecycle.

- [ ] **Step 3: Implement the minimal installer behavior**

In `installer.iss`:

- set `ChangesEnvironment=yes`;
- add a default-selected `addtopath` task;
- split PATH on semicolons and compare normalized entries case-insensitively;
- preserve every unrelated entry byte-for-byte;
- append `{app}` only when absent;
- create `HKLM\Software\LAC\LacPathInstalled=1` only when LAC owns the addition;
- if the task is deselected during an upgrade, remove a previously owned entry;
- on uninstall, remove the exact owned entry and delete only LAC's marker.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same focused pytest command and expect PASS.

- [ ] **Step 5: Compile-check the Inno script without installing**

Compile the installer only after confirming the command can target a disposable output without replacing the existing `dist` release artifact. If that isolation is not available, record compilation as an explicit manual release gate instead of overwriting a held artifact.

### Task 2: Ship the setup guide and correct the Windows golden path

**Files:**
- Modify: `installer.iss`
- Modify: `docs/GETTING_STARTED.md`
- Modify: `README.md`
- Test: `tests/test_installer_cli_path.py`

**Interfaces:**
- Consumes: `docs/GETTING_STARTED.md` and the installed `lac` command.
- Produces: an installed offline guide and exact first-terminal instructions.

- [ ] **Step 1: Extend the failing installer test**

Assert that `docs\GETTING_STARTED.md` is copied to `{app}\docs` and exposed as a Start Menu shortcut. The production change that must fail this test is dropping the offline guide from the installer payload.

- [ ] **Step 2: Verify RED**

Run the focused test and confirm it fails only because the guide is not packaged.

- [ ] **Step 3: Package and link the guide**

Add the guide to `[Files]` and a `Getting Started` entry to `[Icons]`. Update the README and guide so Windows users:

1. run the installer with `Add LAC to PATH` selected;
2. open a new terminal after setup;
3. enter their repository;
4. run `lac doctor .`;
5. continue with `lac inspect .` and `lac agent .` only when the doctor is ready.

- [ ] **Step 4: Verify GREEN**

Run `tests/test_installer_cli_path.py` and the existing First Private Win test group.

### Task 3: Public-readiness verification

**Files:**
- Verify only; no new production files.

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: bounded evidence and an honest remaining clean-machine gate.

- [ ] **Step 1: Run focused tests**

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-windows-cli-focused" tests/test_installer_cli_path.py tests/test_installer_no_ollama_check.py tests/test_third_party_audit.py tests/test_first_win_doctor.py tests/test_first_win_inspect_repo.py tests/test_cli_first_win.py
```

- [ ] **Step 2: Run the full non-live suite**

Run the repository's established full non-live pytest command with cache disabled and scratch under the writable workspace.

- [ ] **Step 3: Run static release checks**

Run `git diff --check`, the third-party audit, and the release-readiness tests. Review the complete diff without altering unrelated dirty files.

- [ ] **Step 4: Record the remaining runtime gate**

Do not call the installer public-ready from source tests alone. The final gate remains a human-approved clean Windows install/uninstall smoke proving:

- a newly opened terminal resolves `lac`;
- `lac doctor .` runs from a real repository;
- reinstall does not duplicate PATH;
- uninstall preserves every unrelated PATH entry and removes LAC's owned entry.
