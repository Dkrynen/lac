# LAC Public Readiness Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing OpenCode-based local agent fail closed and prevent unverified shared iGPU memory from inflating model-fit recommendations.

**Architecture:** Preserve the existing `scan -> recommend -> variant -> config -> launch` path. Add one explicit verification bit at the hardware boundary and filter unverified integrated GPUs before capacity planning; add a deterministic OpenCode permission policy at config generation, while treating it as defense in depth rather than LAC's final security boundary.

**Tech Stack:** Python 3.10+, dataclasses, JSON, pytest, OpenCode configuration schema, Ollama runtime.

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub`.
- Branch: `feat/2026-07-15-lac-terminal-agent-p1`.
- Local-only: do not push, publish, deploy, purchase, create remotes, or touch secrets.
- Preserve the OpenCode-as-replaceable-runtime boundary.
- No new runtime dependency.
- Every production behavior change follows red-green-refactor.
- Integrated GPU capacity fails closed unless `split_verified=True`.
- OpenCode starts with `* = ask`; project read-only inspection may be allowed, secret-shaped reads are denied, state-changing/network tools ask, external directories and subagents are denied.
- OpenCode compatibility target is exactly `1.18.4`.
- Python verification uses `python -m pytest` with `--basetemp` under the writable workspace when Windows temp ACLs require it.
- Design source: `docs/superpowers/specs/2026-07-25-lac-local-agent-public-readiness-design.md`.
- Scope: this is Phase 0 foundation slice A. Reproducible raw/stock/LAC
  baselines require their own implementation plan and remain a Phase 0 exit
  gate; completing this plan does not complete all of Phase 0.

---

### Task 1: Fail-closed OpenCode configuration

**Files:**
- Modify: `backend/agent_launch/config_writer.py`
- Test: `tests/test_agent_config_writer.py`

**Interfaces:**
- Consumes: `write_opencode_config(project_dir, model, ollama_host) -> Path`.
- Produces: generated `opencode.json` with a deterministic `permission` mapping.

- [ ] **Step 1: Write the failing behavioral test**

```python
def test_write_opencode_config_is_fail_closed(tmp_path):
    out = write_opencode_config(
        tmp_path, "qwen3:8b-agent", "http://localhost:11434"
    )
    permission = json.loads(out.read_text(encoding="utf-8"))["permission"]

    assert permission["*"] == "ask"
    assert permission["edit"] == "ask"
    assert permission["bash"] == "ask"
    assert permission["webfetch"] == "ask"
    assert permission["websearch"] == "ask"
    assert permission["external_directory"] == "deny"
    assert permission["task"] == "deny"
    assert permission["read"]["*"] == "allow"
    assert permission["grep"] == "ask"
    assert permission["read"]["*.env"] == "deny"
    assert permission["read"]["*.env.*"] == "deny"
    assert permission["read"]["*credentials.json"] == "deny"
    assert permission["read"]["*token.json"] == "deny"
    assert permission["read"]["*.pem"] == "deny"
    assert permission["read"]["*.key"] == "deny"
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-config-red" tests/test_agent_config_writer.py::test_write_opencode_config_is_fail_closed
```

Expected: fail with missing key `permission`.

- [ ] **Step 3: Add the minimal deterministic policy**

Add a module-level `_FAIL_CLOSED_PERMISSIONS` mapping and include it as
`"permission": _FAIL_CLOSED_PERMISSIONS` in the generated configuration. Keep
ordinary project reads and local inspection tools allowed; deny secret-shaped
reads, external directories, and task dispatch; require approval for edits,
shell, and network tools.

- [ ] **Step 4: Run the config-writer tests and verify GREEN**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-config-green" tests/test_agent_config_writer.py
```

Expected: all tests pass, including table-driven root and nested secret paths.

- [ ] **Step 5: Review the generated artifact**

Generate config in a pytest temporary project and verify the assertions exercise
the parsed JSON contract rather than source text.

---

### Task 2: Runtime-verified integrated GPU eligibility

**Files:**
- Modify: `backend/cookbook/hardware.py`
- Modify: `backend/api.py`
- Modify: `tests/test_recommend.py`
- Modify: `tests/test_api.py`
- Test: `tests/test_hardware_truth.py`

**Interfaces:**
- Produces: `GPUInfo.split_verified: bool = False`.
- Produces: `build_compute_tiers(...)` omits integrated GPUs unless
  `split_verified=True`.
- Produces: `SystemInfo.combined_vram_gb` sums discrete GPUs and only verified
  integrated GPUs.
- Produces: unknown GPU types contribute no model-fit capacity.
- Produces: API masking and manual VRAM override recompute through
  `_finalize_compute_tiers`.
- Consumes: the existing `_compute_split_plan(...) -> Optional[SplitPlan]`.

- [ ] **Step 1: Write failing hardware-boundary tests**

```python
from backend.cookbook.hardware import (
    GPUInfo,
    SystemInfo,
    _finalize_compute_tiers,
    build_compute_tiers,
)


def test_unverified_integrated_gpu_is_not_a_compute_tier():
    gpus = [
        GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
        GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="vulkan"),
    ]
    tiers = build_compute_tiers(gpus, 30.9)
    assert [tier.kind for tier in tiers] == ["discrete", "ram"]


def test_verified_integrated_gpu_remains_available_for_split():
    gpus = [
        GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
        GPUInfo(
            "AMD Radeon(TM) Graphics",
            10.5,
            backend="vulkan",
            split_verified=True,
        ),
    ]
    tiers = build_compute_tiers(gpus, 30.9)
    assert [tier.kind for tier in tiers] == ["discrete", "integrated", "ram"]


def test_combined_vram_excludes_unverified_shared_memory():
    info = SystemInfo(
        ram_gb=30.9,
        gpus=[
            GPUInfo("AMD Radeon RX 6800 XT", 16.0, backend="vulkan"),
            GPUInfo("AMD Radeon(TM) Graphics", 10.5, backend="vulkan"),
        ],
    )
    _finalize_compute_tiers(info)
    assert info.total_vram_gb == 16.0
    assert info.combined_vram_gb == 16.0
```

- [ ] **Step 2: Run the new file and verify RED**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-hardware-red" tests/test_hardware_truth.py
```

Expected: import or constructor failure because `split_verified` and the
finalization seam do not yet exist.

- [ ] **Step 3: Implement the minimal hardware boundary**

Append `split_verified: bool = False` to `GPUInfo`. Extract the existing final
classification/capacity block from `detect()` into
`_finalize_compute_tiers(info: SystemInfo) -> SystemInfo`. Exclude unverified
integrated GPUs from `build_compute_tiers()` and `combined_vram_gb`. Preserve
their `GPUInfo` entries so diagnostics can still report detected hardware.

- [ ] **Step 4: Verify the new boundary tests GREEN**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-hardware-green" tests/test_hardware_truth.py
```

Expected: all tests pass.

- [ ] **Step 5: Update existing split-planner fixtures**

Set `split_verified=True` only in fixtures whose purpose is to exercise a
previously measured integrated-GPU split. Change the user's default-system
fixture to unverified and assert that a model larger than the discrete GPU uses
RAM offload rather than claiming multi-GPU fit.

- [ ] **Step 6: Run recommendation tests**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-recommend" tests/test_recommend.py tests/test_api.py tests/test_detect_cache.py
```

Expected: all tests pass with unverified integrated memory excluded.

---

### Task 3: OpenCode compatibility pin

**Files:**
- Modify: `backend/agent_launch/opencode_bin.py`
- Test: `tests/test_opencode_bin.py`

**Interfaces:**
- Produces: `SUPPORTED_OPENCODE_VERSION = "1.18.4"`.
- Produces: `resolve_opencode_binary()` rejects missing, unverifiable, and
  untested OpenCode binaries.

- [ ] **Step 1: Write failing resolver tests**

```python
EXPECTED_OPENCODE_VERSION = "1.18.4"


def test_rejects_untested_opencode_version(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which",
        lambda name: r"C:\tools\opencode.exe",
    )
    monkeypatch.setattr(
        opencode_bin, "_probe_version", lambda binary: "1.19.0"
    )
    with pytest.raises(RuntimeError) as exc:
        resolve_opencode_binary()
    assert "1.19.0" in str(exc.value)
    assert EXPECTED_OPENCODE_VERSION in str(exc.value)


def test_rejects_opencode_when_version_probe_fails(monkeypatch):
    monkeypatch.setattr(
        "backend.agent_launch.opencode_bin.shutil.which",
        lambda name: r"C:\tools\opencode.exe",
    )
    monkeypatch.setattr(
        opencode_bin,
        "_probe_version",
        lambda binary: (_ for _ in ()).throw(RuntimeError("probe failed")),
    )
    with pytest.raises(RuntimeError, match="probe failed"):
        resolve_opencode_binary()
```

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-opencode-pin-red" tests/test_opencode_bin.py
```

- [ ] **Step 3: Implement the exact compatibility check**

Probe `<binary> --version`, extract a semantic version, and reject anything
other than `1.18.4` with installed and supported versions in the error.

- [ ] **Step 4: Verify GREEN and run the live resolver**

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-opencode-pin-green" tests/test_opencode_bin.py
```

---

### Task 4: Phase 0 slice A verification and compatibility evidence

**Files:**
- Modify only if verification exposes a regression.

**Interfaces:**
- Consumes: generated OpenCode config and hardware/recommendation contracts from
  Tasks 1 and 2.
- Produces: fresh test evidence and an exact remaining-gaps report.

- [ ] **Step 1: Run the focused terminal-agent suite**

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-agent" tests/test_agent_config_writer.py tests/test_agent_launcher.py tests/test_agent_variant.py tests/test_opencode_bin.py tests/test_recommend_agent.py tests/test_hardware_truth.py
```

- [ ] **Step 2: Run the full Python suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-phase0-full"
```

- [ ] **Step 3: Inspect the exact diff**

```powershell
git status --short
git diff --check
git diff -- backend/agent_launch/config_writer.py backend/cookbook/hardware.py backend/cookbook/recommend.py tests/test_agent_config_writer.py tests/test_hardware_truth.py tests/test_recommend.py docs/superpowers/specs/2026-07-25-lac-local-agent-public-readiness-design.md docs/superpowers/plans/2026-07-25-lac-public-readiness-phase0.md
```

- [ ] **Step 4: Record a local checkpoint**

After the full suite and diff review are clean, create a local commit containing
only the Phase 0 files. Do not push or deploy.
