# LAC Agent Evidence Integrity Design

**Date:** 2026-07-26

**Status:** Approved direction; written specification awaiting final user review

**Scope:** Phase 0 exit gate for the raw Ollama, stock OpenCode, and LAC
OpenCode evaluation runner

## 1. Decision

LAC will finish evidence integrity before adding Phase 1 harness features.
Evaluation has two explicit modes:

- `verified` is the default. It runs only when every required identity,
  containment, capture, fixture, scheduling, and artifact control is active.
  Any missing or failed control stops before model generation or produces an
  invalid artifact.
- `diagnostic` is an explicit opt-in for local development. It may run without
  privileged containment, but its artifacts always contain
  `"artifact_valid": false` and cannot be presented as benchmark evidence.

There is no automatic downgrade from `verified` to `diagnostic`.

The first verified implementation targets Windows 11 because that is the
current installer and release platform. Other operating systems must report
`verified_containment_unsupported` until an equivalent provider is implemented
and adversarially tested.

## 2. Why this approach

Three approaches were considered:

1. **Evidence-first, fail-closed verification — selected.** Finish the seven
   declared evidence controls, then run the baseline. This delays visible
   harness features but makes later comparisons defensible.
2. **Run private smokes while containment is incomplete.** Useful for
   development, but the results cannot support public claims. This remains
   available only as explicit `diagnostic` mode.
3. **Start Phase 1 and defer Phase 0 evidence.** Fastest feature velocity, but
   it would make every improvement difficult to measure and easy to overclaim.

The selected approach treats evaluation evidence as a release artifact, not a
console transcript.

## 3. Current baseline

The committed runner already provides:

- trusted task loading with exact schemas, size limits, secret-path refusal,
  and link/reparse-point refusal;
- three explicit arms using installed models only;
- OpenCode `1.18.4` compatibility enforcement;
- isolated OpenCode home, config, data, cache, and temporary directories;
- read-only evaluation tools and external-directory denial;
- deterministic exact-text scoring;
- per-arm workspaces and machine-readable result files;
- truthful `artifact_valid: false` output.

It does not yet provide:

- immutable runtime and model identities;
- OS-enforced loopback-only networking;
- complete Windows descendant-process termination;
- bounded process and HTTP capture;
- sealed and post-run-verified fixture materialization;
- counterbalanced repeated trials;
- a final hash ledger proving artifact completeness and immutability.

## 4. Threat model

### 4.1 Protected claims

A verified artifact must prove, for this machine and run:

- which task, fixture bytes, models, model lineage, binaries, configuration,
  generation settings, order, and hardware were used;
- that no model or runtime dependency was downloaded during the run;
- that evaluated processes could connect only to the approved loopback Ollama
  endpoint;
- that each arm saw equivalent source bytes and generation controls;
- that timeouts and capture ceilings terminated the complete process tree;
- that saved results match the captured responses and deterministic scorer;
- that no artifact was omitted, overwritten, or mutated before sealing.

### 4.2 Defended failures

The design must fail against:

- model tags changing between preflight and completion;
- a LAC variant whose parent is not the declared base model;
- OpenCode or Ollama binary drift;
- cold runtime/plugin/provider bootstrap;
- inherited credentials, proxy settings, global OpenCode config, plugins, MCPs,
  formatters, instructions, or sharing;
- external network access by the evaluator or OpenCode;
- child processes surviving timeout or runner failure;
- stdout, stderr, JSONL, or HTTP bodies exceeding declared ceilings;
- fixture link swaps, mutation, or cross-arm differences;
- duplicate run IDs, partial artifacts, scorer mismatch, or ledger tampering;
- a diagnostic run being mislabeled as verified.

### 4.3 Explicit non-claims

Phase 0 does not claim:

- that OpenCode permissions are an OS filesystem sandbox;
- that one task predicts general coding capability;
- that deterministic seeds make every GPU/backend bit-reproducible;
- that unsigned artifacts are public releases;
- that results from unsupported containment providers are verified.

## 5. Architecture

The runner becomes a fail-closed pipeline:

```text
load task
  -> resolve exact identities
  -> build deterministic schedule
  -> preflight evidence controls
  -> create exclusive run root
  -> materialize and seal trial fixtures
  -> enter network/process containment
  -> execute bounded arms
  -> reverify fixtures, models, runtimes, and containment
  -> score captured responses
  -> write atomic artifacts
  -> hash and seal ledger
  -> compute artifact_valid from control results
```

`artifact_valid` is derived from structured control results. No caller may set
it directly.

### 5.1 Module boundaries

The implementation should introduce these focused units:

- `backend/agent_eval/evidence.py`
  - `EvidenceStatus`, `EvidenceControlResult`, and `EvidenceVerdict`
  - the single function that derives readiness and `artifact_valid`
- `backend/agent_eval/identity.py`
  - `RuntimeIdentity` and `ModelIdentity`
  - bounded Ollama `/api/tags`, `/api/show`, and `/api/version` capture
  - SHA-256 identity for OpenCode, Ollama, LAC, and relevant config/package
    metadata
- `backend/agent_eval/fixture.py`
  - file-level fixture manifest, exclusive materialization, read-only marking,
    and post-run verification
- `backend/agent_eval/capture.py`
  - bounded HTTP and subprocess capture with explicit truncation/failure
    metadata
- `backend/agent_eval/containment.py`
  - provider protocol and diagnostic provider
- `backend/agent_eval/windows_job.py`
  - suspended process creation, Job Object assignment, resource limits,
    timeout termination, and handle cleanup
- `backend/agent_eval/windows_wfp.py`
  - dynamic Windows Filtering Platform session scoped to exact executable
    identities and approved loopback endpoint
- `backend/agent_eval/schedule.py`
  - deterministic trial seeds and counterbalanced arm order
- `backend/agent_eval/ledger.py`
  - atomic artifact writes, file hashes, and final evidence ledger

`runner.py` orchestrates these units and does not reimplement them.

## 6. Evidence controls

### 6.1 Runtime dependency provenance

Preflight resolves the actual executable behind every command wrapper and
records:

- absolute normalized path;
- file size and SHA-256;
- reported version;
- Authenticode status when available;
- OpenCode package metadata and the SHA-256 of the generated config;
- Ollama executable identity and `/api/version`;
- LAC source commit and dirty state.

Verified OpenCode config must explicitly set:

- `autoupdate: false`;
- `share: "disabled"`;
- only the `ollama` provider enabled;
- no plugins, MCP servers, instructions, formatters, remote references, or
  shell tools;
- the exact loopback base URL and model;
- the existing isolated home/config/data/cache directories.

The `$schema` URL is omitted from runtime config. Environment flags remain
defence in depth, not proof.

Runtime directories are snapshotted before and after every arm. New executable,
package, plugin, provider, or dependency files invalidate the run. Cache/log
files may change only when their paths and purposes are explicitly allowlisted
in the manifest.

### 6.2 Immutable Ollama model lineage

Names are insufficient. Preflight uses bounded loopback API calls to record:

- full 64-character digest from `/api/tags`;
- size, modification timestamp, family, parameter size, quantization, and
  capabilities;
- canonical SHA-256 of `/api/show` fields: `modelfile`, `parameters`,
  `template`, `details`, `model_info`, and `capabilities`;
- the LAC variant's declared `details.parent_model`;
- the `FROM` blob digest and generation parameters in its generated Modelfile.

The LAC variant must name the declared base model as its parent. Both tag
digests and canonical show hashes are queried again after all trials. Any drift
invalidates the complete run.

The evidence records current local identities; it never creates, rebuilds,
pulls, or retags a model.

### 6.3 Sealed fixture materialization

The trusted fixture is converted into a manifest containing normalized relative
path, byte length, file SHA-256, aggregate SHA-256, and task-contract SHA-256.

For each trial and arm:

1. create a new destination with exclusive semantics;
2. reject links, reparse points, alternate data streams, non-regular files,
   case-colliding paths, and unexpected files;
3. copy from already-validated source handles;
4. rehash the destination and require an exact manifest match;
5. remove inherited write permissions where supported and mark files
   read-only;
6. rehash after the arm exits.

Any mutation, missing file, added file, metadata escape, or seal failure makes
that arm and the overall artifact invalid. Workspaces remain in the evidence
root for inspection.

### 6.4 Windows process-tree containment

Verified Windows execution uses a Job Object. The root process is created
suspended, assigned to the job, and only then resumed. The job applies:

- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`;
- a wall-clock deadline;
- an active-process limit of one for the current read-only OpenCode arms;
- explicit termination on timeout, capture overflow, runner cancellation, or
  adapter failure.

No breakaway flags are allowed. A failure to create, assign, query, or terminate
the job is a control failure. The runner verifies zero active processes before
sealing evidence.

If OpenCode later requires a legitimate child process, that behavior requires a
new reviewed executable allowlist and adversarial tests; the Phase 0 runner
must not silently relax the limit.

### 6.5 OS-enforced loopback-only egress

Verified Windows mode requires an elevated, dynamic Windows Filtering Platform
session. It installs filters at outbound ALE authorization layers for IPv4 and
IPv6:

- allow the exact evaluator and OpenCode executable identities to connect only
  to `127.0.0.1`, `::1`, or the resolved loopback form of `localhost`;
- restrict the destination to the declared Ollama port;
- block all other outbound connections for those executable identities;
- block DNS and proxy escape;
- remove filters automatically when the dynamic session closes.

The filter provider records its session ID, sublayer, executable application
IDs, endpoint, filter IDs, and cleanup result without recording unrelated
system firewall policy.

Administrator rights are required by the Windows filtering security model.
LAC does not auto-elevate or weaken the requirement. Verified preflight fails
before generation with a copy/paste-ready elevated command when the provider is
unavailable.

Source-mode verified runs are refused when the evaluator uses a shared system
Python executable. A packaged LAC executable or a repository-owned isolated
virtual-environment executable is required, and preflight displays the exact
application path affected by the temporary filters.

### 6.6 Bounded process and HTTP capture

All external data is streamed with hard limits:

- OpenCode stdout: 4 MiB;
- OpenCode stderr: 1 MiB;
- OpenCode JSONL events: 50,000 events;
- individual JSONL line: 256 KiB;
- Ollama response body: 8 MiB;
- Ollama identity response: 2 MiB;
- task timeout: existing task value, maximum 900 seconds;
- process cleanup grace: 5 seconds before forced job termination.

Limit overflow terminates the arm, records the observed byte/event count and
limit, and invalidates evidence. Logs are written incrementally to temporary
files and atomically renamed. The scorer consumes the exact captured terminal
response, not a second parse or console rendering.

### 6.7 Counterbalanced deterministic sampling

Task schema version 2 adds:

```json
{
  "trials": 3,
  "generation": {
    "temperature": 1.0,
    "seed_base": 20260726,
    "max_output_tokens": 128
  }
}
```

Phase 0 requires exactly three trials. Arm order rotates as a Latin square:

1. `raw`, `stock`, `lac`
2. `stock`, `lac`, `raw`
3. `lac`, `raw`, `stock`

Each trial seed is derived from SHA-256 of the task-contract hash, model
digests, and `seed_base`. The same trial seed and generation settings are
passed to all three arms. Preflight fails if the installed OpenCode/Ollama
combination cannot carry the declared settings to the provider.

The schedule, derived seeds, request settings, start/end timestamps, and arm
order are stored before execution. A missing or extra trial invalidates the
run. Results report per-trial outcomes and aggregate counts; Phase 0 does not
turn three samples into a general capability claim.

## 7. Artifact contract

The run layout becomes:

```text
<output>/<run-id>/
  preflight.json
  manifest.json
  schedule.json
  identities/
    lac.json
    ollama.json
    opencode.json
    models.json
  trials/
    001/
      raw/
      stock/
      lac/
    002/...
    003/...
  comparison.json
  controls.json
  evidence.json
```

Each arm directory contains:

- `fixture-manifest.before.json`;
- `fixture-manifest.after.json`;
- `prompt.txt`;
- `request.json` with sensitive headers excluded;
- `stdout.log` and `stderr.log`;
- `events.jsonl` when applicable;
- `result.json`;
- `score.json`.

`evidence.json` is written last and contains:

- schema version and run ID;
- SHA-256 for every other artifact;
- control results with `pass`, `fail`, or `unsupported`;
- incomplete/overflow/timeout flags;
- preflight and postflight identity comparison;
- final `artifact_valid`;
- a deterministic `evidence_root_sha256` over sorted path/hash pairs.

Artifact writes use temporary sibling files, flush, `fsync`, and atomic rename.
Existing run roots are never overwritten. Any file not represented in the
ledger, other than declared workspaces, invalidates verification.

## 8. Failure behavior

- Preflight control failure: no model tokens are generated and no containment
  policy is left active.
- Arm failure: persist bounded failure evidence, terminate its job, continue
  only when doing so cannot compromise containment, and mark the overall run
  invalid.
- Runner interruption: close Job and WFP sessions in `finally` blocks, write an
  incomplete control record when safe, and never write a valid ledger.
- Cleanup uncertainty: fail closed and print exact filter/job identifiers for
  operator inspection.
- Diagnostic mode: run the same capture and artifact pipeline where possible,
  list missing controls, and force invalidity.

## 9. Verification strategy

### 9.1 Unit and contract tests

- exact identity schema and canonical hashing;
- model digest/parent/show drift;
- runtime/config/package drift;
- task schema v2 and deterministic schedule;
- counterbalanced order and identical per-trial settings;
- fixture case collisions, links, reparse points, alternate streams, mutation,
  additions, and deletion;
- stdout/stderr/event/HTTP overflow;
- timeout and partial output persistence;
- artifact atomicity, completeness, hash mismatch, and unknown files;
- evidence verdict cannot be manually overridden;
- diagnostic mode can never become valid.

### 9.2 Windows integration tests

Privileged tests use disposable executables and dynamic filters to prove:

- loopback Ollama-port traffic succeeds;
- another loopback port fails;
- public IPv4, public IPv6, DNS, and configured proxy traffic fail;
- filters disappear after normal completion and forced runner termination;
- a spawned child is denied by the active-process limit;
- timeout and capture overflow leave no descendant process;
- a non-elevated verified run stops before generation.

These tests run behind an explicit `live_containment` marker and are not
silently skipped in a release-candidate evidence session.

### 9.3 End-to-end acceptance

Before Phase 0 can close:

1. all focused and repository tests pass;
2. a clean Windows packaged build passes privileged containment tests;
3. dry-run reports every control ready and the exact installed identities;
4. the operator approves the displayed maximum runtime: nine bounded arm runs;
5. one verified `python-empty-mean` evaluation completes with no downloads;
6. all files match `evidence.json`;
7. independent review confirms scores match captured terminal responses;
8. the report is described only as a single-machine smoke baseline.

## 10. Delivery slices

Implementation is ordered to keep every slice independently testable:

1. Evidence verdict, schemas, diagnostic/verified mode, and atomic ledger.
2. Immutable runtime/model identity and postflight drift detection.
3. Fixture manifest, materialization seal, and mutation verification.
4. Bounded HTTP/process capture.
5. Windows Job Object process containment.
6. Windows WFP network containment and privileged adversarial tests.
7. Task schema v2, deterministic schedule, and three-trial orchestration.
8. Packaged Windows end-to-end verification and the bounded live baseline.

Phase 1 harness work starts only after slice 8 evidence is reviewed.

## 11. Research basis

- Microsoft documents Job Objects as the Windows primitive for managing a
  process tree and confirms `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` terminates
  associated processes:
  <https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects>
- Microsoft documents Windows Filtering Platform application- and
  connection-scoped filtering, and ALE outbound connection authorization:
  <https://learn.microsoft.com/en-us/windows/win32/fwp/about-windows-filtering-platform>
  and
  <https://learn.microsoft.com/en-us/windows/win32/fwp/application-layer-enforcement--ale->
- Ollama's model-list API exposes full model digests and its show API exposes
  model parameters and lineage metadata:
  <https://docs.ollama.com/api/tags> and
  <https://docs.ollama.com/api-reference/show-model-details>
- Ollama's chat API accepts bounded runtime generation options:
  <https://docs.ollama.com/api/chat>
- OpenCode documents automatic updates, merged configuration, provider
  allowlisting, disabled sharing, provider options, and locally installed
  plugin dependencies:
  <https://opencode.ai/docs/config>,
  <https://opencode.ai/docs/providers>, and
  <https://opencode.ai/config.json>

## 12. Acceptance criteria

This design is complete when:

- every one of the seven original evidence blockers maps to an implemented,
  machine-verifiable control;
- verified mode fails before generation when any required control is absent;
- diagnostic mode remains useful but unambiguously invalid;
- no network policy, process, or temporary privilege state survives a run;
- the three arms receive equivalent fixture bytes and declared generation
  settings across a counterbalanced schedule;
- final evidence is complete, hash-sealed, reproducible, and independently
  inspectable;
- public wording remains scoped to the exact task/model/runtime/hardware tuple.
