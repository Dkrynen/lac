# LAC Local Agent Public-Readiness Design

**Date:** 2026-07-25
**Status:** Approved direction; implementation phased behind evidence gates
**Product:** LAC — local-model-first coding agent and hardware control plane

## 1. Product boundary

LAC is not a new editor and does not fork a terminal agent by default. LAC owns
the hardware intelligence, model-harness profiles, task state, security policy,
evaluation evidence, and packaging. OpenCode is the first replaceable terminal
runtime. Ollama is the first inference runtime.

The public promise is:

> LAC extracts the maximum verified agent capability from each local model and
> machine.

LAC must not promise that every small model equals Claude or Codex. Capability
claims are reported for a measured `model + harness + runtime + hardware`
configuration.

## 2. Architecture

```text
LAC CLI / thin VS Code surface
              |
              v
LAC control plane
  - hardware and runtime profiler
  - model-harness profile selector
  - deterministic task controller
  - durable task and evidence ledger
  - context and compaction manager
  - recovery and circuit breakers
  - evaluation and tracing
              |
              v
LAC security boundary
  - process/workspace sandbox
  - fail-closed capability broker
  - path/command/network policy
  - parameter-bound approvals
  - secret and external-action isolation
              |
              v
Replaceable runtime adapter
  - OpenCode first
  - version-pinned compatibility contract
              |
              v
Local inference
  - Ollama first
  - future OpenAI-compatible local providers
```

## 3. Public-readiness invariants

### 3.1 Hardware truth

- Dedicated accelerator memory and shared system memory are never presented as
  equivalent.
- An integrated GPU may be displayed as detected hardware while remaining
  ineligible for model splitting.
- Shared iGPU memory contributes to model-fit capacity only after a bounded
  runtime probe verifies that the active runtime can load and execute the
  target split on the exact machine.
- Unknown adapter types fail closed and do not contribute model-fit capacity.
- A failed, missing, stale, or incompatible probe fails closed to the verified
  discrete GPU and system-RAM tiers.
- Recommendation output distinguishes reported memory, verified usable memory,
  estimated throughput, and measured throughput.
- Runtime evidence outranks vendor-name inference and static compatibility
  tables.

### 3.2 Runtime security

- Generated OpenCode configuration starts with `* = ask`.
- Read-only project inspection may be allowed without approval.
- Secret-shaped files are denied even when ordinary project reads are allowed.
- Writes, shell execution, and network access require approval until they are
  routed through the LAC capability broker.
- External-directory access is denied.
- Subagent dispatch is denied until LAC has version-pinned tests proving
  transitive permission enforcement and recursion limits.
- The first compatibility target is exactly OpenCode `1.18.4`; untested or
  unverifiable versions are rejected before launch.
- OpenCode permissions are defense in depth, not the final authorization
  boundary. The later LAC broker and process sandbox remain authoritative.

### 3.3 Harness behavior

- The default is one adaptive agent, not a swarm.
- Every supported local model has an evidence-backed harness profile containing
  context budget, output/reasoning budget, tool-schema budget, repair policy,
  compaction policy, maximum steps, and allowed agent roles.
- Tool output is structured and bounded. Large results are stored as artifacts
  and referenced rather than injected in full.
- Task state survives compaction and records goal, current plan, completed
  checks, changed files, approvals, failures, and exact next action.
- Retry, token, tool-chain, elapsed-time, and recursion budgets are bounded.
- A model that cannot reliably satisfy a task contract degrades to a simpler
  workflow, another installed model, or a truthful human handoff.

### 3.4 Evaluation

Every release candidate compares the same model and task under:

1. Raw local model.
2. Stock OpenCode.
3. LAC harness over OpenCode.

Reference Claude or Codex runs are optional, separately budgeted, and require
approval before paid evaluation exceeds the AIOS money rail.

Required metrics:

- task Pass@1;
- repair success;
- malformed or incorrect tool calls;
- context overflows and compactions;
- wall time and generated tokens;
- unsafe actions blocked;
- approval prompts and human interventions;
- runtime residency and measured throughput.

No public capability statement may be based only on unit tests, parameter
count, advertised context size, or a single successful demonstration.

## 4. Runtime reuse and provenance

- OpenCode remains an external, pinned dependency behind a LAC adapter.
- Upstream code is imported only when the license permits it and the benefit is
  greater than maintaining an adapter.
- Imported files record source repository, commit, license, and local
  modifications.
- Public packaging includes third-party notices, a dependency lock, an SBOM,
  and a compatibility matrix.
- LAC branding does not imply endorsement by OpenCode, Anthropic, OpenAI,
  Moonshot AI, or other upstream projects.
- Proprietary Claude Code implementation is not copied. Public behavior and
  published architecture patterns may be reproduced independently.

## 5. Phased delivery

### Phase 0 — Truth and containment

- Exclude unverified shared iGPU memory from fit calculations.
- Generate fail-closed OpenCode permissions.
- Pin and report the OpenCode compatibility target.
- Establish raw-model and stock-OpenCode baseline tasks.

### Phase 1 — Harness core

- Add model-harness profiles.
- Add bounded structured-output and tool-call repair.
- Add durable task/evidence state and deterministic compaction summaries.
- Add stuck-loop detection and circuit breakers.

### Phase 2 — LAC capability broker

- Replace state-changing runtime tools with LAC-scoped tools.
- Add path-, command-, network-, and parameter-bound approvals.
- Add process/workspace sandboxing and adversarial security regression tests.

### Phase 3 — Adaptive roles

- Add read-only explorer and reviewer roles.
- Dispatch only when task decomposition and evaluation justify the overhead.
- Enforce role-specific context, tool, recursion, and permission budgets.

### Phase 4 — Product surfaces and packaging

- Preserve the CLI as the primary interface.
- Add a thin LAC VS Code surface over the runtime/server adapter.
- Add installer/runtime dependency handling, provenance artifacts, diagnostics,
  release gates, and upgrade/rollback compatibility checks.

### Phase 5 — Competitive evidence

- Run a stable local golden suite and repository-level task suite.
- Publish model-harness-hardware compatibility results with reproducible
  settings.
- Run larger external benchmark subsets only after cost and runtime approval.

## 6. Phase 0 acceptance

Phase 0 is complete only when:

- unverified integrated GPUs do not increase `combined_vram_gb` or appear in a
  split plan;
- unknown adapters do not become trusted discrete capacity;
- an explicitly verified integrated GPU can still participate in the existing
  split planner;
- API masking and manual VRAM overrides cannot restore unverified capacity;
- API and CLI output expose whether integrated capacity is split-verified;
- generated OpenCode configuration denies external directories and subagents,
  denies root and nested secret-shaped reads, and asks before grep, writes,
  shell, or network use;
- launch rejects any OpenCode version other than `1.18.4`;
- focused and full automated suites pass;
- the exact OpenCode version and remaining runtime-security limitations are
  reported truthfully.
