# Layer 3 — `lac quantize` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Download it and I'll quantize you a copy that fits." When a model you want doesn't fit your hardware at any distributed quant, `lac quantize <model>` computes the target quant from your VRAM, quantizes your installed copy via llama.cpp, imports the result into Ollama as a local variant, and registers it in the LAC catalog — with honest quality cost stated up front and every failure path leaving no mess behind.

**Architecture:** A new `backend/cookbook/quantize.py` module orchestrates: catalog lookup → installed-check (never trigger a silent pull) → target-quant selection (reuses `best_fit_quant` + `available_vram_gb`) → source GGUF resolution from the Ollama store → disk-space guard → `llama-quantize` subprocess (orchestrate + verify, never re-implement) → Ollama import of the quantized GGUF → catalog registration. Per the locked design doc (`docs/superpowers/specs/2026-08-01-fit-intelligence-distillation-design.md` §5, Duan 2026-08-01 — do not re-litigate): mechanism = quantization tooling wrapping llama.cpp, NOT training/distillation; CLI first; LAC orchestrates + verifies.

**Tech Stack:** Python 3.10+, pytest (`-m "not live"`), existing `backend/cookbook/` (fit.py, recommend.py, hardware.py), argparse CLI (`cli.py`). No new dependencies.

## Global Constraints

- Python 3.10+; core stays Pro-LOGIC-unaware (no `lac_pro` imports in model-hub).
- Test gate: `.venv\Scripts\python.exe -m pytest -m "not live" -p no:cacheprovider -q` stays green.
- TDD every task: red → green → commit. One logical commit per task.
- **Dependency-injected seams everywhere** (house pattern — see `ensure_agent_variant` in `backend/agent_launch/variant.py` and `hardware_fn` in `backend/agent_eval/runner.py`): Ollama calls, subprocess execution, store root, quantizer path, and disk probes are all callables/paths passed in, so unit tests never touch a real Ollama daemon, real subprocess, or real model files.
- **No real model builds in tests** (locked spec §7): the quantize subprocess is always a mocked runner.
- **Cleanup invariant** (locked spec: "every one of them must leave no scratch files behind"): every failure path removes staged artifacts.
- **Honesty invariant:** state the quality cost before running; never claim a quant LAC can't produce; refuse below-ladder targets with the math shown.
- Quant vocabulary: ladder names from `recommend.QUANTS` (F16, Q8, Q6_K, Q5_K_M, Q4_K_M, Q3_K_M, Q2_K). llama.cpp spells Q8 as `Q8_0`; Ollama's `/api/tags` `details.quantization_level` reports e.g. `Q4_K_M`, `Q8_0`, `F16`. Alias map lives in `quantize.py`.
- Run tests with the repo venv: `.venv\Scripts\python.exe -m pytest ...` (Windows).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/cookbook/fit.py` | Promote `_available_vram_gb` → public `available_vram_gb` | Modify |
| `backend/cookbook/quantize.py` | `QuantizePlan`, `select_target_quant`, store resolution, quantizer discovery/runner, disk guard, `quantize_model` orchestrator, error taxonomy | Create |
| `backend/cookbook/recommend.py` | `ModelEntry.quantized_from: Optional[str]` (default None); `recommend()` skips quantized derivatives for top-K | Modify |
| `cli.py` | `lac quantize <model> [--vram N] [-y]` subcommand + `cmd_quantize`; Layer-3 hint in `quantize_to_fit` / punishing `fits_at_quant` messages | Modify |
| `tests/test_quantize.py` | All new unit tests (pure core, store fixtures, mocked runner, orchestrator refusals) | Create |
| `tests/test_fit.py` | `available_vram_gb` public-API test | Modify |
| `tests/test_recommend.py` | `quantized_from` exclusion test | Modify |

Dependency order: Task 0 (spike, manual) → Task 1 (public VRAM helper) → Task 2 (target-quant core) → Task 3 (store resolution) → Task 4 (quantizer discovery + runner) → Task 5 (disk guard) → Task 6 (catalog field) → Task 7 (orchestrator, needs 1–6) → Task 8 (CLI, needs 7).

---

## Task 0: Spike — validate Ollama import-from-local-GGUF on the real daemon

**Why first:** Task 7's import step assumes `POST /api/create` with `from` = a local GGUF path works on Ollama 0.31.1 (the installed version). The Pro import plan proved the blob-upload + `files` path; the simpler `from: <path>` path is documented (docs.ollama.com/import uses Modelfile `FROM /path/to/file.gguf`; the API's `from` field accepts the same) but NOT yet observed in this codebase. Prove it before building on it.

- [ ] **Step 1: Pick any small installed model and locate its GGUF blob**

```powershell
# daemon must be running (ollama serve)
ollama list
# store root (default): $env:USERPROFILE\.ollama\models
# manifest for e.g. qwen3:4b -> manifests\registry.ollama.ai\library\qwen3\4b
# find the weights layer digest (mediaType application/vnd.ollama.image.model),
# blob at blobs\sha256-<digest>
```

- [ ] **Step 2: Create a throwaway model from the blob path**

```powershell
$body = @{ model = "lac-spike-import"; from = "C:\Users\User\.ollama\models\blobs\sha256-<digest>" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/create" -Body $body -ContentType "application/json"
ollama list   # lac-spike-import must appear
ollama delete lac-spike-import
```

- [ ] **Step 3: Record the outcome in this plan's PR description.**
  - If `from: <path>` works → Task 7 uses it.
  - If it fails → Task 7 falls back to the proven Pro-import path: `POST /api/blobs/:digest` upload then `/api/create` with `files` (see `docs/superpowers/plans/2026-07-05-lac-pro-custom-model-import.md` Task 1 for the observed shapes). Adjust Task 7's `create_from_file` seam accordingly; the seam exists precisely so this decision doesn't ripple.

---

## Task 1: Public `available_vram_gb` in fit.py

**Files:**
- Modify: `backend/cookbook/fit.py` (`_available_vram_gb`, ~line 44)
- Test: `tests/test_fit.py`

**Interfaces:**
- Produces: `available_vram_gb(info: SystemInfo) -> float` (public; internal callers updated). Semantics unchanged: combined GPU tier memory when tiers exist (RAM-half fallback), else best discrete VRAM (RAM-quarter fallback).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fit.py` (reuse that file's existing `SystemInfo`/`GPUInfo` construction pattern):

```python
def test_available_vram_gb_is_public_and_matches_internal_math():
    from backend.cookbook.fit import available_vram_gb
    # Discrete GPU tier wins over RAM fallbacks.
    info = SystemInfo(
        os="Test", cpu="c", cpu_cores=8, ram_gb=32.0,
        gpus=[GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=16.0, backend="rocm")],
        total_vram_gb=16.0,
    )
    from backend.cookbook.hardware import build_compute_tiers
    info.compute_tiers = build_compute_tiers(info.gpus, info.ram_gb)
    assert available_vram_gb(info) == 16.0
    # No tiers, no discrete VRAM -> RAM quarter floor.
    bare = SystemInfo(os="Test", cpu="c", cpu_cores=8, ram_gb=16.0, gpus=[], total_vram_gb=0.0)
    assert available_vram_gb(bare) == 4.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fit.py::test_available_vram_gb_is_public_and_matches_internal_math -v`
Expected: FAIL — `ImportError: cannot import name 'available_vram_gb'`.

- [ ] **Step 3: Rename `_available_vram_gb` → `available_vram_gb`**

In `backend/cookbook/fit.py`: rename the function and update its two internal call sites (`_max_fitting_bpp`). Add a docstring: "VRAM budget LAC plans against: combined GPU tier memory when compute tiers exist, else best discrete VRAM with RAM fallbacks."

- [ ] **Step 4: Run test to verify it passes + full fit suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/fit.py tests/test_fit.py
git commit -m "refactor(fit): promote available_vram_gb to public API for Layer 3"
```

---

## Task 2: Target-quant selection core

**Files:**
- Create: `backend/cookbook/quantize.py`
- Test: `tests/test_quantize.py`

**Interfaces:**

```python
QUANT_ALIASES = {"Q8_0": "Q8"}          # Ollama/llama.cpp spellings -> ladder names
LLAMA_QUANT_NAMES = {"Q8": "Q8_0"}      # ladder names -> llama-quantize type arg (others pass through)

@dataclass(frozen=True)
class QuantizePlan:
    target_quant: str        # ladder name, e.g. "Q3_K_M"
    target_bpp: float
    estimated_size_gb: float # weights-only estimate: params_b * bpp / 8 (active params for MoE)
    quality_cost: float      # |quality_penalty| of the target quant
    context: int             # context the fit was judged at

class QuantizeRefusal(Exception):
    """Honest refusal with a user-facing reason. Carries .reason and optional .suggestion."""

def select_target_quant(model: ModelEntry, info: SystemInfo, *,
                        vram_override_gb: float | None = None,
                        use_case: str = "coding") -> QuantizePlan:
    """Pick the quant that makes `model` fit, or raise QuantizeRefusal.

    Refusals (each with an honest reason):
    - model already fits at Q4_K_M (verdict "fits") -> nothing to gain; quantizing only loses quality
    - best ladder quant that fits is Q4_K_M or above -> same
    - nothing on the ladder fits (verdict would be quantize_to_fit/too_big: target below Q2_K)
      -> refusal shows the bpp math and suggests recommend_distill's answer when one exists
    """
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quantize.py`:

```python
from __future__ import annotations

import pytest

from backend.cookbook.hardware import GPUInfo, SystemInfo, build_compute_tiers
from backend.cookbook.recommend import QUANTS, ModelEntry
from backend.cookbook.quantize import QuantizePlan, QuantizeRefusal, select_target_quant


def _info(vram: float, ram: float = 32.0):
    gpus = [GPUInfo(name="AMD Radeon RX 6800 XT", vram_gb=vram, backend="rocm")] if vram else []
    info = SystemInfo(os="Test", cpu="c", cpu_cores=8, ram_gb=ram, gpus=gpus, total_vram_gb=vram)
    info.compute_tiers = build_compute_tiers(info.gpus, info.ram_gb)
    return info


def _model(params_b=20.0, context=65536, **kw):
    return ModelEntry(id="m:tag", name="M", provider="p", params_b=params_b,
                      arch="custom", context=context, use_cases=["coding"],
                      is_moe=False, **kw)


def test_select_target_quant_picks_lower_quant_for_tight_vram():
    plan = select_target_quant(_model(20.0), _info(16.0))
    assert isinstance(plan, QuantizePlan)
    assert plan.target_quant in ("Q3_K_M", "Q2_K")   # 20B on 16 GB cannot keep Q4
    assert plan.quality_cost > 0
    assert plan.estimated_size_gb > 0


def test_select_target_quant_refuses_when_model_already_fits():
    with pytest.raises(QuantizeRefusal, match="already fits"):
        select_target_quant(_model(3.0), _info(24.0))


def test_select_target_quant_refuses_below_ladder_with_math():
    # 70B on 8 GB: even Q2_K can't fit -> honest refusal with the bpp shown.
    with pytest.raises(QuantizeRefusal) as exc:
        select_target_quant(_model(70.0), _info(8.0))
    assert "bpp" in str(exc.value.reason)


def test_vram_override_changes_the_target():
    tight = select_target_quant(_model(20.0), _info(16.0))
    loose = select_target_quant(_model(20.0), _info(16.0), vram_override_gb=48.0)
    assert loose.target_quant != tight.target_quant or loose.target_bpp >= tight.target_bpp
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_quantize.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.cookbook.quantize`.

- [ ] **Step 3: Implement `quantize.py` core**

Implement `QuantizePlan`, `QuantizeRefusal`, `select_target_quant` per the interface above. Reuse: `fit_verdict` (for the "already fits" refusal), `available_vram_gb` (Task 1), `best_fit_quant` (target pick), `QUANTS` (bpp + quality_penalty lookup). Estimated size uses active params for MoE (`model.active_params_b or model.params_b`). Below-ladder refusal computes `_max_fitting_bpp`-style math via `fit_verdict`'s `bpp` when the verdict is `quantize_to_fit`, and includes it in `.reason`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_quantize.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/quantize.py tests/test_quantize.py
git commit -m "feat(quantize): target-quant selection core with honest refusals (Layer 3)"
```

---

## Task 3: Ollama store source resolution

**Files:**
- Modify: `backend/cookbook/quantize.py`
- Test: `tests/test_quantize.py`

**Interfaces:**

```python
class StoreError(Exception): ...        # base
class ManifestNotFound(StoreError): ... # model not in the local store
class MultiPartModel(StoreError): ...   # >1 weights layer: llama-quantize needs one GGUF
class NonGgufWeights(StoreError): ...   # weights blob lacks the GGUF magic (e.g. safetensors)

def default_store_root() -> Path:
    """OLLAMA_MODELS env var if set, else ~/.ollama/models."""

def resolve_source_gguf(model_name: str, *, store_root: Path | None = None) -> Path:
    """Resolve an installed model to its single GGUF blob path (read-only).

    model_name "qwen3:30b" -> <root>/manifests/registry.ollama.ai/library/qwen3/30b
    (untagged names get ":latest"; names containing a dotted first segment use it as the registry).
    Reads the manifest JSON, takes layers with mediaType "application/vnd.ollama.image.model",
    refuses MultiPartModel when >1, maps digest "sha256:<hex>" -> <root>/blobs/sha256-<hex>,
    and verifies the blob's first 4 bytes are the GGUF magic b"GGUF".
    """
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_quantize.py`:

```python
import json

from backend.cookbook.quantize import (
    ManifestNotFound, MultiPartModel, NonGgufWeights, resolve_source_gguf,
)


def _fake_store(tmp_path, name, tag, layers, blob_heads):
    """Build a minimal Ollama store fixture. layers: [(digest, mediaType)];
    blob_heads: {digest_hex: first bytes of the blob file}."""
    manifest_dir = tmp_path / "manifests" / "registry.ollama.ai" / "library" / name
    manifest_dir.mkdir(parents=True)
    manifest = {"schemaVersion": 2, "layers": [
        {"digest": f"sha256:{d}", "mediaType": mt, "size": 1} for d, mt in layers
    ]}
    (manifest_dir / tag).write_text(json.dumps(manifest), encoding="utf-8")
    blobs = tmp_path / "blobs"
    blobs.mkdir(exist_ok=True)
    for d, head in blob_heads.items():
        (blobs / f"sha256-{d}").write_bytes(head)
    return tmp_path


GGUF_MAGIC = b"GGUF" + b"\x00" * 16


def test_resolve_source_gguf_finds_single_weights_blob(tmp_path):
    d = "a" * 64
    root = _fake_store(tmp_path, "qwen3", "4b",
                       [(d, "application/vnd.ollama.image.model"),
                        ("b" * 64, "application/vnd.ollama.image.license")],
                       {d: GGUF_MAGIC})
    assert resolve_source_gguf("qwen3:4b", store_root=root) == root / "blobs" / f"sha256-{d}"


def test_resolve_source_gguf_defaults_latest_tag(tmp_path):
    d = "c" * 64
    root = _fake_store(tmp_path, "llama3.2", "latest",
                       [(d, "application/vnd.ollama.image.model")], {d: GGUF_MAGIC})
    assert resolve_source_gguf("llama3.2", store_root=root).name == f"sha256-{d}"


def test_resolve_source_gguf_refuses_multipart(tmp_path):
    root = _fake_store(tmp_path, "big", "70b",
                       [("a" * 64, "application/vnd.ollama.image.model"),
                        ("b" * 64, "application/vnd.ollama.image.model")],
                       {"a" * 64: GGUF_MAGIC, "b" * 64: GGUF_MAGIC})
    with pytest.raises(MultiPartModel):
        resolve_source_gguf("big:70b", store_root=root)


def test_resolve_source_gguf_refuses_non_gguf_weights(tmp_path):
    d = "d" * 64
    root = _fake_store(tmp_path, "st", "1b",
                       [(d, "application/vnd.ollama.image.model")], {d: b"\x00" * 8})
    with pytest.raises(NonGgufWeights):
        resolve_source_gguf("st:1b", store_root=root)


def test_resolve_source_gguf_missing_manifest(tmp_path):
    with pytest.raises(ManifestNotFound):
        resolve_source_gguf("nope:1b", store_root=tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**, then implement `default_store_root` + `resolve_source_gguf` + error classes per the interface. Read blobs with `open(path, "rb").read(4)` for the magic check — never copy or mutate the source blob.

- [ ] **Step 3: Run tests to verify they pass.**

- [ ] **Step 4: Commit**

```bash
git add backend/cookbook/quantize.py tests/test_quantize.py
git commit -m "feat(quantize): resolve installed model to its GGUF blob in the Ollama store"
```

---

## Task 4: Quantizer discovery + bounded runner

**Files:**
- Modify: `backend/cookbook/quantize.py`
- Test: `tests/test_quantize.py`

**Interfaces:**

```python
class QuantizerNotFound(QuantizeError): ...  # carries install guidance in .reason
class QuantizeRunFailed(QuantizeError): ...  # non-zero exit; carries tail of output

def find_quantizer(*, override: str | None = None) -> Path:
    """LAC_LLAMA_QUANTIZE env var (or `override`) if set, else shutil.which for
    'llama-quantize' then legacy 'quantize'. Raises QuantizerNotFound with clear
    install guidance (build llama.cpp or install a release; must be on PATH)."""

def run_quantize(src: Path, dst: Path, quant_type: str, *,
                 quantizer: Path,
                 run=subprocess.run) -> None:
    """Run `<quantizer> <src> <dst> <quant_type>` to completion. Streams nothing by
    default in tests (injected `run`); the CLI passes a runner that echoes progress.
    On non-zero exit: delete any partial `dst`, raise QuantizeRunFailed with the
    output tail. Never touches `src`."""
```

- [ ] **Step 1: Write the failing tests** — cover: env-var override wins; PATH discovery (monkeypatch `shutil.which`); not-found raises with "llama.cpp" in the reason; runner success leaves `dst` (fake `run` writes it); runner failure deletes partial `dst` and raises with output tail. All with an injected fake `run` callable — never a real subprocess.

- [ ] **Step 2: Run to verify fail → implement → run to verify pass.**

- [ ] **Step 3: Commit**

```bash
git add backend/cookbook/quantize.py tests/test_quantize.py
git commit -m "feat(quantize): llama-quantize discovery + bounded runner with partial-output cleanup"
```

---

## Task 5: Disk-space guard

**Files:**
- Modify: `backend/cookbook/quantize.py`
- Test: `tests/test_quantize.py`

**Interfaces:**

```python
def required_space_gb(plan: QuantizePlan) -> float:
    """Staging copy + Ollama import copy + 10% margin = plan.estimated_size_gb * 2.2."""

class InsufficientDiskSpace(QuantizeError): ...

def check_disk_space(staging_dir: Path, store_root: Path, required_gb: float, *,
                     disk_usage=shutil.disk_usage) -> None:
    """Raise InsufficientDiskSpace unless BOTH the staging volume and the store
    volume have required_gb free (the quantized file is staged, then Ollama copies
    it into its blob store on import)."""
```

- [ ] **Step 1: Failing tests** with a fake `disk_usage` returning controlled free-space values (both-ok, staging-short, store-short). **Step 2: Implement. Step 3: Pass. Step 4: Commit** (`feat(quantize): pre-flight disk-space guard for staging + import copies`).

---

## Task 6: Catalog field for quantized derivatives

**Files:**
- Modify: `backend/cookbook/recommend.py` (`ModelEntry`, `recommend()`)
- Test: `tests/test_recommend.py`

**Interfaces:**
- `ModelEntry.quantized_from: Optional[str] = None` — the source model id when this entry is a LAC-produced quantized variant. Optional with default (must not break `ModelEntry(**m)` for existing entries — locked spec risk-table pattern).
- `recommend()` skips entries with `quantized_from` set when building the top-K (a derivative is launchable and lookup-able, but must not pollute recommendations scored across quants it doesn't have).

- [ ] **Step 1: Write the failing tests**

```python
def test_quantized_from_defaults_none():
    m = ModelEntry(id="x", name="X", provider="p", params_b=1.0, arch="llama",
                   context=4096, use_cases=[], is_moe=False)
    assert m.quantized_from is None


def test_recommend_top_k_excludes_quantized_derivatives(monkeypatch):
    # A derivative entry with a great score must not appear in top-K,
    # while direct lookup (load_models) still sees it.
    ...
```

(For the second test, follow the existing `tests/test_recommend.py` fixture pattern for constructing a `SystemInfo` and calling `recommend()`; add one synthetic derivative via monkeypatched `load_models`.)

- [ ] **Step 2: Red → implement (field + one-line filter in `recommend()`'s candidate loop) → green → commit** (`feat(catalog): quantized_from marker; keep derivatives out of recommendation top-K`).

---

## Task 7: The orchestrator — `quantize_model`

**Files:**
- Modify: `backend/cookbook/quantize.py`
- Test: `tests/test_quantize.py`

**Interfaces:**

```python
STAGING_DIR = Path.home() / ".model-hub" / "quantize-staging"

def quantize_variant_name(base_model: str, quant: str) -> str:
    """'qwen3:30b' + 'Q3_K_M' -> 'qwen3:30b-q3_k_m-fit' (agent-variant naming convention)."""

@dataclass(frozen=True)
class QuantizeResult:
    variant: str            # Ollama model name created
    plan: QuantizePlan
    source: str             # source model id
    catalog_id: str         # registered custom-model id

def quantize_model(model_id: str, *,
                   vram_override_gb: float | None = None,
                   list_names: Callable[[], Iterable[str]],          # /api/tags names
                   quant_levels: Callable[[], dict[str, str]],       # name -> quantization_level
                   create_from_file: Callable[[str, Path], None],    # Ollama import (Task 0 seam)
                   store_root: Path | None = None,
                   quantizer: Path | None = None,
                   run=subprocess.run,
                   disk_usage=shutil.disk_usage,
                   yes: bool = False) -> QuantizeResult:
    """The full Layer-3 pipeline. Every refusal is a QuantizeRefusal/StoreError/
    QuantizeError subclass with a user-facing reason; every failure after staging
    deletes the staged file."""
```

Pipeline (each step a refusal point with an honest message):
1. Catalog lookup via `load_models()` — unknown model → refusal ("Unknown model ... LAC quantizes models it knows: see `lac browse`").
2. Installed check via `list_names()` (normalize like `variant.is_installed`) — not installed → refusal with `lac pull <model>` hint. **Never let Ollama pull implicitly** (variant.py hard rule).
3. `select_target_quant` (Task 2) — refusals pass through.
4. Source quant via `quant_levels()` — target must be strictly below the installed quant (bpp ordering, alias-normalized); equal → "already at <quant>"; higher → "can't raise quality by re-quantizing".
5. Variant-exists check via `list_names()` — `quantize_variant_name` already present → refusal ("delete it first: lac delete <variant>").
6. `resolve_source_gguf` (Task 3) — store errors pass through.
7. `check_disk_space` (Task 5).
8. Stage + run: `STAGING_DIR.mkdir(parents=True, exist_ok=True)`, `run_quantize(src, staged, quant_type, quantizer=find_quantizer(...), run=run)` — on ANY exception from here through step 10, delete the staged file before re-raising.
9. `create_from_file(variant, staged)` (Task 0 seam), then `register_custom_model(entry)` — entry = source entry's dict with `id` = variant, `name` = f"{source.name} {target_quant} (LAC quantized)", `quantized_from` = source id.
10. Delete staged file; return `QuantizeResult`.

- [ ] **Step 1: Write the failing tests** — all with injected fakes:
  - happy path: fake list_names/quant_levels/create_from_file capture calls; asserts variant name, create called with staged path, catalog entry registered (monkeypatch `register_custom_model` or point `CUSTOM_MODELS_PATH` at tmp_path), staged file cleaned up.
  - refusal matrix: unknown model / not installed / already fits / target-not-below-source / variant exists / multipart store / insufficient disk / quantizer missing / runner failure (each asserts the specific exception AND no staged file left behind).
- [ ] **Step 2: Red → implement → green.**
- [ ] **Step 3: Full suite** `.venv\Scripts\python.exe -m pytest -m "not live" -q` — green.
- [ ] **Step 4: Commit** (`feat(quantize): orchestrator — quantize installed model to a fitting local variant (Layer 3)`).

---

## Task 8: CLI wiring + Layer-3 hints

**Files:**
- Modify: `cli.py`
- Test: `tests/test_cli_quantize.py` (create; follow existing CLI-test conventions — check `tests/` for the closest `cli`/`build_parser` test file and mirror it)

**Interfaces:**
- Parser: `p_quant = sub.add_parser("quantize", help="Quantize an installed model so it fits your hardware")`; args: `model` (positional), `--vram` (float, GB override), `-y/--yes` (skip confirmation). `set_defaults(func=cmd_quantize)`.
- `cmd_quantize(args)`:
  1. `detect()` (respect `--vram`), resolve plan via `select_target_quant` — refusals print the reason in red and exit 1.
  2. Print the honest plan BEFORE running: target quant, estimated size, `~N quality pts below F16`, context judged at. Confirm `y/N` unless `-y`.
  3. Call `quantize_model` with the cli.py `ollama()` helpers as seams: `list_names` = names from `GET /api/tags`; `quant_levels` = `{m["name"]: m.get("details", {}).get("quantization_level", "") ...}`; `create_from_file` = `ollama_stream("/api/create", {"model": name, "from": str(path), "stream": True}, timeout=3600)` consumed to completion (error chunks → raise).
  4. Progress: echo `run_quantize` output lines (pass a streaming runner); success line follows house style: `✓ <variant> created — proven target <quant> at ~<size> GB`.
- Layer-3 hints (locked spec §6 data flow): in `cmd_recommend`'s `--model` branch — `quantize_to_fit` message gains `Run: lac quantize <model>`; `fits_at_quant` with `quality_cost >= HIGH_QUALITY_COST` (import from fit.py) mentions the quantize alternative alongside.

- [ ] **Step 1: Failing tests** — parser accepts `quantize m --vram 16 -y` (assert parsed fields); hint text present for quantize_to_fit (monkeypatch fit_verdict or call the formatting path directly — mirror how existing tests exercise cli messages).
- [ ] **Step 2: Red → implement → green.**
- [ ] **Step 3: Full gate** `.venv\Scripts\python.exe -m pytest -m "not live" -p no:cacheprovider -q` — exit 0.
- [ ] **Step 4: Commit** (`feat(cli): lac quantize command + Layer-3 hints in fit messages`).

---

## Out of scope (explicitly)

- Ollama-native quantize path (`/api/create` `quantize` field): only supports q8_0/q4_K_S/q4_K_M from FP16/FP32 sources — never covers the fits_at_quant targets (Q6_K/Q5_K_M/Q3_K_M/Q2_K) that are Layer 3's reason to exist. Future optimization only.
- IQ-quants / below-Q2_K targets (the `quantize_to_fit` bpp region): refused with math + distill suggestion. llama.cpp imatrix quants are a later cycle if demanded.
- Multi-part (sharded) models and safetensors-stored pulls: refused honestly in v1.
- Re-quantizing upward, quantizing models LAC doesn't know (no catalog entry), GUI cockpit, Pro gating (Layer 3 is free-tier; core stays Pro-unaware).
- Tweakability 3b/3c (per-project profiles, offload config editor): separate follow-on, still parked.

## Risks

| Risk | Mitigation |
|---|---|
| `/api/create` `from: <local path>` behaves differently on 0.31.1 | Task 0 spike gates it; fallback = proven blob-upload + `files` path (Pro import plan) |
| llama.cpp not on the user's machine | `find_quantizer` refusal carries install guidance; degrades gracefully (locked spec §9) |
| Long-running quantize interrupted mid-write | staged file in `~/.model-hub/quantize-staging`; every failure/exception path deletes it |
| Silent multi-GB download via Ollama create | installed-check before anything else (variant.py hard rule, refusal with `lac pull` hint) |
| Disk fills during quantize | pre-flight guard checks BOTH staging and store volumes for 2.2× the estimate |
| Quantized output disappoints | honest quality-cost line printed before confirmation (locked spec §5 Honesty) |
