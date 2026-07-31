# Phase 3 — Proven Agent Profiles + Tweakability (Design / Scope)

**Date:** 2026-08-01 · **Owner:** Duan Krynen · **Status:** Scope — pending owner review
**Repo:** `C:\Users\User\repos\model-hub` (GitHub `Dkrynen/lac`)
**Origin:** Pivot plan Phase 3 (`/.omo/plans/2026-07-30-lac-opencode-pivot.md`), items 3–4.

## 1. Overview

Two deliverables that make LAC's agent recommendations *evidence-backed* and *user-controllable*:

- **Proven agent profiles (recipe cards):** eval-backed cards per hardware class — *"proven
  94 tok/s on a 16 GB AMD rig."* `agent_eval` produces the evidence; LAC ships the recipes
  as badges surfaced in `recommend`/`lac agent`.
- **Real tweakability:** the "can't tweak anything" fix — editable agent profiles, an
  offload-config editor, per-project profiles, and context-floor control.

### Build order (recommended)

Recipe cards first (self-contained, on the "evidence not vibes" brand, no web work), then
tweakability items as independent tasks. Each gets its own TDD plan.

## 2. Proven agent profiles (recipe cards)

### 2.1 Evidence source

`backend/agent_eval/result.py::ArmResult` is the eval contract:
`arm`, `model`, `runtime`, `completed`, `timed_out`, `wall_time_ms`, `metrics: dict`,
`exit_code`. Throughput is computed into `metrics["tokens_per_second"]`
(`raw_ollama.py`). **Important:** `ledger.py` is a hash-sealing *integrity* system
(`evidence.json`), NOT a queryable results store — runs persist as file artifacts
(per-arm `prompt.txt`/`stdout.log`/`stderr.log` under a run directory), and there is
**no existing aggregated, queryable results index.**

**Consequence:** recipe cards need a thin **results-aggregation layer** that scans eval
run directories, extracts `ArmResult` model/runtime/`tokens_per_second`/pass-fail, and
indexes them by (model, hardware class). This is new code (a `results_store`), not a read
of an existing store — and it is the real build cost of this deliverable. The aggregation
must verify each run's `evidence.json` seal (via `ledger.verify_evidence`) before trusting
its numbers, so a card is only ever projected from sealed, valid evidence.

### 2.2 Recipe card schema

```python
@dataclass(frozen=True)
class RecipeCard:
    model_id: str          # e.g. "gpt-oss:20b"
    hardware_class: str    # canonical class, e.g. "amd-16gb"
    tokens_per_second: float
    quant: str             # e.g. "Q4_K_M"
    context: int           # proven num_ctx
    trials: int            # number of passing eval runs behind this card
    evidence_run: str      # ledger run id (provenance)
    use_case: str = "agent"
```

A card exists **only** when `trials >= N` passing eval runs agree (N configurable, default 3
— matching the bounded-eval convention). No evidence, no card — the badge is never asserted
without a ledger receipt behind it.

### 2.3 Hardware-class taxonomy

Canonical classes derived from `detect()` output (GPU name + VRAM tier), e.g.
`amd-16gb`, `amd-24gb`, `nvidia-16gb`, `apple-32gb`. A small classifier maps a `SystemInfo`
to a class id; the same classifier indexes cards and queries them, so a recommendation
matches the user's actual class.

### 2.4 Badge + surfacing

- `recommend()` / `fit_verdict` path: when a recommended model has a card for the user's
  hardware class, attach `proven: {tok_s, quant, trials}` to `Recommendation.details`.
- CLI `lac recommend` / `lac agent`: render a badge — `proven 94 tok/s @ Q4 (3 trials)`.
- API `/api/recommend`: include the `proven` block.
- New module: `backend/agent_eval/recipes.py` (card projection + classifier + lookup),
  tested against synthetic ledger fixtures.

### 2.5 Testing

- Card projection: ledger with ≥N passing runs → card; <N → no card.
- Classifier: representative `SystemInfo`s → expected class ids.
- Lookup: recommendation for a carded model+class carries the `proven` block; uncarded → absent.
- Provenance: card's `evidence_run` resolves to a real ledger entry.

## 3. Real tweakability (independent tasks)

| Item | What | Files (likely) | Size |
|---|---|---|---|
| **3a Context-floor control** | `lac agent --context 96k` with honest budget reporting (Ollama truncates at num_ctx/2; state the real prompt budget) | `agent_launch/launcher.py`, `cli.py` | S |
| **3b Per-project profiles** | `.opencode/` templates LAC manages — per-project model + permission profiles | `agent_launch/`, new project-profile module | M |
| **3c Editable agent profiles** | `lac agent --customize` opens the profile markdown; `lac setup` detects user edits + skips (never clobbers) | `agent_launch/`, `global_setup.py` | M |
| **3d Offload config editor** | web Pro cockpit: tune results → editable → apply | `web/src/components/pro/`, `backend/api.py` | M (web) |

3a–3c are CLI/agent-side; 3d is web (Pro cockpit). Each is independently shippable.

## 4. Out of scope

- Pro tuning-as-a-service autopilot triggers (pivot plan Phase 3 item 2) — separate, Pro-gated.
- New eval arms / benchmark methodology — recipe cards *consume* existing evidence only.
- Cloud recipe sharing — cards are local projections of local evidence.

## 5. Risks

| Risk | Mitigation |
|---|---|
| A badge asserts performance LAC hasn't measured | Card requires ≥N passing trials with a ledger receipt; no evidence → no badge |
| Hardware-class taxonomy too coarse/fine | Start with GPU-vendor + VRAM-tier classes; refine from real eval coverage |
| `--customize` edits get clobbered by `lac setup` | Detect user modification (hash compare) + skip, matching the existing no-clobber profile behavior |
| Recipe staleness as models/calibration change | Cards carry `evidence_run`; re-project on new ledger entries |
