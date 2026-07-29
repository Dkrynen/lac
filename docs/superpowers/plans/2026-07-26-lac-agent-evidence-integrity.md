# LAC Agent Evidence Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing raw Ollama, stock OpenCode, and LAC OpenCode
evaluation runner into a fail-closed Windows evidence pipeline whose verified
artifacts are identity-bound, contained, bounded, counterbalanced, and
hash-sealed.

**Architecture:** The runner remains the orchestrator while focused modules own
evidence verdicts, atomic ledgers, identity capture, fixture sealing, bounded
I/O, Windows Job Objects, Windows Filtering Platform policy, and deterministic
scheduling. `verified` mode is the default and never downgrades;
`diagnostic` mode is explicit and can never produce `artifact_valid: true`.

**Tech Stack:** Python 3.11+ standard library, `ctypes` Win32 bindings, Ollama
loopback HTTP APIs, OpenCode `1.18.4`, pytest, PyInstaller one-directory build,
Windows 11 Job Objects, and Windows Filtering Platform ALE filters.

## Global Constraints

- Work only in `C:\Users\User\repos\model-hub` on
  `feat/2026-07-15-lac-terminal-agent-p1`.
- Preserve `.serena/` untracked and never stage it.
- Do not modify `.env`, credentials, tokens, `lac-pro`, or `lac-cloud`.
- Do not download, create, rebuild, pull, or retag any model.
- Do not copy proprietary Claude Code, Codex, or other closed-source code.
- Reuse only documented behavior or license-compatible public source, and add
  provenance before incorporating third-party implementation code.
- OpenCode must remain pinned to exactly `1.18.4`.
- Verified Windows containment requires elevation; never auto-elevate.
- Never silently downgrade `verified` to `diagnostic`.
- A diagnostic artifact must always contain `"artifact_valid": false`.
- No live nine-arm evaluation until dry-run proves every control ready and the
  operator approves the displayed maximum runtime.
- No tag, release, installer publication, or deployment in this plan.
- Every production change follows RED -> minimal GREEN -> focused suite -> full
  relevant suite -> review -> commit.
- Each task owns only its listed files. Do not combine task commits.

## File Structure

### New production modules

- `backend/agent_eval/evidence.py` — structured control results and the only
  `artifact_valid` derivation.
- `backend/agent_eval/ledger.py` — atomic writes, file hashing, artifact
  enumeration, and final evidence sealing.
- `backend/agent_eval/capture.py` — bounded HTTP and subprocess capture.
- `backend/agent_eval/identity.py` — runtime, config, and Ollama model identity.
- `backend/agent_eval/fixture.py` — fixture manifests, exclusive
  materialization, read-only marking, and post-run verification.
- `backend/agent_eval/windows_job.py` — Windows Job Object lifecycle and
  contained root-process execution.
- `backend/agent_eval/containment.py` — platform-neutral containment protocol,
  diagnostic provider, and provider selection.
- `backend/agent_eval/windows_wfp.py` — dynamic WFP session and exact
  loopback/Ollama-port policy.
- `backend/agent_eval/schedule.py` — schema-v2 generation settings, derived
  seeds, and Latin-square arm schedule.
- `backend/agent_eval/command.py` — reusable application service shared by the
  source script and packaged CLI.

### New tests

- `tests/test_agent_eval_evidence.py`
- `tests/test_agent_eval_ledger.py`
- `tests/test_agent_eval_capture.py`
- `tests/test_agent_eval_identity.py`
- `tests/test_agent_eval_fixture.py`
- `tests/test_agent_eval_windows_job.py`
- `tests/test_agent_eval_windows_wfp.py`
- `tests/test_agent_eval_schedule.py`
- `tests/test_agent_eval_command.py`
- `tests/test_agent_eval_live_containment.py`
- `tests/test_cli_agent_eval.py`
- `tests/test_build_agent_eval_contract.py`

---

### Task 1: Evidence Verdict, Atomic Ledger, and Explicit Modes

**Files:**

- Create: `backend/agent_eval/evidence.py`
- Create: `backend/agent_eval/ledger.py`
- Create: `tests/test_agent_eval_evidence.py`
- Create: `tests/test_agent_eval_ledger.py`
- Modify: `backend/agent_eval/runner.py`
- Modify: `scripts/agent_eval.py`
- Modify: `tests/test_agent_eval_runner.py`
- Modify: `tests/test_agent_eval_script.py`

**Interfaces:**

- Produces:
  - `EvidenceMode(str, Enum)` with `VERIFIED` and `DIAGNOSTIC`.
  - `EvidenceState(str, Enum)` with `PASS`, `FAIL`, and `UNSUPPORTED`.
  - `EvidenceControlResult(name, state, reason, details)`.
  - `EvidenceVerdict.from_results(mode, results)`.
  - `EvidenceVerdict.artifact_valid: bool`.
  - `atomic_write_bytes(path, payload)`.
  - `atomic_write_json(path, value)`.
  - `seal_evidence(run_root, mode, preliminary_results,
    excluded_roots=("workspaces",))`.
- Consumes: existing `EvaluationPlan`, `ArmResult`, and result/scoring artifacts.
- Later tasks add named results to the verdict; no later task writes
  `artifact_valid` directly.

- [ ] **Step 1: Write failing verdict tests**

```python
# tests/test_agent_eval_evidence.py
import pytest

from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
    EvidenceVerdict,
    REQUIRED_CONTROLS,
)


def passed(name: str) -> EvidenceControlResult:
    return EvidenceControlResult(name, EvidenceState.PASS, "verified", {})


def test_verified_verdict_requires_every_named_control():
    results = [passed(name) for name in REQUIRED_CONTROLS]
    verdict = EvidenceVerdict.from_results(EvidenceMode.VERIFIED, results)
    assert verdict.artifact_valid is True
    assert verdict.missing == ()


def test_verified_verdict_fails_closed_for_missing_duplicate_or_failed_control():
    results = [passed(name) for name in REQUIRED_CONTROLS[:-1]]
    verdict = EvidenceVerdict.from_results(EvidenceMode.VERIFIED, results)
    assert verdict.artifact_valid is False
    assert verdict.missing == (REQUIRED_CONTROLS[-1],)

    with pytest.raises(ValueError, match="duplicate evidence control"):
        EvidenceVerdict.from_results(
            EvidenceMode.VERIFIED, [passed(REQUIRED_CONTROLS[0])] * 2
        )


def test_diagnostic_mode_can_never_be_valid():
    results = [passed(name) for name in REQUIRED_CONTROLS]
    verdict = EvidenceVerdict.from_results(EvidenceMode.DIAGNOSTIC, results)
    assert verdict.artifact_valid is False
    assert verdict.mode is EvidenceMode.DIAGNOSTIC
```

`REQUIRED_CONTROLS` must contain exactly:

```python
(
    "runtime_dependency_provenance",
    "os_loopback_only_egress",
    "immutable_ollama_model_lineage",
    "sealed_fixture_materialization",
    "windows_process_tree_containment",
    "bounded_process_and_http_capture",
    "counterbalanced_deterministic_sampling",
    "artifact_ledger_integrity",
)
```

- [ ] **Step 2: Write failing atomic-ledger tests**

```python
# tests/test_agent_eval_ledger.py
import json

import pytest

from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
)
from backend.agent_eval.ledger import (
    ArtifactLedgerError,
    atomic_write_json,
    seal_evidence,
    verify_evidence,
)


def test_atomic_write_refuses_existing_destination(tmp_path):
    target = tmp_path / "result.json"
    atomic_write_json(target, {"first": True})
    with pytest.raises(FileExistsError):
        atomic_write_json(target, {"second": True})


def test_seal_hashes_every_non_workspace_artifact_and_detects_tampering(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    atomic_write_json(run / "manifest.json", {"schema_version": 2})
    (run / "workspaces").mkdir()
    (run / "workspaces" / "fixture.py").write_text("ignored workspace")

    evidence = seal_evidence(
        run,
        EvidenceMode.DIAGNOSTIC,
        preliminary_results=[],
    )
    assert evidence["artifact_valid"] is False
    assert "manifest.json" in evidence["artifacts"]
    assert "workspaces/fixture.py" not in evidence["artifacts"]
    assert evidence["controls"]["results"][-1]["name"] == "artifact_ledger_integrity"
    assert evidence["controls"]["results"][-1]["state"] == "pass"
    assert verify_evidence(run).ok is True

    (run / "manifest.json").write_text('{"tampered": true}')
    assert verify_evidence(run).ok is False


def test_seal_refuses_unknown_temporary_or_partial_files(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / ".manifest.json.tmp").write_text("partial")
    with pytest.raises(ArtifactLedgerError, match="temporary artifact"):
        seal_evidence(
            run,
            EvidenceMode.DIAGNOSTIC,
            preliminary_results=[],
        )


def test_caller_cannot_preclaim_ledger_integrity(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    preclaimed = EvidenceControlResult(
        "artifact_ledger_integrity",
        EvidenceState.PASS,
        "not measured",
        {},
    )
    with pytest.raises(ArtifactLedgerError, match="computed only while sealing"):
        seal_evidence(
            run,
            EvidenceMode.VERIFIED,
            preliminary_results=[preclaimed],
        )
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_evidence.py `
  tests/test_agent_eval_ledger.py -q
```

Expected: collection fails because `evidence.py` and `ledger.py` do not exist.

- [ ] **Step 4: Implement the evidence contract**

```python
# backend/agent_eval/evidence.py
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable


class EvidenceMode(str, Enum):
    VERIFIED = "verified"
    DIAGNOSTIC = "diagnostic"


class EvidenceState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNSUPPORTED = "unsupported"


REQUIRED_CONTROLS = (
    "runtime_dependency_provenance",
    "os_loopback_only_egress",
    "immutable_ollama_model_lineage",
    "sealed_fixture_materialization",
    "windows_process_tree_containment",
    "bounded_process_and_http_capture",
    "counterbalanced_deterministic_sampling",
    "artifact_ledger_integrity",
)


@dataclass(frozen=True)
class EvidenceControlResult:
    name: str
    state: EvidenceState
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceVerdict:
    mode: EvidenceMode
    results: tuple[EvidenceControlResult, ...]
    missing: tuple[str, ...]
    artifact_valid: bool

    @classmethod
    def from_results(
        cls,
        mode: EvidenceMode,
        results: Iterable[EvidenceControlResult],
    ) -> "EvidenceVerdict":
        items = tuple(results)
        names = [item.name for item in items]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                "duplicate evidence control: " + ", ".join(duplicates)
            )
        unknown = sorted(set(names) - set(REQUIRED_CONTROLS))
        if unknown:
            raise ValueError("unknown evidence control: " + ", ".join(unknown))
        missing = tuple(name for name in REQUIRED_CONTROLS if name not in names)
        all_pass = not missing and all(
            item.state is EvidenceState.PASS for item in items
        )
        return cls(
            mode=mode,
            results=items,
            missing=missing,
            artifact_valid=mode is EvidenceMode.VERIFIED and all_pass,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "results": [asdict(item) for item in self.results],
            "missing": list(self.missing),
            "artifact_valid": self.artifact_valid,
        }
```

- [ ] **Step 5: Implement atomic writes and sealing**

`ledger.py` must:

1. create a temporary sibling with `os.open(..., O_CREAT | O_EXCL | O_WRONLY)`;
2. write bytes, flush, and call `os.fsync`;
3. atomically promote with `os.replace`;
4. refuse an existing final destination before writing;
5. normalize ledger paths with `/`;
6. SHA-256 every regular artifact except `evidence.json` and declared excluded
   roots;
7. reject links, reparse points, non-regular files, and `*.tmp`;
8. derive `evidence_root_sha256` from compact JSON of sorted path/hash pairs;
9. write `evidence.json` last;
10. provide `verify_evidence(run_root) -> LedgerVerification`.

The sealing function receives mode plus the seven preliminary control results,
not a boolean or precomputed verdict. It rejects a caller-supplied
`artifact_ledger_integrity` result, hashes and validates the artifact set,
appends that pass result itself, and only then derives the final verdict:

```python
def seal_evidence(
    run_root: Path,
    mode: EvidenceMode,
    preliminary_results: Iterable[EvidenceControlResult],
    *,
    excluded_roots: tuple[str, ...] = ("workspaces",),
) -> dict[str, Any]:
    ...
    items = tuple(preliminary_results)
    if any(
        item.name == "artifact_ledger_integrity"
        for item in items
    ):
        raise ArtifactLedgerError(
            "artifact_ledger_integrity is computed only while sealing"
        )
    ledger_result = EvidenceControlResult(
        "artifact_ledger_integrity",
        EvidenceState.PASS,
        "all declared artifacts hashed",
        {"artifact_count": len(artifact_hashes)},
    )
    verdict = EvidenceVerdict.from_results(
        mode,
        [*items, ledger_result],
    )
    evidence = {
        "schema_version": 2,
        "mode": verdict.mode.value,
        "controls": verdict.to_dict(),
        "artifacts": artifact_hashes,
        "evidence_root_sha256": root_hash,
        "artifact_valid": verdict.artifact_valid,
    }
    atomic_write_json(run_root / "evidence.json", evidence)
    return evidence
```

- [ ] **Step 6: Add explicit mode parsing and preserve truthful invalidity**

Add to `scripts/agent_eval.py`:

```python
parser.add_argument(
    "--mode",
    choices=("verified", "diagnostic"),
    default="verified",
    help="verified fails closed; diagnostic artifacts are always invalid",
)
```

Update dry-run output to include `mode`, structured control results, and
`artifact_valid: false`. Remove the hard-coded ability for `runner.py` to set
validity. Until later tasks implement controls, verified runs must stop before
generation with exit code `2` and a list of missing controls. Diagnostic runs
may execute but seal an invalid ledger.

- [ ] **Step 7: Run focused tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_evidence.py `
  tests/test_agent_eval_ledger.py `
  tests/test_agent_eval_runner.py `
  tests/test_agent_eval_script.py -q
```

Expected: all pass; existing runner artifact tests now assert
`evidence.json["artifact_valid"] is False`.

- [ ] **Step 8: Review and commit Task 1**

```powershell
git diff --check
git status --short
git add -- `
  backend/agent_eval/evidence.py `
  backend/agent_eval/ledger.py `
  backend/agent_eval/runner.py `
  scripts/agent_eval.py `
  tests/test_agent_eval_evidence.py `
  tests/test_agent_eval_ledger.py `
  tests/test_agent_eval_runner.py `
  tests/test_agent_eval_script.py
git commit -m "feat(eval): add fail-closed evidence verdict"
```

---

### Task 2: Bounded Identity Capture and Runtime/Model Provenance

**Files:**

- Create: `backend/agent_eval/capture.py`
- Create: `backend/agent_eval/identity.py`
- Create: `tests/test_agent_eval_capture.py`
- Create: `tests/test_agent_eval_identity.py`
- Modify: `backend/agent_eval/runner.py`
- Modify: `backend/agent_launch/config_writer.py`
- Modify: `tests/test_agent_config_writer.py`
- Modify: `tests/test_agent_eval_runner.py`

**Interfaces:**

- Produces:
  - `CaptureLimitExceeded`.
  - `bounded_http_json(url, *, method, body, timeout, max_bytes)`.
  - `FileIdentity(path, size, sha256, version, authenticode)`.
  - `ModelIdentity(name, digest, size, details, show_sha256, parent_model,
    from_blob_sha256, parameters)`.
  - `EvaluationIdentitySnapshot`.
  - `capture_preflight_identities(plan)`.
  - `compare_identity_payloads(before, after)`.
  - `compare_postflight_identities(before, after)`.
- Consumes: Task 1 evidence results and atomic JSON writer.
- Task 4 extends `capture.py` with subprocess streaming; it must reuse
  `CaptureLimitExceeded`.

- [ ] **Step 1: Write failing bounded-HTTP tests**

```python
# tests/test_agent_eval_capture.py
import io

import pytest

from backend.agent_eval.capture import CaptureLimitExceeded, bounded_http_json


class Response:
    def __init__(self, payload: bytes):
        self.payload = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size=-1):
        return self.payload.read(size)


def test_bounded_http_json_reads_at_most_limit_plus_one():
    calls = []

    def open_fn(request, timeout):
        calls.append((request.full_url, timeout))
        return Response(b'{"ok":true}')

    assert bounded_http_json(
        "http://127.0.0.1:11434/api/version",
        method="GET",
        body=None,
        timeout=5,
        max_bytes=64,
        open_fn=open_fn,
    ) == {"ok": True}
    assert calls == [("http://127.0.0.1:11434/api/version", 5)]


def test_bounded_http_json_fails_on_overflow_or_non_object():
    with pytest.raises(CaptureLimitExceeded):
        bounded_http_json(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
            body=None,
            timeout=5,
            max_bytes=4,
            open_fn=lambda *_args, **_kwargs: Response(b'{"models":[]}'),
        )
```

Also test malformed UTF-8, malformed JSON, non-object JSON, non-loopback URL,
authenticated URL, query/fragment, and a body larger than 2 MiB.

- [ ] **Step 2: Write failing identity tests**

```python
# tests/test_agent_eval_identity.py
from pathlib import Path

import pytest

from backend.agent_eval.identity import (
    IdentityError,
    capture_model_identities,
    compare_postflight_identities,
    file_identity,
)


def test_file_identity_hashes_exact_executable_bytes(tmp_path):
    binary = tmp_path / "opencode.exe"
    binary.write_bytes(b"opencode-1.18.4")
    identity = file_identity(binary, version="1.18.4")
    assert identity.path == binary.resolve()
    assert identity.size == len(b"opencode-1.18.4")
    assert len(identity.sha256) == 64


def test_model_identity_requires_full_digest_and_exact_variant_parent():
    responses = {
        "/api/tags": {
            "models": [
                {
                    "name": "gpt-oss:20b",
                    "digest": "a" * 64,
                    "size": 13,
                    "details": {"family": "gptoss"},
                },
                {
                    "name": "gpt-oss:20b-agent",
                    "digest": "b" * 64,
                    "size": 14,
                    "details": {
                        "family": "gptoss",
                        "parent_model": "gpt-oss:20b",
                    },
                },
            ]
        },
        "gpt-oss:20b": {
            "details": {"parent_model": ""},
            "modelfile": "FROM sha256:" + "c" * 64,
            "parameters": "temperature 1",
            "template": "base",
            "model_info": {},
            "capabilities": ["completion", "tools"],
        },
        "gpt-oss:20b-agent": {
            "details": {"parent_model": "gpt-oss:20b"},
            "modelfile": "FROM C:\\\\models\\\\sha256-" + "c" * 64,
            "parameters": "num_ctx 131072\\ntemperature 1",
            "template": "agent",
            "model_info": {},
            "capabilities": ["completion", "tools"],
        },
    }
    identities = capture_model_identities(
        "gpt-oss:20b",
        "gpt-oss:20b-agent",
        fetch_fn=lambda key: responses[key],
    )
    assert identities.lac.parent_model == "gpt-oss:20b"
    assert identities.lac.from_blob_sha256 == "c" * 64


def test_postflight_model_or_binary_drift_fails():
    before = {
        "runtime": {"opencode": {"sha256": "a" * 64}},
        "models": {"base": {"digest": "b" * 64}},
    }
    after = {
        "runtime": {"opencode": {"sha256": "c" * 64}},
        "models": {"base": {"digest": "b" * 64}},
    }
    result = compare_identity_payloads(before, after)
    assert result.state.value == "fail"
    assert "opencode" in result.reason
```

Add cases for missing tag, duplicate normalized tag, short digest, wrong parent,
missing `FROM` digest, show-hash drift, Ollama version drift, config drift,
OpenCode version other than `1.18.4`, executable link/reparse point, and file
replacement between stat and hash.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_identity.py -q
```

Expected: import failures for the new modules.

- [ ] **Step 4: Implement bounded JSON capture**

`bounded_http_json` must validate loopback before opening, serialize request JSON
with compact sorted encoding, read `max_bytes + 1`, raise on overflow, and
return only a JSON object. Constants:

```python
IDENTITY_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
OLLAMA_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
```

The function must accept `open_fn` for tests and must never call `.read()` with
an unbounded size.

- [ ] **Step 5: Implement identity capture**

Use immutable dataclasses and canonical JSON hashing:

```python
def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

Model identity must:

- obtain the full digest from `/api/tags`;
- POST `{"model": name, "verbose": false}` to `/api/show`;
- hash only `modelfile`, `parameters`, `template`, `details`, `model_info`, and
  `capabilities`;
- parse `FROM` with
  `r"(?im)^FROM\\s+.*(?:sha256[:-])([0-9a-f]{64})\\s*$"`;
- require `lac.details.parent_model == base_name`;
- capture both identities again postflight.

Runtime identity must hash:

- resolved OpenCode executable and wrapper when present;
- generated `opencode.json`;
- Ollama executable;
- LAC executable or current repository-owned interpreter;
- package metadata adjacent to OpenCode when present.

Authenticode is recorded as `valid`, `invalid`, `unsigned`, or `unavailable`;
unsigned status is truthful metadata, not an automatic Phase 0 failure.
`compare_postflight_identities` converts both immutable snapshots with
`to_dict()` and delegates to the pure `compare_identity_payloads` function used
by the drift test.

- [ ] **Step 6: Harden generated evaluation config**

Extend `build_opencode_config` with an `evaluation: bool = False` keyword. When
true, emit:

```python
{
    "autoupdate": False,
    "share": "disabled",
    "enabled_providers": ["ollama"],
    "plugin": [],
    "mcp": {},
    "instructions": [],
    "formatter": False,
    "snapshot": False,
    # existing exact Ollama provider/model, permissions, and tools
}
```

Omit `$schema` in evaluation mode so runtime does not need the remote schema.
`write_opencode_config_file` passes `evaluation=True`; normal product config
keeps existing behavior.

- [ ] **Step 7: Persist and verify identities in the runner**

Before generation, write:

```text
identities/lac.json
identities/ollama.json
identities/opencode.json
identities/models.json
```

After the final arm, capture again and produce two evidence results:

- `runtime_dependency_provenance`
- `immutable_ollama_model_lineage`

Any drift makes both the comparison and final evidence invalid.

- [ ] **Step 8: Run focused and regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_identity.py `
  tests/test_agent_config_writer.py `
  tests/test_agent_eval_runner.py -q
```

Expected: all pass.

- [ ] **Step 9: Review and commit Task 2**

```powershell
git diff --check
git add -- `
  backend/agent_eval/capture.py `
  backend/agent_eval/identity.py `
  backend/agent_eval/runner.py `
  backend/agent_launch/config_writer.py `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_identity.py `
  tests/test_agent_config_writer.py `
  tests/test_agent_eval_runner.py
git commit -m "feat(eval): bind evidence to runtime identities"
```

---

### Task 3: Sealed Fixture Materialization

**Files:**

- Create: `backend/agent_eval/fixture.py`
- Create: `tests/test_agent_eval_fixture.py`
- Modify: `backend/agent_eval/task.py`
- Modify: `backend/agent_eval/runner.py`
- Modify: `tests/test_agent_eval_task.py`
- Modify: `tests/test_agent_eval_runner.py`

**Interfaces:**

- Produces:
  - `FixtureEntry(path, size, sha256)`.
  - `FixtureManifest(entries, aggregate_sha256, task_contract_sha256)`.
  - `build_fixture_manifest(task)`.
  - `materialize_fixture(manifest, source_root, destination)`.
  - `verify_materialized_fixture(manifest, destination)`.
  - `mark_fixture_read_only(destination)`.
- Consumes: Task 1 atomic writer and Task 2 canonical hash helper.

- [ ] **Step 1: Write failing fixture tests**

```python
# tests/test_agent_eval_fixture.py
import os

import pytest

from backend.agent_eval.fixture import (
    FixtureSealError,
    build_fixture_manifest,
    materialize_fixture,
    verify_materialized_fixture,
)


def test_materialized_fixture_matches_file_and_aggregate_hashes(task):
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    materialize_fixture(manifest, task.fixture_root, destination)
    verification = verify_materialized_fixture(manifest, destination)
    assert verification.ok is True
    assert verification.aggregate_sha256 == manifest.aggregate_sha256


def test_verification_detects_mutation_addition_and_deletion(task):
    manifest = build_fixture_manifest(task)
    destination = task.fixture_root.parent / "materialized"
    materialize_fixture(manifest, task.fixture_root, destination)

    target = next(destination.rglob("*.py"))
    target.chmod(0o666)
    target.write_text("mutated")
    assert verify_materialized_fixture(manifest, destination).ok is False


def test_materialization_refuses_existing_destination(task):
    destination = task.fixture_root.parent / "materialized"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        materialize_fixture(
            build_fixture_manifest(task), task.fixture_root, destination
        )
```

Add Windows-specific tests for directory/file reparse points, alternate data
stream syntax (`name.py:stream`), case-colliding paths, unexpected file types,
source mutation between manifest and copy, destination mutation, and failed
read-only marking. Use the existing symlink-skip pattern when the OS denies
test link creation.

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_fixture.py `
  tests/test_agent_eval_task.py -q
```

Expected: missing module/import failures.

- [ ] **Step 3: Implement normalized file manifests**

Use the same traversal and size limits as `snapshot_fixture`. Reject:

- absolute or `..` paths;
- `:` in any Windows relative component;
- case-fold duplicate paths;
- link/reparse entries;
- non-regular files;
- sensitive paths;
- per-file or aggregate limit overflow.

Aggregate hash input is compact sorted JSON of:

```json
[
  {"path": "stats_service.py", "sha256": "...", "size": 123}
]
```

`task_contract_sha256` includes schema version, ID, prompt, scorer expected
hash, timeout, trials, and generation fields when schema v2 arrives in Task 7.

- [ ] **Step 4: Implement exclusive copy and post-run verification**

Open source files with non-follow semantics where available, compare
`fstat` identity before and after reading, write destination files with
exclusive creation, flush and `fsync`, then rehash the full destination.

On Windows, `mark_fixture_read_only` must:

- set `FILE_ATTRIBUTE_READONLY` on files;
- remove inherited write access using the narrowest repository-owned helper;
- record whether ACL hardening succeeded;
- never claim a filesystem sandbox.

If ACL hardening cannot be verified, return a failed seal control rather than
raising after generation.

- [ ] **Step 5: Replace `shutil.copytree` in the runner**

Every trial/arm receives a unique path. Write
`fixture-manifest.before.json` before execution and
`fixture-manifest.after.json` after execution. The
`sealed_fixture_materialization` control passes only when all before/after
manifests match.

- [ ] **Step 6: Run focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_fixture.py `
  tests/test_agent_eval_task.py `
  tests/test_agent_eval_runner.py -q
```

Expected: all pass.

- [ ] **Step 7: Review and commit Task 3**

```powershell
git diff --check
git add -- `
  backend/agent_eval/fixture.py `
  backend/agent_eval/task.py `
  backend/agent_eval/runner.py `
  tests/test_agent_eval_fixture.py `
  tests/test_agent_eval_task.py `
  tests/test_agent_eval_runner.py
git commit -m "feat(eval): seal evaluation fixtures"
```

---

### Task 4: Bounded Subprocess and Ollama Response Capture

**Files:**

- Modify: `backend/agent_eval/capture.py`
- Modify: `backend/agent_eval/opencode.py`
- Modify: `backend/agent_eval/raw_ollama.py`
- Modify: `backend/agent_eval/result.py`
- Modify: `tests/test_agent_eval_capture.py`
- Modify: `tests/test_agent_eval_opencode.py`
- Modify: `tests/test_agent_eval_raw_ollama.py`
- Modify: `tests/test_agent_eval_runner.py`

**Interfaces:**

- Produces:
  - `CaptureLimits`.
  - `CapturedProcess`.
  - `run_bounded_process(argv, *, cwd, env, timeout, limits, launcher=None)`.
  - `capture_bounded_response(response, max_bytes)`.
- Consumes: Task 2 `CaptureLimitExceeded`.
- Task 5 supplies the optional contained Windows launcher.

- [ ] **Step 1: Write failing process-capture tests**

```python
# append to tests/test_agent_eval_capture.py
import sys

from backend.agent_eval.capture import CaptureLimits, run_bounded_process


def test_bounded_process_captures_stdout_stderr_and_exit_code(tmp_path):
    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=1024, stderr_bytes=1024),
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert result.overflowed is False


def test_bounded_process_terminates_on_stdout_overflow(tmp_path):
    result = run_bounded_process(
        [sys.executable, "-c", "print('x' * 4096)"],
        cwd=tmp_path,
        env={},
        timeout=10,
        limits=CaptureLimits(stdout_bytes=128, stderr_bytes=128),
    )
    assert result.overflowed is True
    assert result.completed is False
    assert result.observed_stdout_bytes > result.limits.stdout_bytes
```

Add tests for stderr overflow, timeout, invalid UTF-8 replacement, stdin closed,
concurrent stdout/stderr without deadlock, 256 KiB JSONL line limit, 50,000
event limit, and temporary-log cleanup.

- [ ] **Step 2: Write failing raw-response overflow tests**

Inject a response whose `read(size)` asserts `size == max_bytes + 1`. Verify
`run_raw` returns `completed=False`, `errors=("response_body_overflow",)`, and
records the observed/allowed bytes without retaining the oversized body.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_opencode.py `
  tests/test_agent_eval_raw_ollama.py -q
```

Expected: new capture interfaces are absent.

- [ ] **Step 4: Implement streaming subprocess capture**

Use `subprocess.Popen` with `stdin=DEVNULL`, `stdout=PIPE`, `stderr=PIPE`,
binary mode, and one reader thread per stream. Each reader:

- reads fixed 64 KiB chunks;
- writes only up to the declared limit to an exclusive temporary file;
- records actual bytes observed through the overflow boundary;
- signals termination immediately after `limit + 1`;
- decodes only after completion with UTF-8 replacement.

Constants:

```python
DEFAULT_CAPTURE_LIMITS = CaptureLimits(
    stdout_bytes=4 * 1024 * 1024,
    stderr_bytes=1 * 1024 * 1024,
    jsonl_events=50_000,
    jsonl_line_bytes=256 * 1024,
    cleanup_grace_seconds=5,
)
```

The default non-Windows termination callback may use
`process.terminate()` then `process.kill()`. Verified Windows mode must refuse
this callback; Task 5 supplies Job Object termination.

- [ ] **Step 5: Refactor OpenCode and raw adapters**

`_run_opencode` consumes `CapturedProcess` and parses JSONL from the bounded
stdout file. Parsing stops and fails on line/event ceilings.

`run_raw` uses `bounded_http_json` for the complete chat request with the
8 MiB response ceiling and includes exact request-generation options in
`ArmResult.request_metadata`.

Extend `ArmResult` with:

```python
capture: dict[str, Any] = field(default_factory=dict)
request_metadata: dict[str, Any] = field(default_factory=dict)
```

No response body, stdout, or stderr may be fetched with an unbounded `.read()`.

- [ ] **Step 6: Run focused tests and runner regression**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_opencode.py `
  tests/test_agent_eval_raw_ollama.py `
  tests/test_agent_eval_runner.py -q
```

Expected: all pass and the
`bounded_process_and_http_capture` evidence control passes in unit-injected
runs.

- [ ] **Step 7: Review and commit Task 4**

```powershell
git diff --check
git add -- `
  backend/agent_eval/capture.py `
  backend/agent_eval/opencode.py `
  backend/agent_eval/raw_ollama.py `
  backend/agent_eval/result.py `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_opencode.py `
  tests/test_agent_eval_raw_ollama.py `
  tests/test_agent_eval_runner.py
git commit -m "feat(eval): bound process and HTTP capture"
```

---

### Task 5: Windows Job Object Process Containment

**Files:**

- Create: `backend/agent_eval/windows_job.py`
- Create: `tests/test_agent_eval_windows_job.py`
- Modify: `backend/agent_eval/capture.py`
- Modify: `backend/agent_eval/opencode.py`
- Modify: `backend/agent_eval/runner.py`
- Modify: `tests/test_agent_eval_capture.py`
- Modify: `tests/test_agent_eval_opencode.py`
- Modify: `tests/test_agent_eval_runner.py`

**Interfaces:**

- Produces:
  - `WindowsJobError`.
  - `WindowsJobLimits(active_processes=1, memory_bytes=None)`.
  - `WindowsJobProcess.start(argv, cwd, env, limits)`.
  - `WindowsJobProcess.terminate(exit_code)`.
  - `WindowsJobProcess.active_processes()`.
  - `WindowsJobProcess.close()`.
- Consumes: Task 4 launcher seam.

- [ ] **Step 1: Write failing Win32 contract tests with an injectable API**

```python
# tests/test_agent_eval_windows_job.py
from backend.agent_eval.windows_job import WindowsJobLimits, WindowsJobProcess


def test_process_is_created_suspended_assigned_then_resumed(fake_win32):
    process = WindowsJobProcess.start(
        ["opencode.exe", "--version"],
        cwd="C:\\work",
        env={"PATH": "C:\\bin"},
        limits=WindowsJobLimits(active_processes=1),
        api=fake_win32,
    )
    assert fake_win32.calls[:4] == [
        ("create_job",),
        ("set_limits", 1, True),
        ("create_process_suspended", "opencode.exe"),
        ("assign_process",),
    ]
    assert ("resume_thread",) in fake_win32.calls
    process.close()


def test_close_kills_job_and_reports_zero_active_processes(fake_win32):
    process = fake_started_job(fake_win32)
    process.terminate(124)
    process.close()
    assert ("terminate_job", 124) in fake_win32.calls
    assert process.active_processes() == 0
```

Add failure tests for every Win32 call, assignment before resume, nested-job
failure, timeout, overflow termination, close idempotence, handle closure, and
active process count above zero.

Define `FakeWin32Api` and the `fake_win32` fixture in the same test module. The
fake must expose the exact bound methods from Step 4, allocate monotonically
increasing integer handles, return zero active processes by default, append
each call tuple to `calls`, and allow each method to be configured to raise
`OSError`. `fake_started_job(fake_win32)` calls `WindowsJobProcess.start` with
the same fixed argv/cwd/environment as the first test; it is not a production
helper.

- [ ] **Step 2: Write a real Windows child-denial test**

Use a child script that attempts to spawn another Python process. Mark only
platform dependence, not elevation:

```python
@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_active_process_limit_denies_child_process(tmp_path):
    ...
    assert result.exit_code != 0
    assert job.active_processes() == 0
```

- [ ] **Step 3: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_windows_job.py -q
```

Expected: missing module/import failure.

- [ ] **Step 4: Implement minimal `ctypes` Job Object bindings**

Bind only these Kernel32 APIs with explicit `argtypes` and `restype`:

- `CreateJobObjectW`
- `SetInformationJobObject`
- `CreateProcessW`
- `AssignProcessToJobObject`
- `ResumeThread`
- `TerminateJobObject`
- `QueryInformationJobObject`
- `WaitForSingleObject`
- `GetExitCodeProcess`
- `CloseHandle`

Define only required structures:

- `STARTUPINFOW`
- `PROCESS_INFORMATION`
- `JOBOBJECT_BASIC_LIMIT_INFORMATION`
- `IO_COUNTERS`
- `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`
- `JOBOBJECT_BASIC_ACCOUNTING_INFORMATION`

Set:

```python
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
```

Construct the Windows command line with `subprocess.list2cmdline`, construct a
sorted NUL-separated Unicode environment block, assign the suspended process,
then resume. Neither breakaway flag is permitted.

- [ ] **Step 5: Integrate Job launch with bounded capture**

`run_bounded_process(..., launcher=WindowsJobProcess.start)` must use the Job
Object for timeout and overflow termination. Verified Windows mode refuses to
start when:

- the launcher is not a `WindowsJobProcess`;
- assignment fails;
- active process limit cannot be set;
- final active process count is nonzero;
- any cleanup handle operation is uncertain.

Emit `windows_process_tree_containment` from measured job results.

- [ ] **Step 6: Run focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_windows_job.py `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_opencode.py `
  tests/test_agent_eval_runner.py -q
```

Expected: all pass on Windows; only explicitly impossible link tests may skip.

- [ ] **Step 7: Review and commit Task 5**

```powershell
git diff --check
git add -- `
  backend/agent_eval/windows_job.py `
  backend/agent_eval/capture.py `
  backend/agent_eval/opencode.py `
  backend/agent_eval/runner.py `
  tests/test_agent_eval_windows_job.py `
  tests/test_agent_eval_capture.py `
  tests/test_agent_eval_opencode.py `
  tests/test_agent_eval_runner.py
git commit -m "feat(eval): contain Windows process trees"
```

---

### Task 6: Windows WFP Loopback-Only Network Containment

**Files:**

- Create: `backend/agent_eval/containment.py`
- Create: `backend/agent_eval/windows_wfp.py`
- Create: `tests/test_agent_eval_windows_wfp.py`
- Create: `tests/test_agent_eval_live_containment.py`
- Modify: `backend/agent_eval/runner.py`
- Modify: `scripts/agent_eval.py`
- Modify: `pytest.ini`
- Modify: `tests/test_agent_eval_runner.py`
- Modify: `tests/test_agent_eval_script.py`

**Interfaces:**

- Produces:
  - `ContainmentError`.
  - `ContainmentProvider` protocol.
  - `DiagnosticContainmentProvider`.
  - `select_containment_provider(mode, platform, executable_paths)`.
  - `WindowsWfpSession.open(endpoint, application_paths)`.
  - `WindowsWfpSession.filter_ids`.
  - `WindowsWfpSession.verify_active()`.
  - `WindowsWfpSession.close()`.
- Consumes: Task 2 exact executable identities and Task 5 Job Object execution.

- [ ] **Step 1: Write failing pure WFP policy tests**

```python
# tests/test_agent_eval_windows_wfp.py
import pytest

from backend.agent_eval.windows_wfp import (
    WindowsWfpError,
    WindowsWfpSession,
)


def test_dynamic_session_adds_permit_then_block_for_each_app_and_family(fake_wfp):
    session = WindowsWfpSession.open(
        endpoint=("127.0.0.1", 11434),
        application_paths=[r"C:\Program Files\LAC\lac.exe", r"C:\bin\opencode.exe"],
        api=fake_wfp,
    )
    assert fake_wfp.dynamic is True
    assert fake_wfp.filters == expected_filters(
        apps=2,
        permit_v4=("127.0.0.1", 11434),
        permit_v6=("::1", 11434),
        block_other=True,
    )
    assert session.verify_active() is True
    session.close()
    assert fake_wfp.closed is True


def test_open_fails_closed_without_administrator_rights(fake_wfp):
    fake_wfp.open_error = 5  # ERROR_ACCESS_DENIED
    with pytest.raises(WindowsWfpError, match="administrator"):
        WindowsWfpSession.open(
            endpoint=("127.0.0.1", 11434),
            application_paths=[r"C:\Program Files\LAC\lac.exe"],
            api=fake_wfp,
        )
```

Add tests for IPv4/IPv6 layers, normalized app IDs, exact remote port,
non-loopback refusal, duplicate app paths, missing executable, shared system
Python refusal, filter-add rollback, verification failure, dynamic-session
cleanup, close idempotence, and cleanup uncertainty.

Define `FakeWfpApi` and `expected_filters` in the same test module.
`FakeWfpApi` must record `dynamic`, `filters`, `closed`, allocated IDs, and an
optional `open_error`; it implements the nine bound functions named in Step 4.
`expected_filters` returns eight normalized records for two apps: IPv4 permit,
IPv4 block, IPv6 permit, and IPv6 block per app, with permit weight greater
than block weight and the exact remote port present only on permit records.

- [ ] **Step 2: Register the privileged live marker**

Add to `pytest.ini`:

```ini
    live_containment: privileged Windows Job Object and WFP containment tests
```

Create `tests/test_agent_eval_live_containment.py` with:

```python
pytestmark = [
    pytest.mark.live,
    pytest.mark.live_containment,
    pytest.mark.skipif(os.name != "nt", reason="Windows WFP reference provider"),
]
```

The live test starts disposable listener endpoints and proves:

- allowed `127.0.0.1:<ollama-port>` succeeds;
- another loopback port fails;
- public IPv4 fails;
- public IPv6 fails when IPv6 is available;
- DNS lookup fails;
- `HTTP_PROXY`/`HTTPS_PROXY` cannot escape;
- filters disappear after normal close and forced process termination.

When not elevated, the test must fail with a precise elevation message rather
than skip during a release-candidate containment run.

- [ ] **Step 3: Run pure tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_windows_wfp.py -q
```

Expected: missing module/import failure.

- [ ] **Step 4: Implement WFP user-mode bindings**

Use `ctypes.WinDLL("fwpuclnt.dll", use_last_error=True)` and bind:

- `FwpmEngineOpen0`
- `FwpmEngineClose0`
- `FwpmSubLayerAdd0`
- `FwpmSubLayerDeleteByKey0`
- `FwpmFilterAdd0`
- `FwpmFilterDeleteById0`
- `FwpmFilterGetById0`
- `FwpmGetAppIdFromFileName0`
- `FwpmFreeMemory0`

Define the required WFP structures/unions and GUID constants from the Windows
SDK:

- dynamic `FWPM_SESSION0` with `FWPM_SESSION_FLAG_DYNAMIC`;
- unique per-run `FWPM_SUBLAYER0`;
- `FWPM_FILTER0`;
- `FWPM_FILTER_CONDITION0`;
- `FWP_VALUE0` and `FWP_CONDITION_VALUE0`;
- `FWPM_LAYER_ALE_AUTH_CONNECT_V4`;
- `FWPM_LAYER_ALE_AUTH_CONNECT_V6`;
- `FWPM_CONDITION_ALE_APP_ID`;
- `FWPM_CONDITION_IP_REMOTE_ADDRESS`;
- `FWPM_CONDITION_IP_REMOTE_PORT`;
- `FWP_ACTION_PERMIT` and `FWP_ACTION_BLOCK`.

Within LAC's unique sublayer, add higher-weight exact permit filters before
lower-weight block-all filters. Filters are non-persistent and exist only in
the dynamic engine session. Record every returned filter ID.

Do not use `netsh`, PowerShell firewall cmdlets, permanent Windows Firewall
rules, registry edits, or a kernel driver.

- [ ] **Step 5: Implement containment-provider selection**

```python
def select_containment_provider(
    mode: EvidenceMode,
    *,
    platform: str,
    endpoint: tuple[str, int],
    application_paths: Sequence[Path],
) -> ContainmentProvider:
    if mode is EvidenceMode.DIAGNOSTIC:
        return DiagnosticContainmentProvider(
            reason="OS network policy not enforced in diagnostic mode"
        )
    if platform != "win32":
        raise ContainmentError("verified_containment_unsupported")
    return WindowsContainmentProvider.open(endpoint, application_paths)
```

The verified provider owns both the WFP session and Job Object launcher. It
installs policy before the first model request and closes it in `finally`.
Preflight environment capture happens before policy installation.

- [ ] **Step 6: Wire fail-closed dry-run and runtime behavior**

Verified `--dry-run` performs WFP capability preflight without generating
tokens, reports whether elevation is present, displays exact application paths
affected, and closes the dynamic session.

If unavailable, output:

```text
Verified Windows network containment requires an elevated terminal.
Reopen PowerShell as Administrator and rerun:
<exact original command>
```

Diagnostic mode reports `os_loopback_only_egress=unsupported` and remains
invalid.

- [ ] **Step 7: Run pure containment and runner tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_windows_wfp.py `
  tests/test_agent_eval_runner.py `
  tests/test_agent_eval_script.py -q
```

Expected: all pass without elevation because WFP APIs are injected.

- [ ] **Step 8: Run the privileged adversarial test only with approval**

Before executing, show:

- exact temporary filter scope;
- exact executable paths;
- endpoint and port;
- expected duration;
- confirmation that the dynamic session removes filters on close.

Then run from elevated PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_live_containment.py `
  -m live_containment -vv
```

Expected: loopback Ollama port succeeds; all specified escape attempts fail;
cleanup verification passes.

- [ ] **Step 9: Review and commit Task 6**

```powershell
git diff --check
git add -- `
  backend/agent_eval/containment.py `
  backend/agent_eval/windows_wfp.py `
  backend/agent_eval/runner.py `
  scripts/agent_eval.py `
  pytest.ini `
  tests/test_agent_eval_windows_wfp.py `
  tests/test_agent_eval_live_containment.py `
  tests/test_agent_eval_runner.py `
  tests/test_agent_eval_script.py
git commit -m "feat(eval): enforce loopback-only Windows evidence"
```

---

### Task 7: Task Schema v2 and Counterbalanced Deterministic Trials

**Files:**

- Create: `backend/agent_eval/schedule.py`
- Create: `tests/test_agent_eval_schedule.py`
- Modify: `backend/agent_eval/task.py`
- Modify: `backend/agent_eval/runner.py`
- Modify: `backend/agent_eval/raw_ollama.py`
- Modify: `backend/agent_eval/opencode.py`
- Modify: `backend/agent_launch/config_writer.py`
- Modify: `evals/agent/tasks/python-empty-mean.json`
- Modify: `tests/test_agent_eval_task.py`
- Modify: `tests/test_agent_eval_runner.py`
- Modify: `tests/test_agent_eval_raw_ollama.py`
- Modify: `tests/test_agent_eval_opencode.py`
- Modify: `tests/test_agent_config_writer.py`

**Interfaces:**

- Produces:
  - `GenerationSettings(temperature, seed_base, max_output_tokens)`.
  - `TrialSpec(index, seed, arm_order)`.
  - `EvaluationSchedule(trials)`.
  - `build_schedule(task_contract_sha256, model_digests, generation, trials)`.
- Consumes: identities from Task 2 and all containment/capture controls.

- [ ] **Step 1: Write failing schema-v2 tests**

```python
def test_schema_v2_requires_three_trials_and_exact_generation_fields(tmp_path):
    task = load_task("python-empty-mean", suite_root)
    assert task.schema_version == 2
    assert task.trials == 3
    assert task.generation.temperature == 1.0
    assert task.generation.seed_base == 20260726
    assert task.generation.max_output_tokens == 128
```

Add rejection tests for schema v1 in verified mode, unknown generation keys,
boolean/nonnumeric values, trials other than exactly `3`, temperature outside
`0..2`, seed outside signed 31-bit range, and output tokens outside `1..4096`.
Diagnostic mode may load legacy schema v1 only when the artifact records
`legacy_task_schema`.

- [ ] **Step 2: Write failing schedule tests**

```python
# tests/test_agent_eval_schedule.py
from backend.agent_eval.schedule import build_schedule


def test_schedule_is_deterministic_counterbalanced_and_same_seed_per_trial():
    schedule = build_schedule(
        task_contract_sha256="a" * 64,
        model_digests=("b" * 64, "c" * 64),
        seed_base=20260726,
        trials=3,
    )
    assert [trial.arm_order for trial in schedule.trials] == [
        ("raw", "stock", "lac"),
        ("stock", "lac", "raw"),
        ("lac", "raw", "stock"),
    ]
    assert len({trial.seed for trial in schedule.trials}) == 3
    assert schedule == build_schedule(
        task_contract_sha256="a" * 64,
        model_digests=("b" * 64, "c" * 64),
        seed_base=20260726,
        trials=3,
    )
```

Verify the seed is the first 31 bits of SHA-256 over compact canonical JSON of
task hash, sorted model digests, seed base, and trial index.

- [ ] **Step 3: Run schema/schedule tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_task.py `
  tests/test_agent_eval_schedule.py -q
```

Expected: schema mismatch and missing schedule module.

- [ ] **Step 4: Implement schema v2**

Update the packaged task:

```json
{
  "schema_version": 2,
  "id": "python-empty-mean",
  "prompt": "Inspect the fixture. If summarize([]) is called, what exception class is raised by the current code? Answer with only the exception class name.",
  "fixture": "python-empty-mean",
  "timeout_seconds": 180,
  "trials": 3,
  "generation": {
    "temperature": 1.0,
    "seed_base": 20260726,
    "max_output_tokens": 128
  },
  "scorer": {
    "type": "exact_text",
    "expected": "ZeroDivisionError"
  }
}
```

Use frozen dataclasses and exact-key validation.

- [ ] **Step 5: Implement schedule derivation**

No random module or wall clock participates. Persist `schedule.json` before the
first trial. Its task hash, model digests, settings, order, and seeds must match
the final ledger.

- [ ] **Step 6: Pass identical generation controls to every arm**

Raw Ollama request:

```python
"options": {
    "temperature": task.generation.temperature,
    "seed": trial.seed,
    "num_predict": task.generation.max_output_tokens,
}
```

OpenCode evaluation config:

```python
"agent": {
    "build": {
        "temperature": generation.temperature,
        "options": {
            "seed": trial.seed,
            "max_tokens": generation.max_output_tokens,
        },
        "steps": 1,
    }
}
```

Before accepting this mapping, add a focused compatibility probe against
OpenCode `1.18.4` with injected HTTP capture proving the exact options reach
Ollama. If OpenCode drops or renames an option, change the mapping to the
observed supported field and pin it in a regression test. Do not mark the
sampling control passed from config shape alone.

- [ ] **Step 7: Refactor runner to three trials**

Write artifacts under:

```text
trials/001/raw
trials/001/stock
trials/001/lac
...
trials/003/lac
```

For each trial:

- use the persisted arm order;
- pass the same seed/settings to every arm;
- use a separate sealed workspace;
- collect all failures without changing later order;
- write per-trial results and scores.

`comparison.json` includes per-trial and aggregate pass counts. The
`counterbalanced_deterministic_sampling` control passes only when all nine
scheduled arm records exist and request metadata matches.

- [ ] **Step 8: Run focused scheduling and adapter suites**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_schedule.py `
  tests/test_agent_eval_task.py `
  tests/test_agent_eval_runner.py `
  tests/test_agent_eval_raw_ollama.py `
  tests/test_agent_eval_opencode.py `
  tests/test_agent_config_writer.py -q
```

Expected: all pass.

- [ ] **Step 9: Review and commit Task 7**

```powershell
git diff --check
git add -- `
  backend/agent_eval/schedule.py `
  backend/agent_eval/task.py `
  backend/agent_eval/runner.py `
  backend/agent_eval/raw_ollama.py `
  backend/agent_eval/opencode.py `
  backend/agent_launch/config_writer.py `
  evals/agent/tasks/python-empty-mean.json `
  tests/test_agent_eval_schedule.py `
  tests/test_agent_eval_task.py `
  tests/test_agent_eval_runner.py `
  tests/test_agent_eval_raw_ollama.py `
  tests/test_agent_eval_opencode.py `
  tests/test_agent_config_writer.py
git commit -m "feat(eval): counterbalance deterministic trials"
```

---

### Task 8: Packaged CLI, Release Gates, and Bounded Live Acceptance

**Files:**

- Create: `backend/agent_eval/command.py`
- Create: `tests/test_agent_eval_command.py`
- Create: `tests/test_cli_agent_eval.py`
- Create: `tests/test_build_agent_eval_contract.py`
- Modify: `scripts/agent_eval.py`
- Modify: `cli.py`
- Modify: `build.spec`
- Modify: `README.md`
- Modify: `docs/GETTING_STARTED.md`
- Modify: `tests/test_cli_dispatch.py`
- Modify: `tests/test_release_readiness.py`

**Interfaces:**

- Produces:
  - `EvalCommandRequest`.
  - `execute_eval_command(request, dependencies=None) -> EvalCommandResult`.
  - packaged `lac eval` command.
- Consumes: all Tasks 1–7.

- [ ] **Step 1: Write failing application-service tests**

```python
# tests/test_agent_eval_command.py
from backend.agent_eval.command import EvalCommandRequest, execute_eval_command


def test_verified_dry_run_returns_zero_only_when_all_controls_ready(tmp_path):
    request = EvalCommandRequest(
        task="python-empty-mean",
        base_model="gpt-oss:20b",
        lac_model="gpt-oss:20b-agent",
        output_dir=tmp_path,
        run_id="acceptance",
        mode="verified",
        dry_run=True,
    )
    result = execute_eval_command(request, dependencies=ready_dependencies())
    assert result.exit_code == 0
    assert result.report["evidence_ready"] is True


def test_verified_command_stops_before_generation_when_control_missing(tmp_path):
    deps = ready_dependencies()
    deps.containment.preflight_result = failed_result("os_loopback_only_egress")
    result = execute_eval_command(verified_request(tmp_path), dependencies=deps)
    assert result.exit_code == 2
    assert deps.runner_calls == 0
```

Define `ReadyDependencies` in the test module with injected model listing,
binary resolution, identity capture, containment preflight, environment
capture, and runner callables. `ready_dependencies()` returns all eight
`EvidenceControlResult` objects in `PASS` state and increments `runner_calls`
only from the runner callable. No real process, model, WFP, filesystem outside
`tmp_path`, or network call is permitted in these tests.

- [ ] **Step 2: Write failing packaged CLI tests**

```python
# tests/test_cli_agent_eval.py
def test_parser_exposes_eval_verified_default_and_diagnostic_opt_in():
    parser = cli.build_parser()
    verified = parser.parse_args(
        [
            "eval",
            "--task", "python-empty-mean",
            "--base-model", "gpt-oss:20b",
            "--lac-model", "gpt-oss:20b-agent",
            "--output-dir", "C:\\evidence",
            "--dry-run",
        ]
    )
    assert verified.command == "eval"
    assert verified.mode == "verified"

    diagnostic = parser.parse_args(
        [
            "eval",
            "--task", "python-empty-mean",
            "--base-model", "gpt-oss:20b",
            "--lac-model", "gpt-oss:20b-agent",
            "--output-dir", "C:\\evidence",
            "--mode", "diagnostic",
        ]
    )
    assert diagnostic.mode == "diagnostic"
```

Also assert `server._is_cli_invocation(["eval", "--dry-run"])` routes to CLI,
JSON mode emits no banner, and a nonzero service result becomes the process
exit code.

- [ ] **Step 3: Write failing packaging-contract tests**

```python
# tests/test_build_agent_eval_contract.py
def test_build_spec_packages_agent_eval_tasks_and_command():
    text = Path("build.spec").read_text(encoding="utf-8")
    assert 'PROJECT_ROOT / "evals" / "agent"' in text
    assert '"backend.agent_eval.command"' in text
    assert 'evals_dir.rglob("*")' in text
    assert 'relative_to(PROJECT_ROOT)' in text
```

Also assert `README.md` and `docs/GETTING_STARTED.md` state:

- verified mode needs elevated Windows PowerShell;
- diagnostic artifacts are invalid;
- no downloads occur;
- nine bounded arm runs require operator approval;
- one smoke is not a competitive capability claim.

- [ ] **Step 4: Run command/CLI/packaging tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_agent_eval_command.py `
  tests/test_cli_agent_eval.py `
  tests/test_build_agent_eval_contract.py `
  tests/test_cli_dispatch.py `
  tests/test_release_readiness.py -q
```

Expected: command module and CLI parser are absent.

- [ ] **Step 5: Extract the reusable command service**

Move orchestration dependencies from `scripts/agent_eval.py` into
`backend/agent_eval/command.py`. The source script becomes only:

```python
from backend.agent_eval.command import main

if __name__ == "__main__":
    raise SystemExit(main())
```

The service owns parsing-independent request validation, preflight, execution,
JSON report creation, and exit codes:

- `0`: dry-run ready or verified artifact valid and all arms executed;
- `1`: completed run with invalid/failed arm evidence;
- `2`: input, identity, containment, or preflight failure before generation.

- [ ] **Step 6: Add `lac eval`**

Add all existing evaluator arguments plus:

```text
--mode verified|diagnostic
--json
--dry-run
```

Default mode is `verified`. `cmd_eval` constructs `EvalCommandRequest`, calls
the service, prints its JSON/report, and raises `SystemExit` for nonzero status.
No installer or UI launch occurs.

- [ ] **Step 7: Package tasks and command**

In `build.spec`:

- declare `evals_dir = PROJECT_ROOT / "evals" / "agent"`;
- include `evals/agent/tasks/*.json`;
- include fixture regular files with `for f in evals_dir.rglob("*")` while
  preserving `f.parent.relative_to(PROJECT_ROOT)`;
- add `backend.agent_eval.command` and Windows containment modules to hidden
  imports if PyInstaller analysis does not discover them;
- keep evaluation workspaces and output external to the installation tree.

Build-contract tests must inspect the collected `Analysis.datas`, not merely
search for a string.

- [ ] **Step 8: Update truthful documentation**

Add a dedicated internal/public-evidence section with exact commands:

```powershell
# Read-only preflight; no model tokens
lac eval --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir C:\lac-evidence `
  --run-id phase0-smoke `
  --dry-run --json

# Explicit non-evidence developer run
lac eval ... --mode diagnostic
```

Do not document the live verified command as ready until the privileged clean
build passes.

- [ ] **Step 9: Run all non-live gates**

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:TEMP = "F:\AI Workspace\Youtube Workspace\.tmp\lac-evidence-pytest"
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest `
  -m "not live" -p no:cacheprovider -q `
  --basetemp "$env:TEMP\run"
```

Then:

```powershell
cd web
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
npm.cmd run check:bundle
npm.cmd run audit:release
cd ..
python scripts/third_party_audit.py
git diff --check
```

Expected: all pass; only expected Windows symlink skips remain.

- [ ] **Step 10: Build and inspect the packaged Windows candidate**

```powershell
cd web
npm.cmd run build
cd ..
.\.venv\Scripts\pyinstaller.exe -y build.spec
dist\lac\lac.exe eval --help
dist\lac\lac.exe eval `
  --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir "F:\AI Workspace\Youtube Workspace\.tmp\lac-evidence-dry" `
  --run-id packaged-preflight `
  --dry-run --json
```

Expected: help works from the packaged executable; non-elevated verified
preflight fails before generation with the exact elevated remediation.

- [ ] **Step 11: Run privileged clean-build containment acceptance**

After explicit approval, open elevated PowerShell and run:

```powershell
dist\lac\lac.exe eval `
  --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir "F:\AI Workspace\Youtube Workspace\.tmp\lac-evidence-verified" `
  --run-id phase0-smoke-20260726 `
  --dry-run --json
```

Require:

- all eight controls ready;
- exact OpenCode `1.18.4`;
- exact full model digests;
- no downloads;
- dynamic WFP cleanup proof;
- nine-arm maximum runtime displayed.

Stop and ask for final runtime approval before removing `--dry-run`.

- [ ] **Step 12: Run the bounded live baseline only after runtime approval**

```powershell
dist\lac\lac.exe eval `
  --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir "F:\AI Workspace\Youtube Workspace\.tmp\lac-evidence-verified" `
  --run-id phase0-smoke-20260726 `
  --mode verified --json
```

After completion:

```powershell
.\.venv\Scripts\python.exe -m backend.agent_eval.ledger verify `
  "F:\AI Workspace\Youtube Workspace\.tmp\lac-evidence-verified\phase0-smoke-20260726"
```

Manually compare every trial's captured terminal response with its score.
Report only the exact task/model/runtime/hardware tuple and state that it is a
single-machine smoke, not a competitive benchmark.

- [ ] **Step 13: Final adversarial review**

Review:

- every `artifact_valid` assignment;
- every network/process cleanup path;
- every external read ceiling;
- every identity before/after comparison;
- all diagnostic-mode paths;
- WFP filter scope and teardown;
- Job Object descendant count;
- ledger completeness and tamper detection.

No Critical or Important finding may remain before Task 8 commit.

- [ ] **Step 14: Commit Task 8**

```powershell
git diff --check
git status --short
git add -- `
  backend/agent_eval/command.py `
  scripts/agent_eval.py `
  cli.py `
  build.spec `
  README.md `
  docs/GETTING_STARTED.md `
  tests/test_agent_eval_command.py `
  tests/test_cli_agent_eval.py `
  tests/test_build_agent_eval_contract.py `
  tests/test_cli_dispatch.py `
  tests/test_release_readiness.py
git commit -m "feat(eval): ship verified agent evidence command"
```

Do not tag, publish, deploy, or install from this commit.

---

## Completion Gate

The implementation plan is complete only when:

- all eight tasks have separate reviewed commits;
- the full non-live Python and web gates pass;
- the privileged WFP and Job Object adversarial suite passes;
- the packaged dry-run reports every control ready;
- the operator separately approves the nine-arm runtime;
- the completed run's ledger verifies independently;
- captured responses and scores agree;
- local and remote commit SHAs match after an explicitly authorized push;
- no release tag, publication, deployment, or installer mutation occurs.
