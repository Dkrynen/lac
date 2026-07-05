# LAC Pro Custom Model Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A licensed LAC Pro user pastes a Hugging Face repo ID and LAC downloads it, checks it's a convertible architecture, picks the right quantization for their hardware, converts+quantizes it via Ollama's own native local-import capability, installs it, hands it to Autopilot for benchmarking/tuning, and registers it as a permanent catalog citizen — all gated behind `require()`.

**Architecture:** New `lac_pro/hf_import.py` module orchestrates: HF metadata fetch → architecture pre-check → disk-space pre-check → download to a scratch dir → SHA256 each file → upload as Ollama blobs (`POST /api/blobs/:digest`) → `POST /api/create` with `files` + `quantize` → cleanup → register in model-hub's catalog extension file → hand off to the existing `autopilot.run_autopilot()`. A new CLI command and a background-thread-backed API route (mirroring the existing Autopilot status-polling pattern) surface it to users. model-hub gains two small, additive helpers in `recommend.py`: a reusable "best quant for N params at M GB VRAM" function, and a user-writable catalog-extension file merged into `load_models()`.

**Tech Stack:** Python stdlib only for lac-pro's new code (`urllib.request`, `hashlib`, `json`, `tempfile`) — no new dependency, matching the existing codebase's convention of talking to Ollama's HTTP API directly rather than shelling out to the `ollama` CLI binary or adding a `requests`/`huggingface_hub` dependency.

## Global Constraints

- Subagent-driven development: fresh implementer + fresh reviewer subagent per task. Every dispatch must explicitly say "work in the foreground, do NOT spawn agents."
- TDD per task: write the failing test first, confirm it fails for the stated reason, implement, confirm it passes.
- Commits land directly on `master` in both repos, per task. Never push to origin without Duan's separate, explicit go-ahead each time.
- lac-pro's Python interpreter for every command is model-hub's venv: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe`, invoked from the `C:\Users\User\repos\lac-pro` directory (lac-pro has no venv of its own — it's editable-installed into model-hub's venv).
- License gating: this whole feature is user-invoked (paste a URL, click Import — or run a CLI command), so every entry point gates via `require(feature)` from `lac_pro/license.py` (exits 3 + upgrade message), matching `lac pro tune`/`lac pro benchmark` — **not** `check()`, which is reserved for the silent, non-blocking Autopilot hook.
- Four distinct, honest failure states, never a generic error: architecture unsupported, insufficient disk space, download failed, conversion/quantization failed (spec §4). Every one of them must leave no scratch files behind.
- No new inference runtime (vLLM/TensorRT-LLM/MLX) — out of scope (spec §7). The output of this feature is always a normal Ollama-managed GGUF model.
- Full spec: `docs/superpowers/specs/2026-07-05-lac-pro-custom-model-import-design.md` — read it before starting Task 1.

---

### Task 1: Spike — validate Ollama's blob-upload + quantized local-import flow

This is the flagged open technical risk from the spec (§3): everything else in this plan assumes `POST /api/blobs/:digest` + `POST /api/create` with a `files` dict and a `quantize` parameter actually works end-to-end. This task proves it against a real, small, real Ollama daemon before any orchestration code gets built on top of the assumption.

**Files:**
- Create: `lac_pro/hf_import.py` (starts with just the three low-level primitives this task validates)
- Test: `tests/test_hf_import.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks (this is Task 1)
- Produces: `sha256_file(path: Path) -> str`, `upload_blob(digest: str, path: Path, ollama_host: str) -> None`, `ollama_create_from_files(name: str, files: dict[str, str], quantize: str | None, ollama_host: str) -> dict` — later tasks call these three functions by these exact names/signatures.

- [ ] **Step 1: Manual spike — run this by hand first, before writing any test**

Confirm Ollama is running (`http://localhost:11434`), then in a Python REPL (using model-hub's venv) or a scratch script, download a genuinely tiny public HF model's files by hand (e.g. `hf-internal-testing/tiny-random-LlamaForCausalLM`, a few hundred KB — do NOT use a multi-GB model for this spike) via plain HTTPS GETs to `https://huggingface.co/<repo_id>/resolve/main/<filename>` for each file listed at `https://huggingface.co/api/models/<repo_id>?blobs=true`. For each downloaded file: compute its SHA256, `PUT`/`POST` it to `http://localhost:11434/api/blobs/sha256:<digest>` (confirm via Ollama's docs/experimentation whether it's PUT or POST and what a successful response looks like — this exact detail is what the spike nails down). Then `POST http://localhost:11434/api/create` with a JSON body `{"model": "spike-test:latest", "files": {<filename>: "sha256:<digest>", ...}, "quantize": "q4_K_M", "stream": false}`. Confirm the response indicates success (not an error), then confirm the model is listed via `GET /api/tags` and can actually generate via `POST /api/generate {"model": "spike-test:latest", "prompt": "hi", "stream": false}`.

Write down (in your task report) the exact request method, headers, and body shape that worked, and the exact response shape — the next steps codify whatever you actually observed, not what the docs claimed.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_hf_import.py`:

```python
from __future__ import annotations

import hashlib

import pytest


def test_sha256_file_matches_known_hash(tmp_path):
    from lac_pro.hf_import import sha256_file

    f = tmp_path / "sample.txt"
    f.write_bytes(b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert sha256_file(f) == expected


def test_upload_blob_posts_to_correct_url_with_file_bytes(tmp_path, monkeypatch):
    from lac_pro import hf_import as hf_import_mod

    f = tmp_path / "sample.bin"
    f.write_bytes(b"some-bytes")
    captured = {}

    def fake_urlopen(req, timeout=600):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data

        class FakeResp:
            def read(self):
                return b""
        return FakeResp()

    monkeypatch.setattr(hf_import_mod.urllib.request, "urlopen", fake_urlopen)
    hf_import_mod.upload_blob("sha256:deadbeef", f, "http://localhost:11434")

    assert captured["url"] == "http://localhost:11434/api/blobs/sha256:deadbeef"
    assert captured["data"] == b"some-bytes"


def test_ollama_create_from_files_sends_correct_body(monkeypatch):
    from lac_pro import hf_import as hf_import_mod

    captured = {}

    def fake_urlopen(req, timeout=600):
        import json
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())

        class FakeResp:
            def read(self):
                return b'{"status": "success"}'
        return FakeResp()

    monkeypatch.setattr(hf_import_mod.urllib.request, "urlopen", fake_urlopen)
    result = hf_import_mod.ollama_create_from_files(
        "my-custom-model:latest",
        {"config.json": "sha256:aaa", "model.safetensors": "sha256:bbb"},
        "q4_K_M",
        "http://localhost:11434",
    )

    assert captured["url"] == "http://localhost:11434/api/create"
    assert captured["body"]["model"] == "my-custom-model:latest"
    assert captured["body"]["files"] == {"config.json": "sha256:aaa", "model.safetensors": "sha256:bbb"}
    assert captured["body"]["quantize"] == "q4_K_M"
    assert result == {"status": "success"}


@pytest.mark.live
def test_real_ollama_can_import_a_tiny_hf_model_end_to_end(tmp_path):
    """Live-only, needs a running Ollama daemon and real internet access.
    Downloads a genuinely tiny public HF model and proves the whole
    blob-upload + quantized-create flow works against the real API --
    this is the one piece of the spec this plan is least certain about."""
    import urllib.request
    from lac_pro.hf_import import sha256_file, upload_blob, ollama_create_from_files

    repo_id = "hf-internal-testing/tiny-random-LlamaForCausalLM"
    api_url = f"https://huggingface.co/api/models/{repo_id}?blobs=true"
    with urllib.request.urlopen(api_url, timeout=30) as r:
        import json
        info = json.loads(r.read().decode())

    files = {}
    for sib in info["siblings"]:
        fname = sib["rfilename"]
        if fname in ("README.md", ".gitattributes"):
            continue
        dest = tmp_path / fname
        with urllib.request.urlopen(f"https://huggingface.co/{repo_id}/resolve/main/{fname}", timeout=60) as r:
            dest.write_bytes(r.read())
        digest = "sha256:" + sha256_file(dest)
        upload_blob(digest, dest, "http://localhost:11434")
        files[fname] = digest

    result = ollama_create_from_files("lac-import-spike-test:latest", files, "q4_K_M", "http://localhost:11434")
    assert "error" not in result

    with urllib.request.urlopen(
        urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({"model": "lac-import-spike-test:latest", "prompt": "hi",
                              "stream": False, "options": {"num_predict": 4}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        ),
        timeout=60,
    ) as r:
        gen = json.loads(r.read().decode())
    assert "response" in gen

    # Cleanup: this test creates a real Ollama model, delete it afterward.
    del_req = urllib.request.Request(
        "http://localhost:11434/api/delete",
        data=json.dumps({"name": "lac-import-spike-test:latest"}).encode(),
        headers={"Content-Type": "application/json"}, method="DELETE",
    )
    urllib.request.urlopen(del_req, timeout=30)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `ModuleNotFoundError: No module named 'lac_pro.hf_import'` (collection failure, the module doesn't exist yet).

- [ ] **Step 4: Implement based on Step 1's actual findings**

Create `lac_pro/hf_import.py`. The three functions below are written against Ollama's documented API shape (`POST /api/blobs/sha256:<digest>`, `POST /api/create` with `files`+`quantize`) — **adjust the exact method/URL/body to match whatever Step 1's manual spike actually observed**, not this starting point, if they differ:

```python
"""Custom Hugging Face model import (LAC Pro): download an arbitrary
compatible HF model, convert+quantize it via Ollama's own native
local-safetensors import, and install it. See
docs/superpowers/specs/2026-07-05-lac-pro-custom-model-import-design.md.

Low-level Ollama HTTP primitives below are talked to directly via urllib
(stdlib only), matching the existing pattern in lac_pro/tune.py's
_ollama_json -- no requests/huggingface_hub dependency, no shelling out
to the `ollama` CLI binary.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Hex SHA256 digest of a file's contents, streamed (not loaded whole
    into memory -- model weight files can be multiple GB)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_blob(digest: str, path: Path, ollama_host: str) -> None:
    """POST a file's bytes to Ollama's blob store, keyed by its digest
    (e.g. "sha256:<hex>"). Ollama verifies the uploaded bytes hash to the
    given digest -- callers must pass the REAL digest of path's contents
    (use sha256_file), not a value taken on trust from anywhere else
    (Hugging Face's own declared LFS sha256 uses the same algorithm but
    computing it ourselves from the actual downloaded bytes is the only
    way to guarantee we're uploading what we think we're uploading)."""
    url = f"{ollama_host.rstrip('/')}/api/blobs/{digest}"
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=600) as resp:
        resp.read()


def ollama_create_from_files(name: str, files: dict[str, str], quantize: str | None,
                              ollama_host: str) -> dict:
    """POST /api/create with a `files` map (filename -> "sha256:<digest>",
    every digest already uploaded via upload_blob) and an optional
    `quantize` level (e.g. "q4_K_M") -- this is the single call that makes
    Ollama build a GGUF model from local safetensors AND quantize it, in
    one step, with zero llama.cpp tooling vendored by LAC itself."""
    body: dict = {"model": name, "files": files, "stream": False}
    if quantize:
        body["quantize"] = quantize
    req = urllib.request.Request(
        f"{ollama_host.rstrip('/')}/api/create",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        return json.loads(resp.read().decode() or "{}")
```

Add `pytest.ini`/`pyproject.toml` marker registration if `live` isn't already a registered marker in this repo — check `C:\Users\User\repos\model-hub\pytest.ini` or `pyproject.toml`'s `[tool.pytest.ini_options]` first (core already uses `@pytest.mark.live` elsewhere in this project, so it's very likely already registered; only add it if `pytest --markers` doesn't list it).

- [ ] **Step 5: Run tests to verify they pass**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: 3 tests PASS (the live test is excluded by the marker filter).

If Ollama is running locally, also run the live test: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -m live -v`
Expected: PASS. If it fails, that is real, important signal — do not silently skip it. Report the exact failure back; it likely means Step 4's implementation needs adjusting to match what Step 1's manual spike actually found (the docs and this plan's starting-point code could both be wrong about an exact detail).

- [ ] **Step 6: Commit**

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/hf_import.py tests/test_hf_import.py
git commit -m "spike: validate Ollama's blob-upload + quantized local-import flow

Confirms POST /api/blobs/:digest + POST /api/create with a files map and
a quantize parameter genuinely builds and quantizes a GGUF model from
local Hugging Face safetensors, with zero llama.cpp tooling vendored by
LAC itself. This is the technical foundation the rest of the custom
model import feature builds on (spec 2026-07-05, flagged open risk)."
```

---

### Task 2: `recommend.py` — reusable quant-fit helper + custom-catalog registration

**Files:**
- Modify: `backend/cookbook/recommend.py:143-151` (`load_models`)
- Test: `tests/test_recommend.py` (append)

**Interfaces:**
- Consumes: `ModelEntry`, `QUANTS`, `_estimate_vram`, `_fit_score` (existing, unchanged)
- Produces: `best_fit_quant(params_b: float, is_moe: bool, active_params_b: float | None, context: int, available_vram_gb: float) -> tuple[str, float]` (returns `(quant_name, estimated_vram_gb)`); `register_custom_model(entry_dict: dict) -> None`; `load_models()`'s return value now includes user-registered custom models in addition to the shipped catalog.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recommend.py`:

```python
def test_best_fit_quant_picks_highest_quality_quant_that_fits():
    from backend.cookbook.recommend import best_fit_quant

    # A dense 9B model on a 16GB card: Q8 (1.05 bpp * 9 ~ 9.45GB) should
    # fit and score better than a smaller/lower quant that underuses VRAM.
    quant_name, vram = best_fit_quant(
        params_b=9.0, is_moe=False, active_params_b=None, context=8192, available_vram_gb=16.0,
    )
    assert quant_name == "Q8"
    assert 9.0 < vram < 10.5


def test_best_fit_quant_falls_back_to_smallest_when_nothing_fits():
    from backend.cookbook.recommend import best_fit_quant

    quant_name, vram = best_fit_quant(
        params_b=70.0, is_moe=False, active_params_b=None, context=8192, available_vram_gb=4.0,
    )
    assert quant_name == "Q2_K"  # smallest bpp in QUANTS -- last resort, still returned not raised


def test_best_fit_quant_matches_recommend_engines_own_pick_for_an_equivalent_catalog_model():
    """Spec §6 requires this specific cross-check: best_fit_quant() must
    pick the identical quant the curated-catalog scoring path would for a
    real catalog model at the same param count -- not just structurally
    similar code, the SAME numbers. Picks a real, small, non-MoE catalog
    entry and proves iterating QUANTS via _estimate_vram/_fit_score against
    the REAL ModelEntry produces the same (quant, vram) as best_fit_quant's
    synthetic ModelEntry does for the same params_b/is_moe/context."""
    from backend.cookbook.recommend import (
        best_fit_quant, load_models, QUANTS, _estimate_vram, _fit_score,
    )

    real_model = next(m for m in load_models() if not m.is_moe and 0.5 <= m.params_b <= 4.0)
    available_vram_gb = 8.0

    got_quant, got_vram = best_fit_quant(
        real_model.params_b, real_model.is_moe, real_model.active_params_b,
        real_model.context, available_vram_gb,
    )

    best = None
    for q in QUANTS:
        vram = _estimate_vram(real_model, q, real_model.context)
        if vram > available_vram_gb:
            continue
        utilization = vram / available_vram_gb
        score = _fit_score(utilization, "gpu")
        if best is None or score > best[2]:
            best = (q.name, vram, score)

    assert (got_quant, got_vram) == (best[0], best[1])


def test_register_custom_model_then_load_models_includes_it(tmp_path, monkeypatch):
    from backend.cookbook import recommend as recommend_mod

    custom_path = tmp_path / "custom_models.json"
    monkeypatch.setattr(recommend_mod, "CUSTOM_MODELS_PATH", custom_path)

    recommend_mod.register_custom_model({
        "id": "myorg/mymodel", "name": "My Model", "provider": "custom",
        "params_b": 9.0, "arch": "qwen2", "context": 8192, "use_cases": ["general"],
        "is_moe": False,
    })

    models = recommend_mod.load_models()
    assert any(m.id == "myorg/mymodel" for m in models)


def test_register_custom_model_dedupes_on_reimport(tmp_path, monkeypatch):
    from backend.cookbook import recommend as recommend_mod

    custom_path = tmp_path / "custom_models.json"
    monkeypatch.setattr(recommend_mod, "CUSTOM_MODELS_PATH", custom_path)

    entry = {"id": "myorg/mymodel", "name": "My Model", "provider": "custom",
              "params_b": 9.0, "arch": "qwen2", "context": 8192, "use_cases": [], "is_moe": False}
    recommend_mod.register_custom_model(entry)
    recommend_mod.register_custom_model(entry)  # re-import same id

    raw = json.loads(custom_path.read_text())
    assert len(raw) == 1
```

Add `import json` to the top of `tests/test_recommend.py` if it isn't already imported (check first).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_recommend.py -k "best_fit_quant or register_custom_model"` (from `C:\Users\User\repos\model-hub`)
Expected: FAIL — `ImportError`/`AttributeError`, `best_fit_quant`/`register_custom_model`/`CUSTOM_MODELS_PATH` don't exist yet.

- [ ] **Step 3: Implement**

Replace lines 143-151 (`load_models`):

```python
def load_models() -> list[ModelEntry]:
    # Catalog is read-only app data; memoize so repeated callers (recommend(),
    # load_calibration, parse_model_tag) share one JSON parse per process.
    path = DATA_DIR / "models.json"
    if not path.exists():
        raise FileNotFoundError(f"Model database not found at {path}")
    with open(path) as f:
        raw = json.load(f)
    return [ModelEntry(**m) for m in raw]
```

with:

```python
@functools.lru_cache(maxsize=1)
def _load_shipped_models() -> list[ModelEntry]:
    path = DATA_DIR / "models.json"
    if not path.exists():
        raise FileNotFoundError(f"Model database not found at {path}")
    with open(path) as f:
        raw = json.load(f)
    return [ModelEntry(**m) for m in raw]


CUSTOM_MODELS_PATH = Path.home() / ".model-hub" / "custom_models.json"


def _load_custom_models() -> list[ModelEntry]:
    """User-imported custom models (LAC Pro's Hugging Face import feature,
    spec 2026-07-05). Deliberately NOT cached like the shipped catalog --
    this file can change while the app is running, right after a new
    import completes, and callers must see it on their very next
    load_models() call with no restart needed."""
    if not CUSTOM_MODELS_PATH.exists():
        return []
    try:
        with open(CUSTOM_MODELS_PATH) as f:
            raw = json.load(f)
        return [ModelEntry(**m) for m in raw]
    except Exception:
        return []


def register_custom_model(entry_dict: dict) -> None:
    """Append a newly-imported custom model to the user's catalog
    extension file, so it becomes a full catalog citizen -- scored,
    tagged, recommended -- exactly like the curated 91 (spec 2026-07-05,
    decision 4). Re-importing the same id replaces the previous entry
    rather than duplicating it."""
    CUSTOM_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if CUSTOM_MODELS_PATH.exists():
        try:
            existing = json.loads(CUSTOM_MODELS_PATH.read_text())
        except Exception:
            existing = []
    existing = [e for e in existing if e.get("id") != entry_dict["id"]]
    existing.append(entry_dict)
    CUSTOM_MODELS_PATH.write_text(json.dumps(existing, indent=2))


def load_models() -> list[ModelEntry]:
    return _load_shipped_models() + _load_custom_models()


def best_fit_quant(params_b: float, is_moe: bool, active_params_b: float | None,
                    context: int, available_vram_gb: float) -> tuple[str, float]:
    """Pick the best-fitting quant for an arbitrary model that ISN'T in the
    catalog yet, given its raw param count and the user's available VRAM --
    reuses the exact same _estimate_vram/_fit_score logic recommend()
    already uses for the curated 91, so a custom-imported model gets
    scored by the identical rules (spec 2026-07-05, decision 3). Returns
    (quant_name, estimated_vram_gb). Falls back to the smallest/most
    aggressive quant in QUANTS if nothing fits within available_vram_gb,
    rather than raising -- a caller can still offer that as a last resort
    with a clear "this will be tight" framing, not a hard failure."""
    synthetic = ModelEntry(
        id="_synthetic", name="_synthetic", provider="custom", params_b=params_b,
        arch="custom", context=context, use_cases=[], is_moe=is_moe,
        active_params_b=active_params_b,
    )
    best: tuple[str, float, float] | None = None
    for q in QUANTS:
        vram = _estimate_vram(synthetic, q, context)
        if vram > available_vram_gb:
            continue
        utilization = vram / available_vram_gb if available_vram_gb > 0 else 0.0
        score = _fit_score(utilization, "gpu")
        if best is None or score > best[2]:
            best = (q.name, vram, score)
    if best is None:
        smallest = min(QUANTS, key=lambda q: q.bpp)
        return smallest.name, _estimate_vram(synthetic, smallest, context)
    return best[0], best[1]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_recommend.py` (from `C:\Users\User\repos\model-hub`)
Expected: all tests PASS, including the pre-existing ones (`load_models` callers are unaffected — `_load_shipped_models()` is the same cached parse the old `load_models()` did, just renamed and now composed with an empty-by-default custom list).

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/cookbook/recommend.py tests/test_recommend.py
git commit -m "feat(recommend): best_fit_quant() + user-writable custom-model catalog extension

Two small, additive helpers for LAC Pro's custom Hugging Face model
import feature: best_fit_quant() reuses the exact scoring logic
recommend() already applies to the curated 91 for an arbitrary
(params_b, is_moe, context) tuple; register_custom_model()/load_models()
let a user-imported model become a full catalog citizen without touching
the shipped, read-only catalog file. _estimate_vram's existing signature
and the shipped-catalog cache are both untouched."
```

---

### Task 3: HF repo metadata fetch + architecture pre-check

**Files:**
- Modify: `lac_pro/hf_import.py` (append)
- Test: `tests/test_hf_import.py` (append)

**Interfaces:**
- Consumes: nothing new
- Produces: `fetch_hf_model_info(repo_id: str, fetch_fn=None) -> dict` (raises `HfImportError` subclasses, below); `SUPPORTED_ARCHITECTURES: set[str]`; `check_architecture_supported(hf_info: dict) -> None` (raises `ArchitectureUnsupportedError` if unsupported, returns None if fine).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hf_import.py`:

```python
def test_fetch_hf_model_info_calls_correct_url_and_parses_json():
    from lac_pro import hf_import as hf_import_mod

    def fake_fetch(url, timeout=30):
        assert url == "https://huggingface.co/api/models/myorg/mymodel?blobs=true"
        return {
            "config": {"architectures": ["Qwen2ForCausalLM"]},
            "siblings": [{"rfilename": "config.json", "size": 659}],
        }

    info = hf_import_mod.fetch_hf_model_info("myorg/mymodel", fetch_fn=fake_fetch)
    assert info["config"]["architectures"] == ["Qwen2ForCausalLM"]


def test_check_architecture_supported_passes_for_known_architecture():
    from lac_pro.hf_import import check_architecture_supported

    check_architecture_supported({"config": {"architectures": ["Qwen2ForCausalLM"]}})  # must not raise


def test_check_architecture_supported_raises_for_unknown_architecture():
    from lac_pro.hf_import import check_architecture_supported, ArchitectureUnsupportedError

    with pytest.raises(ArchitectureUnsupportedError) as exc_info:
        check_architecture_supported({"config": {"architectures": ["SomeVisionProjectorModel"]}})
    assert "SomeVisionProjectorModel" in str(exc_info.value)


def test_check_architecture_supported_raises_when_no_architecture_declared():
    from lac_pro.hf_import import check_architecture_supported, ArchitectureUnsupportedError

    with pytest.raises(ArchitectureUnsupportedError):
        check_architecture_supported({"config": {}})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -k "fetch_hf_model_info or check_architecture_supported" -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `AttributeError`/`ImportError`, none of these names exist yet.

- [ ] **Step 3: Implement**

Append to `lac_pro/hf_import.py`:

```python
class HfImportError(Exception):
    """Base class for the four honest failure states (spec §4)."""


class ArchitectureUnsupportedError(HfImportError):
    pass


class InsufficientDiskSpaceError(HfImportError):
    pass


class DownloadFailedError(HfImportError):
    pass


class ConversionFailedError(HfImportError):
    pass


def _default_fetch(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_hf_model_info(repo_id: str, fetch_fn=None) -> dict:
    """One HTTP call to Hugging Face's public model API, requesting blob
    metadata (file sizes + LFS info) alongside the usual repo card --
    gives us both the architecture (for the pre-check below) and every
    file's real size (for the disk-space pre-check, Task 4) in a single
    round trip, with zero weight bytes downloaded yet."""
    fetch_fn = fetch_fn or _default_fetch
    url = f"https://huggingface.co/api/models/{repo_id}?blobs=true"
    try:
        return fetch_fn(url, timeout=30)
    except urllib.error.HTTPError as e:
        raise DownloadFailedError(f"Hugging Face repo '{repo_id}' not found or inaccessible (HTTP {e.code}).") from e
    except Exception as e:  # noqa: BLE001 — any transport failure here is a download-stage failure
        raise DownloadFailedError(f"Could not reach Hugging Face for '{repo_id}': {e}") from e


# Architectures llama.cpp's convert_hf_to_gguf.py (and therefore Ollama's
# own internal safetensors import) is known to support, as of this
# feature's build date (2026-07-05). This is a maintained allowlist, not
# derived by parsing llama.cpp's source at runtime -- refresh it
# periodically (llama.cpp's own `convert_hf_to_gguf.py --print-supported-models`
# is the authoritative source when this list needs updating). This is the
# exact boundary spec §3 calls out: closes MOST of the "cool new HF model"
# gap, not literally all of it -- vision-language / multimodal-projector
# architectures in particular are commonly NOT on this list (this is
# precisely why Ornith-1.0-9B, the model that originally motivated this
# feature, would still correctly fail this check).
SUPPORTED_ARCHITECTURES: set[str] = {
    "LlamaForCausalLM", "Qwen2ForCausalLM", "Qwen2MoeForCausalLM", "Qwen3ForCausalLM",
    "Qwen3MoeForCausalLM", "MistralForCausalLM", "MixtralForCausalLM", "GemmaForCausalLM",
    "Gemma2ForCausalLM", "Gemma3ForCausalLM", "Phi3ForCausalLM", "PhiForCausalLM",
    "FalconForCausalLM", "GPT2LMHeadModel", "GPTNeoXForCausalLM", "MPTForCausalLM",
    "StableLmForCausalLM", "DeepseekForCausalLM", "DeepseekV2ForCausalLM",
    "DeepseekV3ForCausalLM", "CohereForCausalLM", "Starcoder2ForCausalLM",
    "ChatGLMModel", "InternLM2ForCausalLM", "MiniCPMForCausalLM", "OlmoForCausalLM",
    "OlmoeForCausalLM", "ExaoneForCausalLM", "GraniteForCausalLM", "DbrxForCausalLM",
    "NemotronForCausalLM", "BloomForCausalLM",
}


def check_architecture_supported(hf_info: dict) -> None:
    """Raises ArchitectureUnsupportedError with a specific, honest message
    naming the actual architecture found, or returns None if it's
    convertible. Runs BEFORE any download starts (spec §3/§4 -- fail fast,
    fail specific)."""
    architectures = (hf_info.get("config") or {}).get("architectures") or []
    if not architectures:
        raise ArchitectureUnsupportedError(
            "This repo doesn't declare a model architecture in its config.json "
            "-- LAC can't tell what it is, so it can't be converted."
        )
    if not any(a in SUPPORTED_ARCHITECTURES for a in architectures):
        raise ArchitectureUnsupportedError(
            f"'{architectures[0]}' isn't a supported architecture yet -- LAC can only "
            f"convert models llama.cpp knows how to load (most text-generation Llama/"
            f"Qwen/Mistral/Gemma/Phi-family models and similar). Multimodal or vision-"
            f"projector models in particular usually aren't supported."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS (this task's 4 new + Task 1's 3 non-live tests = 7 total).

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/hf_import.py tests/test_hf_import.py
git commit -m "feat(hf_import): architecture pre-check, fails fast before any download

fetch_hf_model_info() gets a Hugging Face repo's architecture AND every
file's size in one API call (?blobs=true) -- zero weight bytes touched
yet. check_architecture_supported() raises a specific, honest error
naming the actual unsupported architecture (this is precisely the
Ornith-1.0-9B case that motivated this whole feature -- its vision-
projector head correctly still fails this check, communicated clearly
instead of silently)."
```

---

### Task 4: Disk-space pre-check

**Files:**
- Modify: `lac_pro/hf_import.py` (append)
- Test: `tests/test_hf_import.py` (append)

**Interfaces:**
- Consumes: `HfImportError`, `InsufficientDiskSpaceError` (Task 3)
- Produces: `total_download_size_bytes(hf_info: dict) -> int`; `check_disk_space(hf_info: dict, scratch_dir: Path, disk_usage_fn=None) -> None` (raises `InsufficientDiskSpaceError` or returns None)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hf_import.py`:

```python
def test_total_download_size_bytes_sums_siblings():
    from lac_pro.hf_import import total_download_size_bytes

    info = {"siblings": [{"rfilename": "config.json", "size": 659},
                          {"rfilename": "model.safetensors", "size": 1_000_000_000}]}
    assert total_download_size_bytes(info) == 1_000_000_659


def test_check_disk_space_passes_when_enough_free(tmp_path):
    from lac_pro.hf_import import check_disk_space

    info = {"siblings": [{"rfilename": "model.safetensors", "size": 1_000_000_000}]}

    def fake_usage(path):
        class Usage:
            free = 100_000_000_000  # 100GB free, plenty
        return Usage()

    check_disk_space(info, tmp_path, disk_usage_fn=fake_usage)  # must not raise


def test_check_disk_space_raises_when_insufficient(tmp_path):
    from lac_pro.hf_import import check_disk_space, InsufficientDiskSpaceError

    info = {"siblings": [{"rfilename": "model.safetensors", "size": 18_000_000_000}]}  # 18GB model

    def fake_usage(path):
        class Usage:
            free = 5_000_000_000  # only 5GB free -- needs ~2x model size
        return Usage()

    with pytest.raises(InsufficientDiskSpaceError) as exc_info:
        check_disk_space(info, tmp_path, disk_usage_fn=fake_usage)
    assert "GB" in str(exc_info.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -k "disk_space or total_download_size" -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `AttributeError`, `total_download_size_bytes`/`check_disk_space` don't exist yet.

- [ ] **Step 3: Implement**

Append to `lac_pro/hf_import.py`:

```python
import shutil

DISK_SPACE_SAFETY_FACTOR = 2.0  # raw download + Ollama's internal FP16 intermediate (spec §3)


def total_download_size_bytes(hf_info: dict) -> int:
    return sum(s.get("size", 0) for s in hf_info.get("siblings", []))


def check_disk_space(hf_info: dict, scratch_dir: Path, disk_usage_fn=None) -> None:
    """Raises InsufficientDiskSpaceError if there isn't roughly 2x the
    model's total file size free on the volume scratch_dir lives on --
    covers the raw download plus Ollama's own internal FP16 conversion
    step before quantizing (spec §3). Runs before any download starts."""
    disk_usage_fn = disk_usage_fn or shutil.disk_usage
    needed = int(total_download_size_bytes(hf_info) * DISK_SPACE_SAFETY_FACTOR)
    free = disk_usage_fn(scratch_dir).free
    if free < needed:
        needed_gb = needed / (1024**3)
        free_gb = free / (1024**3)
        raise InsufficientDiskSpaceError(
            f"Not enough free disk space: this model needs about {needed_gb:.1f}GB free "
            f"during conversion, but only {free_gb:.1f}GB is available."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS (10 total non-live tests so far).

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/hf_import.py tests/test_hf_import.py
git commit -m "feat(hf_import): disk-space pre-check before any download starts

Needs ~2x the model's total file size free (raw download + Ollama's
internal FP16 intermediate before quantizing, per the spec's research).
Fails with a specific GB-vs-GB message, not a mid-download disk-full
crash."
```

---

### Task 5: Download orchestration + guaranteed scratch-dir cleanup

**Files:**
- Modify: `lac_pro/hf_import.py` (append)
- Test: `tests/test_hf_import.py` (append)

**Interfaces:**
- Consumes: `DownloadFailedError` (Task 3), `sha256_file`/`upload_blob` (Task 1)
- Produces: `download_model_files(repo_id: str, hf_info: dict, scratch_dir: Path, fetch_bytes_fn=None) -> dict[str, Path]` (returns `{filename: local_path}`, raises `DownloadFailedError` on any failure, guarantees `scratch_dir` is removed if it raises)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hf_import.py`:

```python
def test_download_model_files_writes_each_sibling_and_returns_paths(tmp_path):
    from lac_pro.hf_import import download_model_files

    info = {"siblings": [{"rfilename": "config.json", "size": 5},
                          {"rfilename": "model.safetensors", "size": 5}]}
    fake_bytes = {"config.json": b"AAAAA", "model.safetensors": b"BBBBB"}

    def fake_fetch_bytes(url, timeout=60):
        for fname, content in fake_bytes.items():
            if fname in url:
                return content
        raise AssertionError(f"unexpected url {url}")

    scratch = tmp_path / "scratch"
    paths = download_model_files("myorg/mymodel", info, scratch, fetch_bytes_fn=fake_fetch_bytes)

    assert paths["config.json"].read_bytes() == b"AAAAA"
    assert paths["model.safetensors"].read_bytes() == b"BBBBB"
    assert paths["config.json"].parent == scratch


def test_download_model_files_skips_readme_and_gitattributes(tmp_path):
    from lac_pro.hf_import import download_model_files

    info = {"siblings": [{"rfilename": "README.md", "size": 5},
                          {"rfilename": ".gitattributes", "size": 5},
                          {"rfilename": "config.json", "size": 5}]}

    def fake_fetch_bytes(url, timeout=60):
        return b"AAAAA"

    scratch = tmp_path / "scratch"
    paths = download_model_files("myorg/mymodel", info, scratch, fetch_bytes_fn=fake_fetch_bytes)
    assert set(paths.keys()) == {"config.json"}


def test_download_model_files_cleans_up_scratch_dir_on_failure(tmp_path):
    from lac_pro.hf_import import download_model_files, DownloadFailedError

    info = {"siblings": [{"rfilename": "config.json", "size": 5},
                          {"rfilename": "model.safetensors", "size": 5}]}

    calls = {"n": 0}

    def failing_fetch_bytes(url, timeout=60):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("network dropped")
        return b"AAAAA"

    scratch = tmp_path / "scratch"
    with pytest.raises(DownloadFailedError):
        download_model_files("myorg/mymodel", info, scratch, fetch_bytes_fn=failing_fetch_bytes)

    assert not scratch.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -k download_model_files -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `AttributeError`, `download_model_files` doesn't exist yet.

- [ ] **Step 3: Implement**

Append to `lac_pro/hf_import.py`:

```python
_SKIP_FILES = {"README.md", ".gitattributes"}


def _default_fetch_bytes(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def download_model_files(repo_id: str, hf_info: dict, scratch_dir: Path,
                          fetch_bytes_fn=None) -> dict[str, Path]:
    """Download every non-doc file from the HF repo into scratch_dir.
    Returns {filename: local_path}. On ANY failure, the entire scratch_dir
    is removed before the error propagates -- no partial downloads left
    behind (spec §4)."""
    fetch_bytes_fn = fetch_bytes_fn or _default_fetch_bytes
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        paths: dict[str, Path] = {}
        for sib in hf_info.get("siblings", []):
            fname = sib["rfilename"]
            if fname in _SKIP_FILES:
                continue
            url = f"https://huggingface.co/{repo_id}/resolve/main/{fname}"
            try:
                content = fetch_bytes_fn(url, timeout=60)
            except Exception as e:  # noqa: BLE001 — any transport failure is a download failure
                raise DownloadFailedError(f"Failed to download '{fname}' from '{repo_id}': {e}") from e
            dest = scratch_dir / fname
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            paths[fname] = dest
        return paths
    except Exception:
        shutil.rmtree(scratch_dir, ignore_errors=True)
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS (13 total non-live tests so far).

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/hf_import.py tests/test_hf_import.py
git commit -m "feat(hf_import): download orchestration with guaranteed scratch-dir cleanup

Downloads every non-doc file from a Hugging Face repo into a scratch
directory. Any failure mid-download removes the entire scratch directory
before the error propagates -- no partial state left on disk (spec §4)."
```

---

### Task 6: Full pipeline orchestration, status tracking, and Autopilot handoff

This is where Tasks 1-5's pieces get wired together into the one function everything else (CLI, API) calls.

**Files:**
- Modify: `lac_pro/hf_import.py` (append)
- Test: `tests/test_hf_import.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1-5; `backend.cookbook.recommend.best_fit_quant`/`register_custom_model` (Task 2); `backend.cookbook.hardware.detect` (existing, unchanged); `lac_pro.autopilot.run_autopilot` (existing, unchanged)
- Produces: `import_custom_model(repo_id: str, quant_override: str | None = None, ollama_host: str = "http://localhost:11434") -> dict` (the one function Tasks 7/8 call); `read_import_status(repo_id: str) -> dict`; `IMPORT_STATUS_PATH: Path`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hf_import.py`:

```python
@pytest.fixture
def isolated_import_status(tmp_path, monkeypatch):
    from lac_pro import hf_import as hf_import_mod
    status = tmp_path / "import_status.json"
    monkeypatch.setattr(hf_import_mod, "IMPORT_STATUS_PATH", status)
    return status


def test_import_custom_model_happy_path_registers_catalog_and_runs_autopilot(
    tmp_path, monkeypatch, isolated_import_status,
):
    from lac_pro import hf_import as hf_import_mod

    fake_info = {
        "config": {"architectures": ["Qwen2ForCausalLM"], "max_position_embeddings": 8192},
        "siblings": [{"rfilename": "config.json", "size": 5}],
    }
    monkeypatch.setattr(hf_import_mod, "fetch_hf_model_info", lambda repo_id, fetch_fn=None: fake_info)
    monkeypatch.setattr(hf_import_mod, "check_disk_space", lambda info, scratch, disk_usage_fn=None: None)
    monkeypatch.setattr(hf_import_mod, "download_model_files",
                         lambda repo_id, info, scratch, fetch_bytes_fn=None: {"config.json": scratch / "config.json"})
    monkeypatch.setattr(hf_import_mod, "sha256_file", lambda path: "deadbeef")
    monkeypatch.setattr(hf_import_mod, "upload_blob", lambda digest, path, host: None)
    monkeypatch.setattr(hf_import_mod, "ollama_create_from_files",
                         lambda name, files, quant, host: {"status": "success"})

    from backend.cookbook.hardware import SystemInfo
    monkeypatch.setattr(hf_import_mod, "detect_hardware",
                         lambda: SystemInfo(os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
                                              gpus=[], total_vram_gb=16.0, combined_vram_gb=16.0,
                                              compute_tiers=[]))

    registered = {}
    monkeypatch.setattr(hf_import_mod, "register_custom_model", lambda entry: registered.update(entry))

    autopilot_calls = []
    monkeypatch.setattr(hf_import_mod, "run_autopilot", lambda model: autopilot_calls.append(model))

    result = hf_import_mod.import_custom_model("myorg/mymodel")

    assert result["state"] == "done"
    assert registered["id"] == "myorg/mymodel"
    assert len(autopilot_calls) == 1

    status = hf_import_mod.read_import_status("myorg/mymodel")
    assert status["state"] == "done"


def test_import_custom_model_architecture_unsupported_records_failed_state(monkeypatch, isolated_import_status):
    from lac_pro import hf_import as hf_import_mod

    monkeypatch.setattr(hf_import_mod, "fetch_hf_model_info",
                         lambda repo_id, fetch_fn=None: {"config": {"architectures": ["WeirdVisionModel"]}, "siblings": []})

    result = hf_import_mod.import_custom_model("myorg/weird-model")

    assert result["state"] == "failed"
    assert result["error_type"] == "architecture_unsupported"
    assert "WeirdVisionModel" in result["message"]


def test_import_custom_model_conversion_failure_cleans_up_scratch_dir(tmp_path, monkeypatch, isolated_import_status):
    from lac_pro import hf_import as hf_import_mod

    fake_info = {"config": {"architectures": ["Qwen2ForCausalLM"], "max_position_embeddings": 8192},
                 "siblings": [{"rfilename": "config.json", "size": 5}]}
    monkeypatch.setattr(hf_import_mod, "fetch_hf_model_info", lambda repo_id, fetch_fn=None: fake_info)
    monkeypatch.setattr(hf_import_mod, "check_disk_space", lambda info, scratch, disk_usage_fn=None: None)

    written_scratch = {}

    def fake_download(repo_id, info, scratch, fetch_bytes_fn=None):
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "config.json").write_bytes(b"x")
        written_scratch["path"] = scratch
        return {"config.json": scratch / "config.json"}

    monkeypatch.setattr(hf_import_mod, "download_model_files", fake_download)
    monkeypatch.setattr(hf_import_mod, "sha256_file", lambda path: "deadbeef")
    monkeypatch.setattr(hf_import_mod, "upload_blob", lambda digest, path, host: None)

    def failing_create(name, files, quant, host):
        raise RuntimeError("ollama rejected the model")
    monkeypatch.setattr(hf_import_mod, "ollama_create_from_files", failing_create)

    from backend.cookbook.hardware import SystemInfo
    monkeypatch.setattr(hf_import_mod, "detect_hardware",
                         lambda: SystemInfo(os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
                                              gpus=[], total_vram_gb=16.0, combined_vram_gb=16.0,
                                              compute_tiers=[]))

    result = hf_import_mod.import_custom_model("myorg/mymodel")

    assert result["state"] == "failed"
    assert result["error_type"] == "conversion_failed"
    assert not written_scratch["path"].exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -k import_custom_model -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `AttributeError`, `import_custom_model`/`read_import_status`/`IMPORT_STATUS_PATH`/`detect_hardware`/`run_autopilot` (as module attributes of `hf_import`) don't exist yet.

- [ ] **Step 3: Implement**

Append to `lac_pro/hf_import.py`:

```python
import tempfile
import time

from backend.cookbook.hardware import detect as detect_hardware
from backend.cookbook.recommend import best_fit_quant, register_custom_model
from lac_pro.autopilot import run_autopilot

IMPORT_STATUS_PATH = Path.home() / ".model-hub" / "pro_import_status.json"


def _read_import_status_all() -> dict:
    try:
        return json.loads(IMPORT_STATUS_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt == empty
        return {}


def _write_import_status(repo_id: str, entry: dict) -> None:
    try:
        IMPORT_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = _read_import_status_all()
        data[repo_id] = entry
        IMPORT_STATUS_PATH.write_text(json.dumps(data))
    except Exception:  # noqa: BLE001 — status tracking must never break the import
        pass


def read_import_status(repo_id: str) -> dict:
    return _read_import_status_all().get(repo_id, {"state": "idle"})


def _ollama_model_name(repo_id: str) -> str:
    return repo_id.replace("/", "-").lower() + ":latest"


def import_custom_model(repo_id: str, quant_override: str | None = None,
                         ollama_host: str = "http://localhost:11434") -> dict:
    """The full pipeline (spec §3): metadata fetch -> architecture check ->
    disk-space check -> download -> blob-upload each file -> quantized
    create -> catalog registration -> Autopilot handoff. Never raises --
    every failure is caught and recorded as one of the four honest states
    (spec §4), returned as a dict and also persisted via
    _write_import_status so a caller polling read_import_status() sees
    the same result."""
    _write_import_status(repo_id, {"state": "checking", "updated_at": time.time()})
    scratch_dir = Path(tempfile.gettempdir()) / "lac-hf-import" / repo_id.replace("/", "_")

    try:
        hf_info = fetch_hf_model_info(repo_id)
        check_architecture_supported(hf_info)
        check_disk_space(hf_info, Path(tempfile.gettempdir()))

        _write_import_status(repo_id, {"state": "downloading", "updated_at": time.time()})
        local_files = download_model_files(repo_id, hf_info, scratch_dir)

        config = hf_info.get("config") or {}
        params_b = _estimate_params_b(local_files, config)
        context = int(config.get("max_position_embeddings") or 4096)
        is_moe = "num_experts" in config or "num_local_experts" in config

        info = detect_hardware()
        available_vram = info.combined_vram_gb or info.total_vram_gb
        quant_name, _ = (
            (quant_override, None) if quant_override
            else best_fit_quant(params_b, is_moe, None, context, available_vram)
        )

        _write_import_status(repo_id, {"state": "converting", "updated_at": time.time()})
        files_for_create = {}
        for fname, path in local_files.items():
            digest = "sha256:" + sha256_file(path)
            upload_blob(digest, path, ollama_host)
            files_for_create[fname] = digest

        ollama_quant_name = _to_ollama_quantize_value(quant_name)
        model_name = _ollama_model_name(repo_id)
        create_result = ollama_create_from_files(model_name, files_for_create, ollama_quant_name, ollama_host)
        if "error" in create_result:
            raise ConversionFailedError(str(create_result["error"]))

        shutil.rmtree(scratch_dir, ignore_errors=True)

        register_custom_model({
            "id": repo_id, "name": repo_id.split("/")[-1], "provider": "custom",
            "params_b": params_b, "arch": (config.get("architectures") or ["custom"])[0],
            "context": context, "use_cases": ["general"], "is_moe": is_moe,
        })
        run_autopilot(model_name)

        result = {"state": "done", "model_name": model_name, "quant": quant_name, "updated_at": time.time()}
        _write_import_status(repo_id, result)
        return result

    except ArchitectureUnsupportedError as e:
        result = {"state": "failed", "error_type": "architecture_unsupported", "message": str(e), "updated_at": time.time()}
    except InsufficientDiskSpaceError as e:
        result = {"state": "failed", "error_type": "insufficient_disk", "message": str(e), "updated_at": time.time()}
    except DownloadFailedError as e:
        result = {"state": "failed", "error_type": "download_failed", "message": str(e), "updated_at": time.time()}
    except ConversionFailedError as e:
        shutil.rmtree(scratch_dir, ignore_errors=True)
        result = {"state": "failed", "error_type": "conversion_failed", "message": str(e), "updated_at": time.time()}
    except Exception as e:  # noqa: BLE001 — an unexpected failure is still a conversion-stage failure, not a crash
        shutil.rmtree(scratch_dir, ignore_errors=True)
        result = {"state": "failed", "error_type": "conversion_failed", "message": str(e), "updated_at": time.time()}

    _write_import_status(repo_id, result)
    return result


def _to_ollama_quantize_value(quant_name: str) -> str | None:
    """Map LAC's internal QUANTS table names (e.g. "Q4_K_M", "Q8", "F16")
    to the string Ollama's `quantize` field expects. Ollama's own
    convention lowercases just the leading letter (e.g. "q4_K_M", "q8_0").
    "F16" means "don't quantize, keep source precision" -- best_fit_quant
    picks it when the hardware can comfortably hold the full-precision
    model, and it maps to None (no quantize field sent at all) rather
    than a literal quantize value, since F16 is a source precision, not
    a quantize target.

    CONFIRM THIS MAPPING against Task 1's actual spike findings before
    trusting it beyond the values that spike exercised directly -- this
    is exactly the kind of low-level format detail Task 1 exists to
    nail down empirically, not guess."""
    if quant_name == "F16":
        return None
    return quant_name[0].lower() + quant_name[1:]


def _estimate_params_b(local_files: dict, config: dict) -> float:
    """Rough parameter count from config.json's dimensions -- good enough
    to pick a sane quant (best_fit_quant only needs an approximate params_b,
    the same precision the curated catalog's own hand-entered params_b
    values already have). hidden_size * intermediate_size * num_hidden_layers
    is the dominant term for a standard transformer decoder."""
    hidden = config.get("hidden_size", 0)
    layers = config.get("num_hidden_layers", 0)
    intermediate = config.get("intermediate_size", hidden * 4)
    vocab = config.get("vocab_size", 0)
    if not (hidden and layers):
        return 7.0  # unknown shape -- mid-size default, still lets the pipeline proceed
    approx_params = layers * (4 * hidden * hidden + 2 * hidden * intermediate) + 2 * vocab * hidden
    return round(approx_params / 1e9, 2)
```

Note: `is_moe`/MoE `active_params_b` auto-detection from `config.json` is intentionally simple (checks for common `num_experts`/`num_local_experts` keys) — this is a best-effort signal, not a MoE-architecture-family exhaustive list. If `is_moe` is wrongly `True`/`False` for an unusual config, `best_fit_quant` still degrades gracefully (worst case: a slightly conservative or slightly generous quant pick, never a crash).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_hf_import.py -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS (16 total non-live tests so far).

Then run the full lac-pro suite: `.venv\Scripts\python.exe -m pytest -q -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests pass, no regressions in the existing 64.

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/hf_import.py tests/test_hf_import.py
git commit -m "feat(hf_import): full pipeline orchestration + catalog registration + Autopilot handoff

import_custom_model() wires Tasks 1-5 together: metadata fetch ->
architecture check -> disk-space check -> download -> blob-upload ->
quantized create -> catalog registration -> Autopilot benchmark+tune,
exactly matching a fresh curated-catalog install from this point on
(spec 2026-07-05, decision 4). Never raises -- every failure path
records one of the four honest states via _write_import_status and
guarantees scratch-dir cleanup, matching spec §4's contract."
```

---

### Task 7: CLI command — `lac pro import <repo_id>`

**Files:**
- Create: `lac_pro/import_cli.py`
- Modify: `lac_pro/plugin.py:9-19` (register the new subcommand)
- Test: `tests/test_import_cli.py` (new)

**Interfaces:**
- Consumes: `import_custom_model` (Task 6), `require` (existing, `lac_pro/license.py`)
- Produces: `cmd_import(args) -> None`, `configure_parser(parser) -> None` in `lac_pro/import_cli.py`, registered as `lac pro import` via `plugin.py`'s `_SUBCOMMANDS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_import_cli.py`:

```python
from __future__ import annotations

import argparse

import pytest


def test_cmd_import_requires_license(monkeypatch, tmp_path, capsys):
    """Must not read the real dev machine's ~/.model-hub/license.json --
    patch lac_pro.license.GRANT_PATH to a path that doesn't exist, the
    same isolation convention test_license.py/test_tune.py already use,
    so this test's result doesn't depend on whether this machine happens
    to have a real Pro license activated."""
    import lac_pro.license as license_mod
    from lac_pro import import_cli

    monkeypatch.delenv("LAC_PRO_DEV", raising=False)
    monkeypatch.setattr(license_mod, "GRANT_PATH", tmp_path / "license.json")

    args = argparse.Namespace(repo_id="myorg/mymodel", quant=None)
    with pytest.raises(SystemExit) as exc_info:
        import_cli.cmd_import(args)
    assert exc_info.value.code == 3


def test_cmd_import_calls_import_custom_model_and_prints_success(monkeypatch, capsys):
    from lac_pro import import_cli

    monkeypatch.setenv("LAC_PRO_DEV", "1")
    monkeypatch.setattr(import_cli, "import_custom_model",
                         lambda repo_id, quant_override=None: {"state": "done", "model_name": "myorg-mymodel:latest", "quant": "Q4_K_M"})

    args = argparse.Namespace(repo_id="myorg/mymodel", quant=None)
    import_cli.cmd_import(args)

    out = capsys.readouterr().out
    assert "myorg-mymodel:latest" in out
    assert "Q4_K_M" in out


def test_cmd_import_prints_failure_message_and_exits_1(monkeypatch, capsys):
    from lac_pro import import_cli

    monkeypatch.setenv("LAC_PRO_DEV", "1")
    monkeypatch.setattr(import_cli, "import_custom_model",
                         lambda repo_id, quant_override=None: {
                             "state": "failed", "error_type": "architecture_unsupported",
                             "message": "'WeirdModel' isn't supported yet.",
                         })

    args = argparse.Namespace(repo_id="myorg/weird-model", quant=None)
    with pytest.raises(SystemExit) as exc_info:
        import_cli.cmd_import(args)
    assert exc_info.value.code == 1

    err = capsys.readouterr().err
    assert "WeirdModel" in err


def test_cmd_import_passes_quant_override(monkeypatch):
    from lac_pro import import_cli

    monkeypatch.setenv("LAC_PRO_DEV", "1")
    captured = {}
    monkeypatch.setattr(import_cli, "import_custom_model",
                         lambda repo_id, quant_override=None: captured.update(
                             {"repo_id": repo_id, "quant_override": quant_override}) or
                             {"state": "done", "model_name": "x:latest", "quant": quant_override or "auto"})

    args = argparse.Namespace(repo_id="myorg/mymodel", quant="Q5_K_M")
    import_cli.cmd_import(args)

    assert captured["quant_override"] == "Q5_K_M"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_import_cli.py` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `ModuleNotFoundError: No module named 'lac_pro.import_cli'`.

- [ ] **Step 3: Implement**

Create `lac_pro/import_cli.py`:

```python
"""`lac pro import` -- paste a Hugging Face repo ID, LAC downloads,
converts+quantizes, installs, and hands it to Autopilot. Pro-gated the
same as `lac pro tune`/`lac pro benchmark` (require(), not check() --
this is a direct user-invoked command, not the silent Autopilot hook).
See docs/superpowers/specs/2026-07-05-lac-pro-custom-model-import-design.md.
"""
from __future__ import annotations

from lac_pro.hf_import import import_custom_model
from lac_pro.license import require


def cmd_import(args) -> None:
    require("import")
    print(f"Importing {args.repo_id} from Hugging Face…")
    result = import_custom_model(args.repo_id, quant_override=args.quant)

    if result["state"] == "done":
        print(f"  Installed as {result['model_name']} (quant: {result['quant']})")
        print("  Benchmarking + tuning automatically via Autopilot…")
        return

    print(f"Import failed [{result.get('error_type', 'unknown')}]: {result.get('message', '')}", file=__import__("sys").stderr)
    raise SystemExit(1)


def configure_parser(parser) -> None:
    parser.add_argument("repo_id", help="Hugging Face repo ID, e.g. deepreinforce-ai/Ornith-1.0-9B")
    parser.add_argument("--quant", default=None,
                         help="Manual quant override (e.g. Q4_K_M) -- default: auto-picked for your hardware")
    parser.set_defaults(func=cmd_import)
```

Modify `lac_pro/plugin.py` lines 9-19: add the import and register the subcommand. Replace:

```python
from lac_pro import tune as _tune  # noqa: E402 — after _SUBCOMMANDS; tune never imports plugin
from lac_pro import activate as _activate  # noqa: E402
from lac_pro import insights as _insights  # noqa: E402
from lac_pro import benchmark_cli as _benchmark_cli  # noqa: E402

_SUBCOMMANDS.append(("tune", "Sweep offload configs and find the fastest for this rig", _tune.configure_parser))
_SUBCOMMANDS.append(("activate", "Activate a LAC Pro license key on this machine", _activate.configure_activate))
_SUBCOMMANDS.append(("deactivate", "Deactivate this machine's license seat", _activate.configure_deactivate))
_SUBCOMMANDS.append(("insights", "Calibration history + regression detection", _insights.configure_parser))
_SUBCOMMANDS.append(("benchmark", "Benchmark a model's tok/s via Ollama (on-demand re-run)", _benchmark_cli.configure_parser))
```

with:

```python
from lac_pro import tune as _tune  # noqa: E402 — after _SUBCOMMANDS; tune never imports plugin
from lac_pro import activate as _activate  # noqa: E402
from lac_pro import insights as _insights  # noqa: E402
from lac_pro import benchmark_cli as _benchmark_cli  # noqa: E402
from lac_pro import import_cli as _import_cli  # noqa: E402

_SUBCOMMANDS.append(("tune", "Sweep offload configs and find the fastest for this rig", _tune.configure_parser))
_SUBCOMMANDS.append(("activate", "Activate a LAC Pro license key on this machine", _activate.configure_activate))
_SUBCOMMANDS.append(("deactivate", "Deactivate this machine's license seat", _activate.configure_deactivate))
_SUBCOMMANDS.append(("insights", "Calibration history + regression detection", _insights.configure_parser))
_SUBCOMMANDS.append(("benchmark", "Benchmark a model's tok/s via Ollama (on-demand re-run)", _benchmark_cli.configure_parser))
_SUBCOMMANDS.append(("import", "Import any compatible model from Hugging Face", _import_cli.configure_parser))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_import_cli.py tests/test_plugin.py` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS. `tests/test_plugin.py`'s existing subcommand-registration tests (if any check the full `_SUBCOMMANDS` list) may need a quick look — if one asserts an exact count or exact list of subcommand names, it needs updating to include `"import"`; check `tests/test_plugin.py`'s content first before assuming it needs a change.

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/import_cli.py lac_pro/plugin.py tests/test_import_cli.py
git commit -m "feat(cli): lac pro import <repo_id> -- Pro-gated custom Hugging Face model import

Paste a repo ID, get a clean success message with the resulting model
name + quant, or a specific failure message naming exactly what went
wrong (spec §4). Gated via require(), matching lac pro tune/benchmark --
this is a direct, user-invoked command."
```

---

### Task 8: API routes — kick off + poll import status

**Files:**
- Modify: `lac_pro/plugin.py` (`register_api`)
- Test: `tests/test_plugin.py` (append)

**Interfaces:**
- Consumes: `import_custom_model`, `read_import_status` (Task 6)
- Produces: `POST /api/pro/import-model` (body: `{"repo_id": str, "quant": str | null}`, returns `{"accepted": true}` immediately, runs the import in a background thread), `GET /api/pro/import-status?repo_id=<id>` (returns the same shape `read_import_status` does, plus `not_licensed` when unlicensed — mirrors `optimize_status`'s existing state machine).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plugin.py` (check its current imports/fixtures first — it very likely already has a Flask `app`/`client` fixture pattern from the existing `/api/pro/optimize-status` route's own tests; match that pattern rather than reinventing one):

```python
def test_import_model_route_returns_not_licensed_when_unlicensed(monkeypatch, tmp_path):
    """Same GRANT_PATH isolation as test_import_cli.py's license test --
    must not depend on this machine's real ~/.model-hub/license.json."""
    from flask import Flask
    import lac_pro.license as license_mod
    from lac_pro.plugin import PLUGIN

    monkeypatch.delenv("LAC_PRO_DEV", raising=False)
    monkeypatch.setattr(license_mod, "GRANT_PATH", tmp_path / "license.json")
    app = Flask(__name__)
    PLUGIN.register_api(app)
    client = app.test_client()

    r = client.get("/api/pro/import-status?repo_id=myorg/mymodel")
    assert r.status_code == 200
    assert r.get_json()["state"] == "not_licensed"


def test_import_model_route_kicks_off_background_import(monkeypatch):
    from flask import Flask
    import lac_pro.plugin as plugin_mod
    from lac_pro.plugin import PLUGIN

    monkeypatch.setenv("LAC_PRO_DEV", "1")
    calls = []
    monkeypatch.setattr(plugin_mod, "import_custom_model", lambda repo_id, quant_override=None: calls.append((repo_id, quant_override)))

    app = Flask(__name__)
    PLUGIN.register_api(app)
    client = app.test_client()

    r = client.post("/api/pro/import-model", json={"repo_id": "myorg/mymodel", "quant": "Q5_K_M"})
    assert r.status_code == 200
    assert r.get_json() == {"accepted": True}

    import time
    for _ in range(20):
        if calls:
            break
        time.sleep(0.05)
    assert calls == [("myorg/mymodel", "Q5_K_M")]


def test_import_model_route_requires_repo_id(monkeypatch):
    from flask import Flask
    from lac_pro.plugin import PLUGIN

    monkeypatch.setenv("LAC_PRO_DEV", "1")
    app = Flask(__name__)
    PLUGIN.register_api(app)
    client = app.test_client()

    r = client.post("/api/pro/import-model", json={})
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_plugin.py -k import_model` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `/api/pro/import-model`/`/api/pro/import-status` routes don't exist yet (404s).

- [ ] **Step 3: Implement**

Replace `lac_pro/plugin.py`'s `register_api` method:

```python
    def register_api(self, app) -> None:
        from flask import jsonify, request
        from lac_pro.autopilot import optimize_status

        @app.route("/api/pro/optimize-status")
        def _pro_optimize_status():
            model = request.args.get("model", "").strip()
            body, code = optimize_status(model)
            return jsonify(body), code
```

with:

```python
    def register_api(self, app) -> None:
        from flask import jsonify, request
        from lac_pro.autopilot import optimize_status

        @app.route("/api/pro/optimize-status")
        def _pro_optimize_status():
            model = request.args.get("model", "").strip()
            body, code = optimize_status(model)
            return jsonify(body), code

        @app.route("/api/pro/import-model", methods=["POST"])
        def _pro_import_model():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not data.get("repo_id"):
                return jsonify({"error": "repo_id required"}), 400

            import threading
            repo_id = data["repo_id"]
            quant = data.get("quant")
            threading.Thread(target=import_custom_model, args=(repo_id,), kwargs={"quant_override": quant}, daemon=True).start()
            return jsonify({"accepted": True}), 200

        @app.route("/api/pro/import-status")
        def _pro_import_status():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            repo_id = request.args.get("repo_id", "").strip()
            if not repo_id:
                return jsonify({"error": "repo_id required"}), 400
            return jsonify(read_import_status(repo_id)), 200
```

Add the import at the top of `lac_pro/plugin.py`, alongside the existing `from lac_pro import __version__` line:

```python
from lac_pro.hf_import import import_custom_model, read_import_status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_plugin.py` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS.

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q -m "not live"` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/plugin.py tests/test_plugin.py
git commit -m "feat(api): POST /api/pro/import-model + GET /api/pro/import-status

Mirrors the existing optimize-status background-thread + polling shape.
Unlicensed users get {state: not_licensed} from BOTH routes -- the same
pattern optimize_status already uses -- so the frontend's existing
not_licensed handling in installer.ts needs zero new branches to support
this feature's upsell path."
```

---

### Task 9: Frontend — Pro-gated "Import from Hugging Face" UI

**Files:**
- Modify: `web/src/lib/api.ts` (add `importModel`/`importStatus` calls)
- Modify: `web/src/pages/browse.tsx` (add the import form)
- Test: none (no component-test harness in this repo) — verify via `npm run typecheck && npm run build`

**Interfaces:**
- Consumes: `POST /api/pro/import-model`, `GET /api/pro/import-status` (Task 8)
- Produces: a one-field-one-input "Import from Hugging Face" card on the Browse page; polls status the same way `pollProOptimizeStatus` already does in `installer.ts`.

- [ ] **Step 1: Add API client methods**

Modify `web/src/lib/api.ts`: add two methods to the `api` object, alongside the existing `proOptimizeStatus`:

```typescript
  /** Kick off a LAC Pro custom Hugging Face model import (background). */
  importModel: (repoId: string, quant?: string) =>
    postJSON<{ accepted?: boolean; state?: string; error?: string }>("/api/pro/import-model", { repo_id: repoId, quant: quant ?? null }),
  /** Poll a custom-model import's progress. */
  importStatus: (repoId: string) =>
    getJSON<{ state: string; error_type?: string; message?: string; model_name?: string; quant?: string }>(
      `/api/pro/import-status?repo_id=${encodeURIComponent(repoId)}`
    ),
```

- [ ] **Step 2: Add the polling helper to `installer.ts`**

Append to `web/src/lib/installer.ts` (this is the natural home — it already owns the toast/polling pattern this reuses):

```typescript
const IMPORT_POLL_MS = 3000;
const IMPORT_POLL_TIMEOUT_MS = 30 * 60 * 1000; // conversion can genuinely take many minutes

/**
 * Kick off a LAC Pro custom Hugging Face model import and poll its
 * progress via a toast, the same shape pullWithToast/pollProOptimizeStatus
 * already use. Distinct honest failure messages per state (spec §4) --
 * never a generic "something went wrong".
 */
export async function importModelWithToast(repoId: string, quant: string | undefined, onDone?: () => void) {
  const kickoff = await api.importModel(repoId, quant);
  if (kickoff.state === "not_licensed") {
    toast.info("Importing custom Hugging Face models is a LAC Pro feature.", {
      action: { label: "Get Pro", onClick: () => window.open("https://dkrynen.github.io/lac/#pro", "_blank") },
    });
    return;
  }
  if (kickoff.error) {
    toast.error(`Couldn't start import: ${kickoff.error}`);
    return;
  }

  const started = Date.now();
  const toastId = toast.loading(`Importing ${repoId} from Hugging Face…`, {
    description: "This can take several minutes — download, convert, and quantize.",
  });

  while (Date.now() - started < IMPORT_POLL_TIMEOUT_MS) {
    const status = await api.importStatus(repoId);
    if (status.state === "not_licensed") {
      toast.dismiss(toastId);
      return;
    }
    if (status.state === "done") {
      toast.success(`Imported ${status.model_name} (${status.quant})`, { id: toastId });
      onDone?.();
      return;
    }
    if (status.state === "failed") {
      toast.error(`Import failed: ${status.message ?? status.error_type}`, { id: toastId });
      return;
    }
    toast.loading(`Importing ${repoId} — ${status.state}…`, { id: toastId });
    await new Promise((resolve) => setTimeout(resolve, IMPORT_POLL_MS));
  }
  toast.dismiss(toastId);
}
```

Add `import { toast } from "sonner";` and `import { api } from "@/lib/api";` if not already at the top of `installer.ts` — check first (both are already imported per the existing file content).

- [ ] **Step 3: Add the UI card to `browse.tsx`**

Read the current full content of `web/src/pages/browse.tsx` first (it has grown across several earlier tasks in this project's history — confirm the exact current JSX structure before inserting, since this plan can't show a byte-exact diff against a file this plan's author didn't re-read at implementation time). Add a new card, near the top of the page's JSX (above or alongside the existing search/filter controls), with:

```tsx
import { importModelWithToast } from "@/lib/installer";

// ... inside the Browse component, alongside existing useState hooks:
const [hfRepoId, setHfRepoId] = useState("");

// ... inside the JSX, as a new Card:
<Card className="p-4 mb-4">
  <h3 className="text-sm font-semibold mb-2">Import from Hugging Face</h3>
  <p className="text-xs text-fg-muted mb-3">
    LAC Pro can download, convert, and install any compatible model straight from a Hugging Face repo ID.
  </p>
  <div className="flex gap-2">
    <Input
      placeholder="e.g. deepreinforce-ai/Ornith-1.0-9B"
      value={hfRepoId}
      onChange={(e) => setHfRepoId(e.target.value)}
    />
    <Button
      onClick={() => {
        if (!hfRepoId.trim()) return;
        importModelWithToast(hfRepoId.trim(), undefined, () => setHfRepoId(""));
      }}
    >
      Import
    </Button>
  </div>
</Card>
```

`Input`, `Button`, `Card` are already imported in `browse.tsx` (confirmed from the file's existing imports) — do not re-import them.

- [ ] **Step 4: Run verification**

Run: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build`
Expected: both exit 0 with no errors.

- [ ] **Step 5: Commit**

```bash
cd "C:\Users\User\repos\model-hub"
git add web/src/lib/api.ts web/src/lib/installer.ts web/src/pages/browse.tsx
git commit -m "feat(web): Pro-gated 'Import from Hugging Face' card on the Browse page

One field, one button, matching the spec's 'as easy as Autopilot' bar.
Reuses the exact toast/polling shape pullWithToast/pollProOptimizeStatus
already established -- four distinct, honest failure messages surface
through the same toast.error() path, no generic 'something went wrong'."
```

---

## Final Whole-Feature Review

After Task 9, dispatch a final whole-branch review across both repos (opus, per this project's established convention), covering: does `import_custom_model`'s pipeline order match spec §3 exactly; does every one of the four failure states in spec §4 actually clean up its scratch directory (trace it, don't just trust the per-task tests); does the Pro-gating boundary hold with no back door (an unlicensed user genuinely cannot reach any part of the pipeline, in both the CLI and API paths); does `best_fit_quant`'s reuse of `_estimate_vram`/`_fit_score` genuinely match the curated-catalog scoring for an equivalent model (not just structurally similar code); and confirm the live test from Task 1 (`test_real_ollama_can_import_a_tiny_hf_model_end_to_end`) has been run for real at least once against a running Ollama daemon before this feature is considered done, not just left "green in CI when Ollama happens to be up."
