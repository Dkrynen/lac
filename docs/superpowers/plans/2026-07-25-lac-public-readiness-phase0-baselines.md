# LAC Public Readiness Phase 0 Baseline Plan

> **For agentic workers:** Implement task-by-task with
> `superpowers:test-driven-development` and verify before claiming completion.

**Goal:** Produce reproducible, machine-readable comparisons of the same local
model and task under raw Ollama, stock OpenCode, and the LAC-configured OpenCode
harness.

**Scope:** Phase 0 Slice B. This establishes the evaluation contract and a
small, cheap smoke suite. It does not claim broad coding-agent superiority and
does not run paid Claude, Codex, or hosted-model comparisons.

**Architecture:** A trusted, versioned task manifest is copied into a fresh
workspace for each arm. Raw Ollama receives the prompt plus a deterministic
source snapshot. Stock OpenCode receives the prompt in a minimal provider-only
project. LAC OpenCode receives the same prompt and files with the LAC-generated
configuration. A deterministic scorer evaluates only the final response in this
first read-only slice. Every run records model/runtime identity, wall time,
process outcome, parsed token metrics where available, score, and artifact
paths.

## Global constraints

- Repo: `C:\Users\User\repos\model-hub`.
- Branch: `feat/2026-07-15-lac-terminal-agent-p1`.
- No push, deploy, purchase, model download, secret access, or remote provider.
- Use only already-installed Ollama models.
- OpenCode compatibility target remains exactly `1.18.4`.
- Run workspaces live under an explicit output root, never in the source repo.
- Refuse task manifests outside the packaged trusted suite.
- Phase 0 tasks are read-only and use an exact-text scorer; do not execute
  manifest-provided shell commands.
- Stock and LAC arms use `opencode run --format json --pure --auto` only inside
  the disposable copied fixture. The report must disclose that auto-approval
  was enabled for reproducibility.
- A timeout or malformed event stream is a recorded failed arm, not an
  unhandled crash and not a pass.
- The same base model must be used for raw and stock arms. The LAC arm may use
  only the explicitly named, already-installed agent variant derived from that
  base.
- Results are evidence for this exact task/model/runtime/hardware tuple only.

---

### Task 1: Trusted task contract and deterministic scoring

**Files:**
- Create: `backend/agent_eval/__init__.py`
- Create: `backend/agent_eval/task.py`
- Create: `backend/agent_eval/scoring.py`
- Create: `tests/test_agent_eval_task.py`
- Create: `tests/test_agent_eval_scoring.py`
- Create: `evals/agent/tasks/python-empty-mean.json`
- Create: `evals/agent/fixtures/python-empty-mean/stats_service.py`

**Contract:**

- `load_task(task_id, suite_root) -> EvalTask` accepts a safe task identifier,
  resolves only beneath `suite_root/tasks`, validates schema version 1, checks
  that the fixture resolves beneath `suite_root/fixtures`, and rejects unknown
  keys or scorer types.
- `snapshot_fixture(fixture_root) -> str` returns a stable, path-sorted UTF-8
  source snapshot with per-file and total byte limits. It rejects symlinks,
  non-regular files, undecodable content, and secret-shaped filenames.
- `score_exact_text(response, expected) -> ScoreResult` normalizes only outer
  whitespace; it does not use an LLM judge.

- [ ] Write traversal, symlink, secret-file, size-limit, schema, and exact-score
  tests first and verify they fail.
- [ ] Implement the smallest immutable dataclasses and validators that satisfy
  the tests.
- [ ] Add one read-only task whose answer is deterministic from a small Python
  fixture and whose expected answer is not copied into the run workspace.
- [ ] Verify the task/scoring suite passes.

---

### Task 2: Raw Ollama arm

**Files:**
- Create: `backend/agent_eval/raw_ollama.py`
- Create: `tests/test_agent_eval_raw_ollama.py`

**Contract:**

- `run_raw(task, model, ollama_host, timeout_seconds) -> ArmResult`.
- Build a stable prompt from the task instructions plus the bounded fixture
  snapshot.
- Call Ollama locally with streaming disabled and no tools.
- Record response text, wall time, evaluation token count/duration, throughput,
  model identity, endpoint identity, and errors.
- Never pull, create, delete, or mutate a model.

- [ ] Write request-body, metric extraction, timeout, HTTP-error, and empty
  response tests first.
- [ ] Implement against the existing Ollama provider boundary or a small
  injectable request seam.
- [ ] Verify focused tests pass without requiring a live daemon.

---

### Task 3: Stock and LAC OpenCode arms

**Files:**
- Create: `backend/agent_eval/opencode.py`
- Modify: `backend/agent_launch/config_writer.py`
- Create: `tests/test_agent_eval_opencode.py`
- Modify: `tests/test_agent_config_writer.py`

**Contract:**

- Add a provider-only config writer for the stock arm without LAC permissions,
  commands, model selection logic, or agent variant creation.
- Reuse `write_opencode_config` for the LAC arm.
- Resolve and version-check OpenCode through `resolve_opencode_binary`.
- Invoke an argument vector, never a shell string:

```text
opencode run <prompt> --format json --pure --auto --model <provider/model>
  --dir <disposable workspace>
```

- Parse JSONL defensively, preserve raw stdout/stderr as artifacts, extract the
  final assistant text and token/cost fields when present, and record unknown
  event types rather than discarding evidence.
- Enforce one wall-clock timeout and terminate only the process LAC started.

- [ ] Write exact argv/config, JSONL parsing, malformed-event, nonzero-exit, and
  timeout tests first.
- [ ] Implement the stock and LAC adapters through injectable process seams.
- [ ] Verify the LAC arm uses the fail-closed config and the stock arm does not.

---

### Task 4: Orchestration, artifact ledger, and CLI

**Files:**
- Create: `backend/agent_eval/runner.py`
- Create: `scripts/agent_eval.py`
- Create: `tests/test_agent_eval_runner.py`
- Create: `tests/test_agent_eval_script.py`

**Contract:**

- Required arguments: task ID, base model, LAC model, and output directory.
- Refuse unless both models are already installed and the LAC model is the
  expected `-agent` variant of the base model.
- Create distinct raw/stock/LAC run directories from the same immutable fixture.
- Persist `manifest.json`, per-arm `stdout.log`, `stderr.log`, `result.json`, and
  a top-level `comparison.json`.
- Include task schema/hash, fixture hash, git commit/dirty state, hardware
  summary, Ollama host/version, OpenCode version, command policy, timestamps,
  score, and all available performance/safety metrics.
- Exit 0 only when all three arms execute and the comparison artifact validates;
  task failures remain valid measured outcomes and do not by themselves make
  artifact generation fail.

- [ ] Write orchestration, installed-model refusal, artifact-schema, and
  partial-arm-failure tests first.
- [ ] Implement a script entry point with dependency injection around all live
  boundaries.
- [ ] Add `--dry-run` to validate identities, task, paths, and planned commands
  without generating model tokens.
- [ ] Verify focused tests and `--dry-run` against the real local installation.

---

### Task 5: Bounded live evidence

- [ ] Run the complete focused evaluation unit suite.
- [ ] Run the repository suite and separate new regressions from the two known
  baseline failures:
  `test_build_workflow_verifies_source_version_and_uploads_checksum` and
  `test_tui_loads_models_into_bar`.
- [ ] Run one real three-arm task only after dry-run output proves:
  OpenCode `1.18.4`, base `gpt-oss:20b`, LAC variant
  `gpt-oss:20b-agent`, local Ollama, disposable output root, and no downloads.
- [ ] Inspect all artifacts and confirm the scorer result matches the captured
  final response for each arm.
- [ ] Report results as a single-machine smoke baseline, not a competitive
  capability claim.

## Phase 0 Slice B acceptance

- One command can reproduce all three arms from the same trusted task.
- No arm can read the expected answer from its workspace.
- No model or dependency is downloaded or silently created.
- Raw, stock, and LAC identities and prompts are auditable.
- Output survives process failure and is machine-readable.
- Scoring is deterministic and does not use the evaluated model.
- The live smoke result is explicitly scoped to its exact
  task/model/runtime/hardware tuple.
