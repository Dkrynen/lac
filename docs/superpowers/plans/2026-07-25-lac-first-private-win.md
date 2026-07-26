# LAC First Private Win Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a new local user one truthful command that diagnoses LAC readiness and one safe command that maps a repository into a durable, evidence-backed receipt without running project code or sending data off-device.

**Architecture:** Add a small `backend/first_win` package containing deterministic readiness checks, bounded repository inspection, and atomic receipt persistence. Keep OpenCode behind the existing adapter and keep `cli.py` as a thin presentation layer; `lac inspect <directory>` selects repository inspection while existing non-directory model inspection remains backward compatible.

**Tech Stack:** Python 3.10+, standard library, existing LAC hardware/provider/OpenCode boundaries, argparse, pytest.

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub`.
- Branch: `feat/2026-07-15-lac-terminal-agent-p1`.
- Preserve every pre-existing dirty Phase 0 file.
- Do not commit, push, deploy, publish, purchase, install, download models, create remotes, or access secrets.
- No new runtime dependency.
- Every production behavior change follows red-green-refactor.
- Default repository inspection is read-only and never executes repository code or package scripts.
- Secret-shaped files, dependency trees, VCS internals, LAC data roots, build outputs, and symlinks are excluded from content inspection.
- Repository paths must pass the existing `inspect_project_root` boundary.
- Receipts are written atomically beneath `~/.model-hub/receipts/repository-inspections`; the inspected repository is never modified.
- Readiness checks return structured evidence and remediation; a failed check is reported rather than raised as an unhandled exception.
- OpenCode compatibility remains exactly `1.18.4`.
- Design source: `docs/superpowers/specs/2026-07-25-lac-local-agent-public-readiness-design.md`.

---

### Task 1: Third-party provenance ledger

**Files:**
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `docs/third-party/upstream-components.json`
- Create: `scripts/third_party_audit.py`
- Modify: `build.spec`
- Modify: `installer.iss`
- Test: `tests/test_third_party_audit.py`

**Interfaces:**
- Produces: `audit_ledger(repo_root: Path) -> list[str]`.
- Produces: a versioned JSON ledger whose entries include `name`, `repository`, `commit`, `license`, `treatment`, `source_paths`, `local_paths`, `modifications`, and `owner`.

- [ ] **Step 1: Write a failing audit test**

Create a temporary ledger with a missing commit and assert that `audit_ledger`
returns `["components[0].commit must be a 40-character lowercase SHA-1"]`.
Create a complete entry and assert that it returns no findings.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-first-win-provenance-red" tests/test_third_party_audit.py
```

Expected: import failure because `scripts.third_party_audit` does not exist.

- [ ] **Step 3: Implement the validator and initial ledger**

Implement strict schema validation without network access. Record OpenCode as a
pinned external MIT runtime and Jan as the source-audited Apache-2.0 onboarding
reference. Add further upstream projects only when LAC actually depends on,
adapts, or source-audits them at an exact commit; do not manufacture precision
for general product research. Open WebUI remains reference-only unless a future
import receives commit-level license review.

- [ ] **Step 4: Verify GREEN**

Run the focused test and then:

```powershell
python scripts/third_party_audit.py
```

Both commands must exit zero.

---

### Task 2: Structured `lac doctor`

**Files:**
- Create: `backend/first_win/__init__.py`
- Create: `backend/first_win/doctor.py`
- Create: `tests/test_first_win_doctor.py`

**Interfaces:**
- Produces: immutable `DoctorCheck(name, status, summary, evidence, remediation)`.
- Produces: immutable `DoctorReport(ready, checks)`.
- Produces: `run_doctor(*, project_dir, detect_fn, provider_factory, which_fn, opencode_probe_fn, disk_usage_fn) -> DoctorReport`.
- Status values are exactly `pass`, `warn`, or `fail`.

- [ ] **Step 1: Write failing behavior tests**

Test these independently derived cases:

- healthy hardware, reachable Ollama with one installed agent-capable model,
  supported OpenCode, writable receipt directory, and sufficient disk returns
  `ready=True`;
- missing Ollama is `fail` with a start/install remediation;
- no installed model is `fail` and never downloads one;
- unsupported OpenCode is `fail` and includes supported version `1.18.4`;
- unverified shared iGPU capacity is reported as excluded evidence, not usable
  capacity;
- every external-boundary exception becomes a structured failed check.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-first-win-doctor-red" tests/test_first_win_doctor.py
```

- [ ] **Step 3: Implement the minimal structured checks**

Use the existing hardware detector, Ollama provider, OpenCode resolver/version
probe, and standard-library disk/PATH checks through injectable seams. Do not
make network calls except the configured local Ollama endpoint.

- [ ] **Step 4: Verify GREEN**

Run the focused doctor test file and confirm every check has evidence and an
actionable remediation only when non-passing.

---

### Task 3: Bounded read-only repository inspection

**Files:**
- Create: `backend/first_win/inspect_repo.py`
- Create: `tests/test_first_win_inspect_repo.py`

**Interfaces:**
- Produces: immutable `RepositoryFinding(kind, severity, summary, evidence)`.
- Produces: immutable `RepositoryReceipt(schema_version, receipt_id, created_at, repository, repository_fingerprint, privacy, stack, entry_points, instruction_files, check_candidates, findings, limits)`.
- Produces: `inspect_repository(root, *, now_fn, receipt_root, max_files=5000, max_bytes=2_000_000) -> tuple[RepositoryReceipt, Path]`.

- [ ] **Step 1: Write failing behavior tests**

Use real temporary repositories and assert:

- Python, Node, Rust, and Go manifests produce literal stack and check
  candidates;
- `AGENTS.md`, `CLAUDE.md`, `README.md`, and common entry points are reported;
- `.env`, credentials, tokens, private keys, `.git`, `node_modules`, virtual
  environments, build output, and symlinks are never read or fingerprinted;
- traversal, home/data-root ancestors, and non-directories fail closed through
  the existing project path boundary;
- identical safe repository content produces the same repository fingerprint;
- receipt JSON is written under the receipt root with atomic replace and the
  repository remains byte-for-byte unchanged;
- the file and byte limits stop inspection truthfully and appear in the
  receipt;
- no command runner or network client is invoked.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-first-win-inspect-red" tests/test_first_win_inspect_repo.py
```

- [ ] **Step 3: Implement deterministic inspection**

Walk path-sorted regular files without following symlinks. Hash bounded,
non-secret metadata and selected UTF-8 manifests/instruction files only.
Derive check candidates from literal known manifests; do not execute them.
Write the receipt through a temporary file, flush and fsync it, then use
`os.replace`.

- [ ] **Step 4: Verify GREEN**

Run the focused inspection tests and manually mutate one fixture manifest to
confirm the fingerprint and finding evidence change.

---

### Task 4: CLI wiring and backward compatibility

**Files:**
- Modify: `cli.py`
- Modify: `tests/test_cli_reporting.py`
- Create: `tests/test_cli_first_win.py`

**Interfaces:**
- Produces: `lac doctor [directory] [--json]`.
- Produces: `lac inspect <directory> [--json]` for repositories.
- Preserves: `lac inspect <model>` for non-directory model names.
- Both commands print the receipt path or structured remediation and return a
  process exit code without tracebacks for expected readiness/input failures.

- [ ] **Step 1: Write failing parser and output tests**

Assert the parser exposes `doctor`, directory inspection routes to
`inspect_repository`, model inspection still calls `/api/show`, JSON mode is
valid JSON without ANSI/banner contamination at the command handler boundary,
and failure exits are nonzero with remediation.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-first-win-cli-red" tests/test_cli_first_win.py tests/test_cli_reporting.py
```

- [ ] **Step 3: Add thin handlers**

Keep formatting in `cli.py`; do not duplicate doctor or inspection logic.
Resolve an existing directory before attempting Ollama model inspection.

- [ ] **Step 4: Verify GREEN**

Run the focused CLI tests, `lac doctor --help`, `lac inspect --help`, and a
repository inspection against a disposable fixture.

---

### Task 5: Setup documentation and honest product promise

**Files:**
- Modify: `README.md`
- Create: `docs/GETTING_STARTED.md`

**Interfaces:**
- Documents one golden path: install, `lac doctor`, `lac inspect .`, review the
  receipt, then `lac agent .`.
- Separates verified local behavior from downloads and future functionality.
- States that default inspection discovers checks but does not execute
  repository code.

- [ ] **Step 1: Rewrite the top-level quickstart around the first private win**

Lead with the completed job rather than model management. Keep model commands
as supporting infrastructure.

- [ ] **Step 2: Add failure recovery**

Document missing Ollama, no model, unsupported OpenCode, insufficient disk,
unverified integrated GPU capacity, and where receipts are stored.

- [ ] **Step 3: Check all commands against argparse help**

Run every documented `--help` command and correct any drift.

---

### Task 6: Verification and requirement review

**Files:**
- Modify only when verification exposes a regression.

- [ ] **Step 1: Run focused First Private Win tests**

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-first-win-focused" tests/test_third_party_audit.py tests/test_first_win_doctor.py tests/test_first_win_inspect_repo.py tests/test_cli_first_win.py tests/test_cli_reporting.py
```

- [ ] **Step 2: Run terminal-agent and Phase 0 regression tests**

```powershell
python -m pytest -q -p no:cacheprovider --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-first-win-agent" tests/test_agent_config_writer.py tests/test_agent_launcher.py tests/test_agent_variant.py tests/test_opencode_bin.py tests/test_recommend_agent.py tests/test_hardware_truth.py tests/test_agent_eval_task.py tests/test_agent_eval_scoring.py tests/test_agent_eval_runner.py
```

- [ ] **Step 3: Run the full non-live Python suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m "not live" --basetemp "F:\AI Workspace\Youtube Workspace\.tmp\lac-first-win-full"
```

- [ ] **Step 4: Review the exact diff**

Run `git diff --check`, inspect every changed and untracked source file, and
confirm no `.env`, credentials, generated receipt, model artifact, dependency
tree, or unrelated user file entered the diff.

- [ ] **Step 5: Re-read the approved design**

Map every implemented requirement to evidence. Report Phase 0 and First Private
Win separately; do not claim public readiness, installer parity, signing,
runtime sandbox completion, or model-quality parity.
