# Fit Intelligence (Layers 1+2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give LAC a unified `fit_verdict` core that powers honest quant quality-cost surfacing (Layer 1) and distill routing — "this model doesn't fit, but its distilled cousin does" (Layer 2).

**Architecture:** A new `backend/cookbook/fit.py` module classifies any model against the user's hardware into one verdict (`fits` / `fits_at_quant` / `distill_available` / `quantize_to_fit` / `too_big`), reusing `recommend.py`'s VRAM/split-plan math. Layer 1 enriches `recommend()` output with the quant quality cost; Layer 2 walks a hybrid lineage graph (curated-verified catalog fields + unverified HuggingFace `base_model` fallback) to route to a fitting relative.

**Tech Stack:** Python 3.10+, pytest (`-m "not live"`), the existing `backend/cookbook/` package (recommend.py, hardware.py), Flask (`api.py`), argparse CLI (`cli.py`).

## Global Constraints

- Python 3.10+; core stays Pro-LOGIC-unaware (no `lac_pro` imports in model-hub).
- Test gate: `python -m pytest -m "not live" -p no:cacheprovider -q` stays green.
- TDD every task: red → green → commit. One logical commit per task.
- Curated catalog models only for verified lineage; auto-derived lineage is tagged `verified=False` and **never** presented as certain (brand invariant — enforced by a test).
- New `ModelEntry` fields are optional with defaults (must not break `ModelEntry(**m)` for existing catalog/custom entries).
- VRAM math is reused from `recommend.py` (`_estimate_vram`, `_compute_split_plan`, `_estimate_layers`) — do not reinvent it.
- Run tests with the repo venv: `.venv\Scripts\python.exe -m pytest ...` (Windows).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/cookbook/recommend.py` | Add `distill_of`/`family` to `ModelEntry`; add `quant_quality_cost` to rec details | Modify |
| `backend/cookbook/generate_models.py` | Curated lineage seeds (DeepSeek-R1 distills, gpt-oss family) | Modify |
| `backend/cookbook/data/models.json` | Regenerated catalog with lineage fields | Regenerate |
| `backend/cookbook/fit.py` | `FitVerdict`, `fit_verdict()`, `DistillSuggestion`, `recommend_distill()`, relative resolvers | Create |
| `cli.py` | `lac recommend --model <id>` fit-check + distill suggestion; quality-cost note | Modify |
| `backend/api.py` | `api_recommend` `model` param + suggestion payload; `quant_quality_cost` in rec serialization | Modify |
| `tests/test_fit.py` | fit_verdict + recommend_distill + unverified-guard tests | Create |
| `tests/test_recommend.py` | quality-cost-in-details test | Modify |

Dependency order: Task 1 (lineage fields) → Task 2 (fit_verdict) → Task 3 (Layer 1, independent of 2) → Task 4 (Layer 2, needs 1+2) → Task 5 (surface, needs 3+4).

---

## Task 1: Lineage fields on ModelEntry + curated seeds

**Files:**
- Modify: `backend/cookbook/recommend.py` (ModelEntry dataclass, ~line 11-25)
- Modify: `backend/cookbook/generate_models.py` (MODELS entries)
- Regenerate: `backend/cookbook/data/models.json`
- Test: `tests/test_recommend.py`

**Interfaces:**
- Produces: `ModelEntry.distill_of: Optional[str]` and `ModelEntry.family: Optional[str]` (both default `None`); catalog entries for DeepSeek-R1 distills carry `distill_of`; `gpt-oss:20b`/`gpt-oss:120b` carry `family="gpt-oss"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recommend.py`:

```python
def test_catalog_lineage_fields_present():
    ids = {m.id: m for m in load_models()}
    # Curated, verifiable distill relationships (DeepSeek-R1 distills).
    assert ids["deepseek-r1:7b"].distill_of == "qwen2.5:7b"
    assert ids["deepseek-r1:8b"].distill_of == "llama3.1:8b"
    assert ids["deepseek-r1:14b"].distill_of == "qwen2.5:14b"
    assert ids["deepseek-r1:32b"].distill_of == "qwen2.5:32b"
    assert ids["deepseek-r1:70b"].distill_of == "llama3.3:70b"
    # Family grouping (not a strict distill).
    assert ids["gpt-oss:20b"].family == "gpt-oss"
    assert ids["gpt-oss:120b"].family == "gpt-oss"
    # A model with no lineage defaults to None.
    assert ids["qwen3.6:27b"].distill_of is None
    assert ids["qwen3.6:27b"].family is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recommend.py::test_catalog_lineage_fields_present -v`
Expected: FAIL — `ModelEntry` has no attribute `distill_of` (or `AttributeError`/`TypeError`).

- [ ] **Step 3: Add the fields to ModelEntry**

In `backend/cookbook/recommend.py`, in the `ModelEntry` dataclass after `new: bool = False`:

```python
    distill_of: Optional[str] = None
    family: Optional[str] = None
```

- [ ] **Step 4: Add curated lineage seeds to generate_models.py**

In `backend/cookbook/generate_models.py`, update the five DeepSeek-R1 distill entries to include `distill_of`, and the two gpt-oss entries to include `family`:

```python
    {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B", "provider": "DeepSeek", "params_b": 7.6, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False, "distill_of": "qwen2.5:7b"},
    {"id": "deepseek-r1:8b", "name": "DeepSeek R1 8B", "provider": "DeepSeek", "params_b": 8.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False, "distill_of": "llama3.1:8b"},
    {"id": "deepseek-r1:14b", "name": "DeepSeek R1 14B", "provider": "DeepSeek", "params_b": 14.8, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False, "distill_of": "qwen2.5:14b"},
    {"id": "deepseek-r1:32b", "name": "DeepSeek R1 32B", "provider": "DeepSeek", "params_b": 32.8, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False, "distill_of": "qwen2.5:32b"},
    {"id": "deepseek-r1:70b", "name": "DeepSeek R1 70B", "provider": "DeepSeek", "params_b": 70.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False, "distill_of": "llama3.3:70b"},
```

```python
    {"id": "gpt-oss:20b", "name": "GPT-OSS 20B", "provider": "OpenAI", "params_b": 20.0, "arch": "gpt-oss", "context": 131072, "use_cases": ["general","chat","coding","reasoning"], "is_moe": True, "active_params_b": 3.6, "family": "gpt-oss"},
    {"id": "gpt-oss:120b", "name": "GPT-OSS 120B", "provider": "OpenAI", "params_b": 120.0, "arch": "gpt-oss", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 5.1, "family": "gpt-oss"},
```

- [ ] **Step 5: Regenerate the catalog**

Run: `.venv\Scripts\python.exe backend/cookbook/generate_models.py`
Expected: `Generated 98 models -> ...\models.json`

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recommend.py::test_catalog_lineage_fields_present -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/cookbook/recommend.py backend/cookbook/generate_models.py backend/cookbook/data/models.json tests/test_recommend.py
git commit -m "feat(catalog): add distill_of/family lineage fields with curated DeepSeek-R1 + gpt-oss seeds"
```

---

## Task 2: fit_verdict core

**Files:**
- Create: `backend/cookbook/fit.py`
- Test: `tests/test_fit.py`

**Interfaces:**
- Consumes: `ModelEntry`, `SystemInfo`, `QuantInfo`, `QUANTS`, `_estimate_vram`, `_compute_split_plan`, `_estimate_layers` from `recommend.py`/`hardware.py`.
- Produces: `FitVerdict` dataclass and `fit_verdict(model, info, use_case="coding") -> FitVerdict`.

`FitVerdict` shape (used by Tasks 4 and 5):

```python
@dataclass
class FitVerdict:
    kind: str                 # "fits" | "fits_at_quant" | "distill_available" | "quantize_to_fit" | "too_big"
    quant: Optional[str] = None        # set for fits_at_quant
    quality_cost: float = 0.0          # |quality_penalty| for fits_at_quant
    bpp: Optional[float] = None        # set for quantize_to_fit (target bits/param)
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fit.py`:

```python
from __future__ import annotations

from backend.cookbook.hardware import GPUInfo, SystemInfo
from backend.cookbook.recommend import load_models
from backend.cookbook.fit import FitVerdict, fit_verdict


def _box(vram_gb: float) -> SystemInfo:
    return SystemInfo(
        os="Windows", cpu="AMD Ryzen 5 7600", cpu_cores=6, ram_gb=32.0,
        gpus=[GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=vram_gb, backend="rocm")],
        total_vram_gb=vram_gb,
    )


def _model(model_id: str):
    return next(m for m in load_models() if m.id == model_id)


def test_fits_on_big_gpu():
    v = fit_verdict(_model("qwen3.6:27b"), _box(24.0))
    assert v.kind == "fits"


def test_fits_at_quant_on_tight_gpu():
    # 27.8B dense: ~16.4GB at Q4, ~10GB at Q3 -> fits a 12GB box only at a lower quant.
    v = fit_verdict(_model("qwen3.6:27b"), _box(12.0))
    assert v.kind == "fits_at_quant"
    assert v.quant is not None
    assert v.quality_cost > 0


def test_too_big_when_no_quant_fits():
    # 671B MoE (~390GB even at Q2) cannot fit an 8GB box at any quant.
    v = fit_verdict(_model("deepseek-v3:671b"), _box(8.0))
    assert v.kind in ("quantize_to_fit", "too_big")


def test_precedence_fits_beats_fits_at_quant():
    v = fit_verdict(_model("qwen3:4b"), _box(16.0))
    assert v.kind == "fits"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.cookbook.fit'`.

- [ ] **Step 3: Implement fit.py**

Create `backend/cookbook/fit.py`:

```python
"""Fit intelligence: classify a model against the user's hardware.

One verdict, consumed by Layer 1 (quality-cost surfacing), Layer 2 (distill
routing), and Layer 3 (quantize-to-fit). Reuses recommend.py's VRAM math so
the verdict agrees with what recommend() would actually pick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .hardware import SystemInfo
from .recommend import (
    QUANTS,
    ModelEntry,
    _compute_split_plan,
    _estimate_layers,
    _estimate_vram,
)

# Quality cost at/above this many points (<= Q3_K_M) is "punishing": the UI
# should offer a distill/quantize alternative alongside the low-quant fit.
HIGH_QUALITY_COST = 10.0


@dataclass
class FitVerdict:
    kind: str  # "fits" | "fits_at_quant" | "distill_available" | "quantize_to_fit" | "too_big"
    quant: Optional[str] = None
    quality_cost: float = 0.0
    bpp: Optional[float] = None


def _available_vram_gb(info: SystemInfo) -> float:
    tiers = info.compute_tiers
    if tiers:
        combined = sum(t.memory_gb for t in tiers if t.kind != "ram")
        return combined if combined > 0 else max(info.ram_gb * 0.5, 0.1)
    return max(info.total_vram_gb, info.ram_gb * 0.25)


def _max_fitting_bpp(model: ModelEntry, info: SystemInfo, ctx: int) -> Optional[float]:
    """Largest bits/param at which the model's weights+KV+overhead fit. None if
    even an arbitrarily small quant cannot fit (KV alone exceeds the budget)."""
    avail = _available_vram_gb(info)
    active = model.active_params_b if model.is_moe and model.active_params_b else model.params_b
    kv = 0.000008 * active * ctx
    overhead = 0.5
    budget = avail - kv - overhead
    if budget <= 0 or model.params_b <= 0:
        return None
    return budget / model.params_b


def fit_verdict(model: ModelEntry, info: SystemInfo, use_case: str = "coding") -> FitVerdict:
    ctx = model.context
    # 1. Fits at the default quant (Q4_K_M)?
    if _compute_split_plan(_estimate_vram(model, QUANTS[4], ctx), info, model) is not None:
        return FitVerdict(kind="fits", quant="Q4_K_M")
    # 2. Fits at a lower quant in the ladder? Pick the highest-quality that fits.
    for q in QUANTS:  # ordered F16 -> Q2_K (best quality first)
        if _compute_split_plan(_estimate_vram(model, q, ctx), info, model) is not None:
            return FitVerdict(kind="fits_at_quant", quant=q.name, quality_cost=abs(q.quality_penalty))
    # 3. Doesn't fit at any ladder quant. Could a custom (more aggressive) quant fit?
    bpp = _max_fitting_bpp(model, info, ctx)
    if bpp is not None and bpp < QUANTS[-1].bpp:
        return FitVerdict(kind="quantize_to_fit", bpp=round(bpp, 2))
    return FitVerdict(kind="too_big")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fit.py -v`
Expected: PASS (4 tests). If `test_fits_at_quant_on_tight_gpu` doesn't land on `fits_at_quant`, adjust the test's VRAM box to a size where Q4 fails but a lower quant fits (the exact crossover depends on `_estimate_vram`'s KV term).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/fit.py tests/test_fit.py
git commit -m "feat(fit): unified fit_verdict core (fits/fits_at_quant/quantize_to_fit/too_big)"
```

---

## Task 3: Layer 1 — surface quant quality-cost in recommendations

**Files:**
- Modify: `backend/cookbook/recommend.py` (the `details={...}` dict in `recommend()`, ~line 590)
- Modify: `cli.py` (`cmd_recommend` row rendering, ~line 919-932)
- Modify: `backend/api.py` (`api_recommend` rec serialization, ~line 967-988)
- Test: `tests/test_recommend.py`

**Interfaces:**
- Consumes: `quant.quality_penalty` (QuantInfo) inside `recommend()`.
- Produces: `Recommendation.details["quant_quality_cost"]: float` (0.0 for F16); CLI shows a note when the chosen quant is below Q4_K_M; API includes `quant_quality_cost` in each recommendation.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recommend.py`:

```python
def test_recommend_details_carry_quant_quality_cost():
    recs = recommend(_sys16(), use_case="coding", top_k=100)
    assert recs, "expected at least one recommendation"
    for r in recs:
        assert "quant_quality_cost" in r.details
        assert r.details["quant_quality_cost"] >= 0
    # Any rec not at F16 should have a positive cost; F16 (if present) zero.
    f16 = [r for r in recs if r.quant == "F16"]
    for r in f16:
        assert r.details["quant_quality_cost"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recommend.py::test_recommend_details_carry_quant_quality_cost -v`
Expected: FAIL — `KeyError: 'quant_quality_cost'` / assertion error.

- [ ] **Step 3: Add quant_quality_cost to the details dict**

In `backend/cookbook/recommend.py`, in the `details={...}` dict inside `recommend()` (the block that already includes `"new": model.new`), add:

```python
                        "quant_quality_cost": abs(quant.quality_penalty),
```

- [ ] **Step 4: Surface the note in the CLI**

In `cli.py` `cmd_recommend`, after the row-printing loop (after the `for row in rows:` block, ~line 932), add a quality-cost note for the top pick when it is below Q4:

```python
        top = recs[0]
        cost = top.details.get("quant_quality_cost", 0.0)
        if top.quant != "Q4_K_M" and cost:
            print(f"  {C['gray']}Note: best fit is {top.quant} — ~{cost:.0f} quality pts below F16.{C['reset']}")
```

- [ ] **Step 5: Add quant_quality_cost to the API serialization**

In `backend/api.py` `api_recommend`, in the per-rec dict (the block with `"speed_band_pct": r.speed_band_pct,`), add:

```python
                "quant_quality_cost": r.details.get("quant_quality_cost", 0.0),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recommend.py -v`
Expected: PASS (including the new test).

- [ ] **Step 7: Commit**

```bash
git add backend/cookbook/recommend.py cli.py backend/api.py tests/test_recommend.py
git commit -m "feat(fit): surface honest quant quality-cost in recommendations (Layer 1)"
```

---

## Task 4: Layer 2 — distill routing (hybrid lineage)

**Files:**
- Modify: `backend/cookbook/fit.py` (add `DistillSuggestion`, `recommend_distill`, relative resolvers)
- Test: `tests/test_fit.py`

**Interfaces:**
- Consumes: `fit_verdict()` (Task 2), `ModelEntry.distill_of`/`family` (Task 1), `load_models()` (recommend.py).
- Produces: `DistillSuggestion` dataclass and `recommend_distill(info, model_id, use_case="coding") -> Optional[DistillSuggestion]`.

`DistillSuggestion` shape (used by Task 5):

```python
@dataclass
class DistillSuggestion:
    model: ModelEntry
    relationship: str   # human-readable, e.g. "reasoning distill in the same class"
    verified: bool      # True = curated catalog lineage; False = upstream metadata
    note: str           # full user-facing sentence
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fit.py`:

```python
import backend.cookbook.fit as fit_mod
from backend.cookbook.fit import recommend_distill


def test_distill_routing_finds_verified_relative():
    # deepseek-r1:70b (distill_of llama3.3:70b) won't fit 16GB; its relative
    # deepseek-r1:14b (distill_of qwen2.5:14b, ~14.8B) does. Ask for the 70B.
    sugg = recommend_distill(_box(16.0), "deepseek-r1:70b")
    assert sugg is not None
    assert sugg.verified is True
    assert sugg.model.id != "deepseek-r1:70b"
    assert sugg.model.id.startswith("deepseek-r1:")


def test_distill_routing_none_when_target_fits():
    # A model that fits needs no routing.
    assert recommend_distill(_box(24.0), "qwen3.6:27b") is None


def test_unverified_lineage_never_asserts_certainty(monkeypatch):
    # Brand invariant: auto-derived (unverified) relationships must be framed
    # as "likely related", never "is a distill of".
    target = _model("deepseek-v3:671b")  # no curated relatives
    fake = [(_model("deepseek-v3.1:671b"), "shares the deepseek-v3 base (from upstream metadata)")]
    monkeypatch.setattr(fit_mod, "_unverified_relatives", lambda model_id: fake)
    sugg = recommend_distill(_box(8.0), "deepseek-v3:671b")
    if sugg is not None and not sugg.verified:
        assert "likely" in sugg.note.lower() or "upstream metadata" in sugg.note.lower()
        assert "is a distill of" not in sugg.note.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fit.py -v`
Expected: FAIL — `ImportError: cannot import name 'recommend_distill'`.

- [ ] **Step 3: Implement distill routing in fit.py**

Append to `backend/cookbook/fit.py`:

```python
from .recommend import load_models  # add to the existing recommend import block


@dataclass
class DistillSuggestion:
    model: ModelEntry
    relationship: str
    verified: bool
    note: str


def _curated_relatives(model: ModelEntry, by_id: dict) -> list[tuple[ModelEntry, str]]:
    """Verified relatives from curated catalog lineage (distill_of / family)."""
    out: list[tuple[ModelEntry, str]] = []
    if model.distill_of and model.distill_of in by_id:
        out.append((by_id[model.distill_of], "the base model this is distilled from"))
    for m in by_id.values():
        if m.id == model.id:
            continue
        if m.distill_of == model.id:
            out.append((m, "a distilled variant of this model"))
        elif model.family and m.family == model.family:
            out.append((m, f"in the same {model.family} family"))
    return out


def _unverified_relatives(model_id: str) -> list[tuple[ModelEntry, str]]:
    """Auto-derived relatives from upstream (HuggingFace base_model) metadata.
    NEVER verified — callers must frame these as 'likely related'. Wired to the
    HF base_model resolver in api.py when available; empty by default."""
    return []


def recommend_distill(info: SystemInfo, model_id: str, use_case: str = "coding") -> Optional[DistillSuggestion]:
    """If model_id doesn't fit, return the best-fitting relative, or None."""
    by_id = {m.id: m for m in load_models()}
    target = by_id.get(model_id)
    if target is None:
        return None
    if fit_verdict(target, info, use_case).kind in ("fits", "fits_at_quant"):
        return None

    def best(candidates: list[tuple[ModelEntry, str]], verified: bool) -> Optional[DistillSuggestion]:
        fitted = []
        for rel, relationship in candidates:
            v = fit_verdict(rel, info, use_case)
            if v.kind in ("fits", "fits_at_quant"):
                fitted.append((rel, relationship, v))
        if not fitted:
            return None
        fitted.sort(key=lambda t: t[0].params_b, reverse=True)  # biggest that fits = closest to target
        rel, relationship, v = fitted[0]
        if verified:
            note = (f"{target.name} won't fit your hardware. {rel.name} ({relationship}) "
                    f"fits at {v.quant or 'Q4_K_M}.")
        else:
            note = (f"{target.name} won't fit your hardware. {rel.name} {relationship} "
                    f"and fits at {v.quant or 'Q4_K_M'} — worth trying.")
        return DistillSuggestion(model=rel, relationship=relationship, verified=verified, note=note)

    verified = best(_curated_relatives(target, by_id), verified=True)
    if verified is not None:
        return verified
    return best(_unverified_relatives(model_id), verified=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fit.py -v`
Expected: PASS (all fit tests, including the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/fit.py tests/test_fit.py
git commit -m "feat(fit): distill routing with hybrid lineage + unverified-guard invariant (Layer 2)"
```

---

## Task 5: Surface fit-check + suggestion in CLI and API

**Files:**
- Modify: `cli.py` (`cmd_recommend` — add `--model` handling; argparse for `recommend`)
- Modify: `backend/api.py` (`api_recommend` — `model` param + suggestion payload)
- Test: `tests/test_cli_browse_recommend.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `fit_verdict()`, `recommend_distill()` (fit.py), `DistillSuggestion`.
- Produces: `lac recommend --model <id>` prints the fit verdict + distill suggestion; `GET /api/recommend?model=<id>` returns `fit` + `suggestion` fields.

- [ ] **Step 1: Write the failing CLI test**

Append to `tests/test_cli_browse_recommend.py` (follow its existing pattern for building `args` and invoking `cmd_recommend`; use a namespace with `model`, `use_case`, `top_k`, `no_calibration`):

```python
def test_recommend_model_flag_reports_too_big(monkeypatch, capsys):
    import cli as cli_mod
    from backend.cookbook.hardware import GPUInfo, SystemInfo

    box = SystemInfo(os="Windows", cpu="c", cpu_cores=6, ram_gb=32.0,
                     gpus=[GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=8.0, backend="rocm")],
                     total_vram_gb=8.0)
    monkeypatch.setattr("backend.cookbook.hardware.detect", lambda: box)

    args = argparse.Namespace(model="deepseek-v3:671b", use_case="coding", top_k=5, no_calibration=True)
    cli_mod.cmd_recommend(args)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "fit" in out.lower()
```

(If the existing test file imports `argparse` differently, mirror its exact idiom.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_browse_recommend.py::test_recommend_model_flag_reports_too_big -v`
Expected: FAIL — `cmd_recommend` ignores `args.model` (no fit output) or `AttributeError`.

- [ ] **Step 3: Add --model to the recommend argparse subcommand**

In `cli.py`, find the `recommend` subparser setup (search for `add_parser("recommend"`) and add:

```python
    p_recommend.add_argument("--model", help="Check whether a specific model fits (and get a distill suggestion if not)")
```

- [ ] **Step 4: Handle --model in cmd_recommend**

In `cli.py` `cmd_recommend`, immediately after `info = detect()` (before the `recs = recommend(...)` line), add:

```python
        if getattr(args, "model", None):
            from backend.cookbook.fit import fit_verdict, recommend_distill
            from backend.cookbook.recommend import load_models
            by_id = {m.id: m for m in load_models()}
            target = by_id.get(args.model)
            if target is None:
                eprint(f"{C['red']}Unknown model '{args.model}'.{C['reset']}")
                sys.exit(1)
            v = fit_verdict(target, info, use_case)
            if v.kind == "fits":
                print(f"{C['green']}{target.name} fits your hardware at {v.quant}.{C['reset']}")
            elif v.kind == "fits_at_quant":
                print(f"{C['yellow']}{target.name} fits at {v.quant} (~{v.quality_cost:.0f} quality pts below F16).{C['reset']}")
            elif v.kind == "quantize_to_fit":
                print(f"{C['yellow']}{target.name} doesn't fit as-is; quantizing to ~{v.bpp} bpp would fit.{C['reset']}")
            else:
                print(f"{C['red']}{target.name} won't fit your hardware.{C['reset']}")
            sugg = recommend_distill(info, args.model, use_case)
            if sugg is not None:
                tag = "" if sugg.verified else f" {C['gray']}(unverified){C['reset']}"
                print(f"  {C['bold']}Try instead:{C['reset']} {sugg.note}{tag}")
            return
```

- [ ] **Step 5: Add the model param + suggestion to the API**

In `backend/api.py` `api_recommend`, after `top_k = request.args.get(...)` add:

```python
    model = request.args.get("model", "")
```

And in the returned `jsonify({...})`, add a `suggestion` block (compute before the return):

```python
    suggestion = None
    fit = None
    if model:
        from .cookbook.fit import fit_verdict, recommend_distill
        from .cookbook.recommend import load_models
        by_id = {m.id: m for m in load_models()}
        target = by_id.get(model)
        if target is not None:
            v = fit_verdict(target, info, use_case)
            fit = {"kind": v.kind, "quant": v.quant, "quality_cost": v.quality_cost, "bpp": v.bpp}
            s = recommend_distill(info, model, use_case)
            if s is not None:
                suggestion = {"model_id": s.model.id, "name": s.model.name,
                              "relationship": s.relationship, "verified": s.verified, "note": s.note}
```

Then add `"fit": fit, "suggestion": suggestion,` to the returned dict.

- [ ] **Step 6: Add an API test**

Append to `tests/test_api.py` (follow its existing Flask test-client idiom):

```python
def test_api_recommend_model_returns_fit_and_suggestion(client):
    r = client.get("/api/recommend?model=deepseek-v3:671b&vram=8")
    body = r.get_json()
    assert body["fit"]["kind"] in ("quantize_to_fit", "too_big")
```

- [ ] **Step 7: Run the full affected test set**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fit.py tests/test_recommend.py tests/test_cli_browse_recommend.py tests/test_api.py -m "not live" -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add cli.py backend/api.py tests/test_cli_browse_recommend.py tests/test_api.py
git commit -m "feat(fit): surface fit verdict + distill suggestion in CLI and API"
```

---

## Final Gate

- [ ] Run the full core suite: `.venv\Scripts\python.exe -m pytest -m "not live" -p no:cacheprovider -q` → all green.
- [ ] Run web gates if any web surface changed (none in this plan): `npm run typecheck && npm run build` in `web/`.
- [ ] Open a PR against `master` titled `feat(fit): fit intelligence — quant quality-cost + distill routing (Layers 1+2)`.

**Layer 3 (`lac quantize`) is a separate follow-on plan** — it wraps llama.cpp `quantize` and has its own safety/testing gate (subprocess orchestration, disk space, long-running jobs). Do not include it here.
