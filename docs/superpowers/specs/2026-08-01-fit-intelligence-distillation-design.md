# Fit Intelligence: Make Any Model Fit Your Hardware

**Date:** 2026-08-01 · **Owner:** Duan Krynen · **Status:** Design — pending owner review
**Repo:** `C:\Users\User\repos\model-hub` (GitHub `Dkrynen/lac`)
**Origin:** Duan-raised feature — "make bigger models smaller to fit your hardware."

## 1. Overview

LAC's moat is that it knows your exact hardware (the `detect()` scan → VRAM/RAM/compute
tiers). Today, when a model you want doesn't fit, `recommend()` **silently drops it**
(`_compute_split_plan` returns `None` for "too_big") and the only "lighter option" logic
is `launcher.py` showing the next three ranked models. There is no intelligence that says
*here is how to get as close as possible to the model you wanted, on the rig you have.*

This feature is that intelligence — **fit intelligence** — delivered as three layers on
one shared verdict:

- **Layer 1 — quantization path:** "It fits at Q3 — here is the honest quality cost."
- **Layer 2 — distill routing:** "It won't fit, but its 8B distill does — ~90% of the
  reasoning at 1/9 the size."
- **Layer 3 — quantization tooling:** "Download it and I will quantize you a copy that fits."

### Locked decisions (Duan, 2026-08-01 — do not re-litigate)

1. **Scope = all three layers.**
2. **Layer 3 mechanism = quantization tooling** (wrap llama.cpp `quantize`) — NOT a
   training/distillation pipeline. No GPU cluster, no knowledge distillation at home.
3. **Lineage data = hybrid:** curated `distill_of`/`family` in the catalog (verified) +
   auto-derived HuggingFace `base_model` at runtime as an **unverified** fallback.
4. **Approach = unified fit-core, phased delivery:** Layers 1+2 ship as one buildable,
   read-only spec; Layer 3 is a follow-on subsystem (model production) with its own gate.

### Non-goals (YAGNI)

- Knowledge-distillation training pipelines (teacher→student fine-tuning). Out this cycle.
- Cloud-hosted or cloud-only models (e.g. `glm-5.1:cloud`). LAC is local-first.
- Reinventing quantization. LAC orchestrates + verifies llama.cpp; it does not re-implement it.
- A GUI quantization cockpit. CLI first; web surface is later if demanded.

## 2. Architecture — one `fit_verdict` core

A single function classifies any model against the user's hardware and returns one verdict
that all three layers consume. This unifies the fit logic already scattered across
`recommend()` (`_estimate_vram`, `best_fit_quant`, `_compute_split_plan`'s None-for-too-big)
rather than bolting on three disjoint features.

```python
def fit_verdict(model: ModelEntry, info: SystemInfo) -> FitVerdict:
    """Classify a model against the user's hardware."""
```

`FitVerdict` is one of:

| Verdict | Meaning | Layer that acts |
|---|---|---|
| `fits` | Fits at default Q4_K_M | (recommend as-is) |
| `fits_at_quant(q)` | Fits only at quant `q` (< Q4) | Layer 1 — state quality cost |
| `distill_available(m')` | Doesn't fit; relative `m'` does | Layer 2 — route to `m'` |
| `quantize_to_fit(q)` | Doesn't fit; quantizing a copy would | Layer 3 — offer `lac quantize` |
| `too_big` | Nothing helps on this hardware | Honest "won't fit here" |

Verdict precedence when several apply: `fits` > `fits_at_quant` > `distill_available` >
`quantize_to_fit` > `too_big`. (A model that fits at a lower quant is a better answer than
routing to a different model; routing to a verified distill is better than asking the user
to quantize.)

**High-quality-cost override:** when `fits_at_quant` lands at a punishing quant
(quality cost ≥ 10 pts, i.e. Q3_K_M or below), the verdict is still `fits_at_quant`, but the
UI surfaces the distill/quantize alternative *alongside* it rather than only the low-quant
option — a model that "fits" only at Q2 with half its quality is rarely what the user wanted.
The user chooses; LAC presents both with honest tradeoffs.

## 3. Layer 1 — honest quantization quality-cost

**State:** mostly built. `recommend()` already computes
`quality = _quality_base(params) + MODEL_FAMILY_QUALITY_BONUS[arch] + quant.quality_penalty`.
The quant penalty is real but invisible to the user.

**Change:** surface it.
- Add `quant_quality_cost: float` to `Recommendation.details` — the magnitude of
  `quant.quality_penalty` for the chosen quant (0 for F16).
- Add a human-readable note, e.g. *"Fits at Q3_K_M — ~8 quality pts below F16."*
- `print_recommendations` (CLI) shows the note when the chosen quant is below Q4.

**Files:** `backend/cookbook/recommend.py`, `cli.py`.
**No new data; no schema change.**

## 4. Layer 2 — distill routing (the differentiator)

### 4.1 Lineage data (hybrid)

**Curated (verified):** add optional fields to `generate_models.py`:
- `distill_of: str` — the base/teacher model id this is a distilled variant of.
- `family: str` — groups variants that are not strict distills (e.g. same base, different
  tuning) so routing can offer cousins.

Seed with well-established, verifiable relationships only:
- DeepSeek-R1 distills → their bases: `deepseek-r1:7b distill_of qwen2.5:7b`,
  `deepseek-r1:8b distill_of llama3.1:8b`, `deepseek-r1:14b distill_of qwen2.5:14b`,
  `deepseek-r1:32b distill_of qwen2.5:32b`, `deepseek-r1:70b distill_of llama3.3:70b`.
- `gpt-oss:20b` / `gpt-oss:120b` → `family: gpt-oss`.
- (Extend conservatively; each relationship must be verifiable, matching LAC's data brand.)

**Runtime fallback (unverified):** for models the user pulls that are NOT in the curated
catalog, read HuggingFace `base_model` metadata — `api.py:3888` (`_hf_base_model`) already
parses this. Any relationship derived this way is tagged `verified: false`.

**Brand invariant:** the unverified path NEVER asserts certainty. Unverified relationships
are framed as *"likely related (from upstream metadata)"*, never *"is a distill of"*. A
dedicated test enforces this.

### 4.2 Routing

```python
def recommend_distill(info: SystemInfo, model_id: str) -> DistillSuggestion | None:
    """If model_id doesn't fit, find the best-fitting verified relative and return it
    with the relationship + quality tradeoff stated. Returns None if no relative fits."""
```

Behavior:
1. Compute `fit_verdict(target, info)`. If it fits, return `None` (no routing needed).
2. Build the target's relative set from curated `distill_of`/`family` (verified) first;
   add HF `base_model` relatives (unverified) only if no verified relative fits.
3. Pick the best-fitting relative (highest `recommend()` score that fits).
4. Return a `DistillSuggestion(related_model, relationship, verified, quality_note)`.

Example output: *"Llama-3.3-70B won't fit your 16 GB. DeepSeek-R1-14B (a reasoning distill
in the same class) fits at Q4 — strong reasoning at ~1/5 the size."*

**Integration:** `cmd_recommend` (cli.py) and `api_recommend` (api.py) — when a requested
model doesn't fit, surface the suggestion alongside the standard top-K.

**Files:** `generate_models.py` (lineage fields + curated seeds), `recommend.py`
(`recommend_distill`, `DistillSuggestion`), `cli.py`, `api.py`.

## 5. Layer 3 — `lac quantize` (follow-on subsystem)

```
lac quantize <model> [--vram N | --auto]
```

Behavior:
1. Resolve the installed source model (FP16 or best available GGUF). Gated on the source
   being installed (`ollama pull` first — same precondition as `ensure_agent_variant`).
2. Compute the target quant from the user's VRAM via `best_fit_quant` (reuse, don't
   reinvent). `--auto` uses `detect()`; `--vram N` overrides.
3. Shell out to **llama.cpp `quantize`** (or the Ollama-native equivalent if/when available).
   LAC orchestrates + verifies the output; it does not re-implement quantization.
4. Register the output as a local variant (`<model>-q3-fit`) via the existing custom-models
   path (`register_custom_model`).

**Honesty:** state the quality cost of the target quant (Layer 1's note) before running.

**Files:** new `backend/cookbook/quantize.py`, `cli.py`.
**This layer is its own spec/PR** — model production has distinct safety (subprocess,
disk space, long-running jobs) and testing concerns.

## 6. Data flow

```
detect() → SystemInfo → fit_verdict(model, vram) →
   ├─ fits_at_quant     → Layer 1: surface quality cost in recommendation
   ├─ distill_available → Layer 2: route to curated relative (verified) / HF base_model (unverified)
   └─ quantize_to_fit   → Layer 3: offer `lac quantize` → local variant
```

## 7. Testing (TDD per the PLUGIN-FIRST mandate)

- **`fit_verdict`:** all 5 verdicts, across representative VRAM tiers (8/16/24/48 GB).
- **Layer 1:** `quant_quality_cost` correctness vs the quant ladder penalties.
- **Layer 2:** routing returns the best-fitting relative; curated (verified) preferred over
  auto (unverified); **unverified-guard test** — auto-derived lineage is never presented as
  a verified fact.
- **Layer 3:** target-quant selection from VRAM; mocked `quantize` subprocess (no real
  model build in tests); variant registration via the custom-models path.

Suite gates: core `pytest -q -m "not live"` stays green; new tests follow the existing
`tests/test_recommend*.py` patterns.

## 8. Build order

1. **Layer 1** → **Layer 2** — one spec/PR. Both recommendation-side, read-only, no model
   production. This is the buildable first deliverable.
2. **Layer 3** — its own spec/PR after 1+2 land. Model-production subsystem with its own
   safety + testing gate.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Auto-derived lineage asserts a false relationship (the glm-4.6/Kimi trap) | `verified` flag + brand invariant + dedicated unverified-guard test |
| Layer 3 depends on llama.cpp being installed | Detect + clear "install llama.cpp" messaging; degrade gracefully |
| Curated lineage goes stale / curation overhead | Curate only well-known families; auto-fallback covers the long tail |
| New `ModelEntry` fields break `ModelEntry(**m)` | Optional fields with defaults (same pattern as `sub4bit`/`new`) |
| Quantized output quality disappoints | Honest quality-cost note (Layer 1) shown before quantizing |

## 10. Out of scope (explicitly)

- Knowledge-distillation training pipelines (teacher→student). Later cycle if ever.
- Cloud-only models. LAC is local-first.
- Quantization GUI cockpit. CLI first.
- Reinventing quantization algorithms. LAC wraps llama.cpp.
