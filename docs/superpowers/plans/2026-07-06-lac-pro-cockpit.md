# LAC Pro Cockpit (S3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface LAC's buried Pro capabilities (tune, insights, benchmark, autopilot, import) as a premium `/pro` cockpit — with model-tuning-as-craft (before→after tok/s) as the hero.

**Architecture:** `lac-pro` adds thin license-gated `/api/pro/*` routes wrapping existing pure functions; long jobs (sweep, benchmark) run in a daemon thread + write a status file the frontend polls (the autopilot/import pattern). `model-hub` adds a `/pro` page + sidebar entry + polling client methods only — it never imports `lac_pro`. **No new Pro algorithms — surface only.**

**Tech Stack:** Python 3.11, Flask, React/Vite, react-router, pytest.

## Discoveries since the spec (read first)

- `run_sweep(model, ollama_generate, ollama_show, repeat=2, num_predict=128)` returns
  `{"model","layers","results":[{"label","num_gpu","median_tps","runs":[...]}],"winner":{…}}` — **no `num_ctx`
  or VRAM** (the mockup's ctx/VRAM were illustrative). The real per-config detail is `num_gpu` (layers on GPU),
  the `runs` array (per-run tok/s), `median_tps`, and a spread computed from `runs`. Build the detail table
  from THOSE fields.
- Real Ollama callables already exist: `lac_pro.tune.http_generate` / `lac_pro.tune.http_show`. Routes pass them.
- `winner["num_gpu"] is None` = "auto split is already optimal — nothing to apply" (hide/disable Apply for it).
- `apply_config(model, num_gpu, num_ctx=None) -> "<model with : → ->-tuned"`.
- `baseline_tps` (the "before") = the model's most recent measured tok/s from `benchmark.history()` (results.jsonl),
  or `None` if never benchmarked (UI then shows the winner with no delta).

## Global Constraints

- **Open-core boundary:** `model-hub` never imports `lac_pro` (the S2 guard test enforces it). All Pro logic
  lives in `lac-pro`; the frontend only polls.
- **License-gate every route** with `lac_pro.license.check()`; unlicensed → `{"state":"not_licensed"}` (never 500),
  matching the existing `/api/pro/import-*` routes. `not_licensed` wins over any stale status file.
- **Never-raise / honest-JSON** for every route; background jobs record a `failed` state, they don't crash.
- **No new Pro algorithms** — wrap `run_sweep`/`apply_config`/`analyze`/`run_benchmark`/`history`/status reads as-is.
- **Windows-first.** Charts use the `dataviz` skill palette and must read in light + dark.
- **Tests:** `lac-pro` via `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live and not slow"` (from the lac-pro dir); `model-hub` web via `npm run typecheck && npm run build` (no web test runner). Full suites stay green; new tests RED-first.
- **Nothing pushed/published without Duan's go; `lac-pro` never gets a remote.**

---

# PART R — `lac-pro` cockpit routes (repo: `C:/Users/User/repos/lac-pro`)

All routes are added inside `ProPlugin.register_api(app)` in `lac_pro/plugin.py` (which already does
`from flask import jsonify, request` at the top — reuse those). The tune background runner + status file live in a
new module `lac_pro/cockpit.py`.

### Task R1: Tune — background sweep + status (`cockpit.py` + 2 routes)

**Files:**
- Create: `lac_pro/cockpit.py`
- Modify: `lac_pro/plugin.py` (add `POST /api/pro/tune` + `GET /api/pro/tune-status` inside `register_api`)
- Test: `lac-pro/tests/test_cockpit_tune.py`

**Interfaces:**
- Produces:
  - `cockpit.TUNE_STATUS_PATH` (`~/.model-hub/pro_tune_status.json`)
  - `cockpit.baseline_tps(model: str) -> float | None`
  - `cockpit.start_tune(model: str) -> None` — spawns a daemon thread; writes running→done/failed status.
  - `cockpit.read_tune_status(model: str) -> dict`
  - routes `POST /api/pro/tune` and `GET /api/pro/tune-status?model=`.

- [ ] **Step 1: Write the failing tests**

```python
# lac-pro/tests/test_cockpit_tune.py
import json
import flask
import pytest
import lac_pro.cockpit as cockpit
import lac_pro.plugin as plugin
import lac_pro.license as lic


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "TUNE_STATUS_PATH", tmp_path / "tune.json")


def _client():
    app = flask.Flask(__name__)
    plugin.ProPlugin().register_api(app)
    return app.test_client()


def test_baseline_from_history(monkeypatch):
    monkeypatch.setattr(cockpit, "history",
                        lambda: [{"model": "m", "tokens_per_second": 40.0},
                                 {"model": "m", "tokens_per_second": 52.0},
                                 {"model": "other", "tokens_per_second": 9.0}])
    assert cockpit.baseline_tps("m") == 52.0
    assert cockpit.baseline_tps("never") is None


def test_read_tune_status_idle_when_missing():
    assert cockpit.read_tune_status("m") == {"state": "idle"}


def test_start_tune_writes_done(monkeypatch):
    monkeypatch.setattr(cockpit, "run_sweep",
                        lambda model, g, s, **k: {"model": model, "layers": 33,
                            "results": [{"label": "auto", "num_gpu": None, "median_tps": 40.0, "runs": [40.0]},
                                        {"label": "all-33", "num_gpu": 33, "median_tps": 100.0, "runs": [100.0, 99.0]}],
                            "winner": {"label": "all-33", "num_gpu": 33, "median_tps": 100.0, "runs": [100.0, 99.0]}})
    monkeypatch.setattr(cockpit, "baseline_tps", lambda m: 40.0)
    monkeypatch.setattr(cockpit, "_spawn", lambda fn: fn())   # run inline for the test
    cockpit.start_tune("m")
    st = cockpit.read_tune_status("m")
    assert st["state"] == "done"
    assert st["winner"]["median_tps"] == 100.0
    assert st["baseline_tps"] == 40.0
    assert st["layers"] == 33


def test_start_tune_records_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(cockpit, "run_sweep", boom)
    monkeypatch.setattr(cockpit, "_spawn", lambda fn: fn())
    cockpit.start_tune("m")
    st = cockpit.read_tune_status("m")
    assert st["state"] == "failed"
    assert "ollama down" in st["message"]


def test_route_tune_unlicensed(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: None)
    r = _client().post("/api/pro/tune", json={"model": "m"})
    assert r.get_json() == {"state": "not_licensed"}


def test_route_tune_accepts(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    started = {}
    monkeypatch.setattr(cockpit, "start_tune", lambda m: started.setdefault("m", m))
    r = _client().post("/api/pro/tune", json={"model": "m"})
    assert r.get_json() == {"accepted": True}
    assert started["m"] == "m"


def test_route_tune_missing_model(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    assert _client().post("/api/pro/tune", json={}).status_code == 400


def test_route_tune_status_unlicensed_wins(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: None)
    assert _client().get("/api/pro/tune-status?model=m").get_json() == {"state": "not_licensed"}
```

- [ ] **Step 2: Run to verify they fail**

Run (from `C:/Users/User/repos/lac-pro`): `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_cockpit_tune.py -q`
Expected: FAIL — `lac_pro.cockpit` missing; routes missing.

- [ ] **Step 3: Implement `lac_pro/cockpit.py`**

```python
"""Cockpit background jobs + status files for the /pro page (Pro-side).

Long jobs (offload sweep) run in a daemon thread and write a per-model status
file the web cockpit polls — the same pattern as autopilot/import. Never raises.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from lac_pro.tune import run_sweep, http_generate, http_show

TUNE_STATUS_PATH = Path.home() / ".model-hub" / "pro_tune_status.json"


def history():
    from backend.cookbook.benchmark import history as _h
    return _h()


def baseline_tps(model: str) -> float | None:
    """Most recent measured tok/s for `model` from results.jsonl, else None."""
    try:
        rows = [r for r in history() if r.get("model") == model and r.get("tokens_per_second")]
        return float(rows[-1]["tokens_per_second"]) if rows else None
    except Exception:  # noqa: BLE001
        return None


def _read_all() -> dict:
    try:
        return json.loads(TUNE_STATUS_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt == empty
        return {}


def _write(model: str, entry: dict) -> None:
    try:
        TUNE_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = _read_all()
        entry["updated_at"] = time.time()
        data[model] = entry
        TUNE_STATUS_PATH.write_text(json.dumps(data))
    except Exception:  # noqa: BLE001 — status logging must never kill the job
        pass


def read_tune_status(model: str) -> dict:
    return _read_all().get(model, {"state": "idle"})


def _spawn(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


def start_tune(model: str) -> None:
    _write(model, {"state": "running", "started_at": time.time()})

    def _job():
        try:
            out = run_sweep(model, http_generate, http_show)
            _write(model, {"state": "done", "layers": out.get("layers"),
                           "results": out["results"], "winner": out["winner"],
                           "baseline_tps": baseline_tps(model)})
        except Exception as e:  # noqa: BLE001
            _write(model, {"state": "failed", "message": str(e)})

    _spawn(_job)
```

- [ ] **Step 4: Implement the routes** — add inside `ProPlugin.register_api`:

```python
        @app.route("/api/pro/tune", methods=["POST"])
        def _pro_tune():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            data = request.get_json(silent=True)
            model = data.get("model") if isinstance(data, dict) else None
            if not isinstance(model, str) or not model.strip():
                return jsonify({"error": "model required"}), 400
            from lac_pro import cockpit
            cockpit.start_tune(model.strip())
            return jsonify({"accepted": True}), 200

        @app.route("/api/pro/tune-status")
        def _pro_tune_status():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            model = request.args.get("model", "").strip()
            if not model:
                return jsonify({"error": "model required"}), 400
            from lac_pro import cockpit
            return jsonify(cockpit.read_tune_status(model)), 200
```

- [ ] **Step 5: Run tests + full suite + commit**

Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_cockpit_tune.py -q`
Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest -q -m "not live and not slow"`
```bash
git add lac_pro/cockpit.py lac_pro/plugin.py tests/test_cockpit_tune.py
git commit -m "feat(pro): tune sweep background job + /api/pro/tune + /api/pro/tune-status"
```

---

### Task R2: Tune — apply route

**Files:**
- Modify: `lac_pro/plugin.py` (add `POST /api/pro/tune-apply`)
- Test: `lac-pro/tests/test_cockpit_apply.py`

**Interfaces:** Consumes `lac_pro.apply.apply_config(model, num_gpu, num_ctx=None) -> str`. Produces route
`POST /api/pro/tune-apply {model, num_gpu, num_ctx?}` → `{"state":"applied","tuned_model":str}` | `failed` | `not_licensed` | `400`.

- [ ] **Step 1: Write the failing tests**

```python
# lac-pro/tests/test_cockpit_apply.py
import flask, pytest
import lac_pro.plugin as plugin
import lac_pro.license as lic
import lac_pro.apply as apply_mod


def _client():
    app = flask.Flask(__name__)
    plugin.ProPlugin().register_api(app)
    return app.test_client()


def test_apply_unlicensed(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: None)
    assert _client().post("/api/pro/tune-apply", json={"model": "m", "num_gpu": 33}).get_json() == {"state": "not_licensed"}


def test_apply_happy(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    monkeypatch.setattr(apply_mod, "apply_config", lambda model, num_gpu, num_ctx=None: "m-tuned")
    body = _client().post("/api/pro/tune-apply", json={"model": "m", "num_gpu": 33}).get_json()
    assert body == {"state": "applied", "tuned_model": "m-tuned"}


def test_apply_missing_args(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    assert _client().post("/api/pro/tune-apply", json={"model": "m"}).status_code == 400


def test_apply_failure(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    def boom(*a, **k):
        raise RuntimeError("create failed")
    monkeypatch.setattr(apply_mod, "apply_config", boom)
    body = _client().post("/api/pro/tune-apply", json={"model": "m", "num_gpu": 33}).get_json()
    assert body["state"] == "failed"
    assert "create failed" in body["message"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `C:/Users/User/repos/model-hub/.venv/Scripts/python.exe -m pytest tests/test_cockpit_apply.py -q` → FAIL (route missing).

- [ ] **Step 3: Implement** — add inside `register_api`:

```python
        @app.route("/api/pro/tune-apply", methods=["POST"])
        def _pro_tune_apply():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            data = request.get_json(silent=True) or {}
            model = data.get("model") if isinstance(data, dict) else None
            num_gpu = data.get("num_gpu") if isinstance(data, dict) else None
            if not isinstance(model, str) or not model.strip() or not isinstance(num_gpu, int):
                return jsonify({"error": "model and integer num_gpu required"}), 400
            num_ctx = data.get("num_ctx") if isinstance(data.get("num_ctx"), int) else None
            from lac_pro.apply import apply_config
            try:
                name = apply_config(model.strip(), num_gpu, num_ctx)
            except Exception as e:  # noqa: BLE001
                return jsonify({"state": "failed", "message": str(e)}), 200
            return jsonify({"state": "applied", "tuned_model": name}), 200
```

- [ ] **Step 4: Run tests + full suite + commit**

Run: `...pytest tests/test_cockpit_apply.py -q` then `...pytest -q -m "not live and not slow"`.
```bash
git add lac_pro/plugin.py tests/test_cockpit_apply.py
git commit -m "feat(pro): POST /api/pro/tune-apply (bake the winning offload config)"
```

---

### Task R3: Insights route

**Files:** Modify `lac_pro/plugin.py` (add `GET /api/pro/insights`); Test `lac-pro/tests/test_cockpit_insights.py`.

**Interfaces:** Consumes `lac_pro.insights.analyze(rows, threshold=…)` + `backend.cookbook.benchmark.history()`.
Produces `GET /api/pro/insights?threshold=` → `{"state":"ok","rows":[…analyze output…]}` | `not_licensed`.

- [ ] **Step 1: Failing tests**

```python
# lac-pro/tests/test_cockpit_insights.py
import flask, pytest
import lac_pro.plugin as plugin
import lac_pro.license as lic
import lac_pro.insights as insights_mod


def _client():
    app = flask.Flask(__name__); plugin.ProPlugin().register_api(app); return app.test_client()


def test_insights_unlicensed(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: None)
    assert _client().get("/api/pro/insights").get_json() == {"state": "not_licensed"}


def test_insights_ok(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    monkeypatch.setattr(insights_mod, "analyze",
                        lambda rows, threshold=0.15: [{"model": "m", "runs": 6, "baseline_tps": 100.0,
                                                       "recent_tps": 92.0, "delta_pct": -8.0, "regression": False}])
    import lac_pro.plugin as pl
    monkeypatch.setattr(pl, "_cockpit_history", lambda: [{"model": "m"}], raising=False)
    body = _client().get("/api/pro/insights?threshold=0.2").get_json()
    assert body["state"] == "ok"
    assert body["rows"][0]["model"] == "m"


def test_insights_bad_threshold_defaults(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    monkeypatch.setattr(insights_mod, "analyze", lambda rows, threshold=0.15: [{"t": threshold}])
    body = _client().get("/api/pro/insights?threshold=notanumber").get_json()
    assert body["rows"][0]["t"] == 0.15
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — add inside `register_api` (import history + analyze locally):

```python
        @app.route("/api/pro/insights")
        def _pro_insights():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            try:
                threshold = float(request.args.get("threshold", "0.15"))
            except (TypeError, ValueError):
                threshold = 0.15
            from lac_pro.insights import analyze
            from backend.cookbook.benchmark import history
            rows = analyze(history(), threshold=threshold)
            return jsonify({"state": "ok", "rows": rows}), 200
```

(Remove the `_cockpit_history` monkeypatch line from the test — history is imported directly; instead monkeypatch
`backend.cookbook.benchmark.history`. Adjust the test's import to `import backend.cookbook.benchmark as bench` and
`monkeypatch.setattr(bench, "history", lambda: [{"model": "m"}])`.)

- [ ] **Step 4: Run tests + full suite + commit.**
```bash
git add lac_pro/plugin.py tests/test_cockpit_insights.py
git commit -m "feat(pro): GET /api/pro/insights (measured tok/s history + regressions)"
```

---

### Task R4: Benchmark routes

**Files:** Modify `lac_pro/plugin.py` (add `POST /api/pro/benchmark` + `GET /api/pro/benchmark-history`);
Test `lac-pro/tests/test_cockpit_benchmark.py`.

**Interfaces:** Consumes `lac_pro.autopilot.run_benchmark(model)` + `backend.cookbook.benchmark.history()`.
Produces `POST /api/pro/benchmark {model}` → `{"accepted":true}` (daemon thread) | `not_licensed` | `400`;
`GET /api/pro/benchmark-history?model=` → `{"state":"ok","runs":[{tokens_per_second,time_to_first_token_ms,timestamp}]}` (newest first, ≤20) | `not_licensed`.

- [ ] **Step 1: Failing tests**

```python
# lac-pro/tests/test_cockpit_benchmark.py
import flask, pytest
import lac_pro.plugin as plugin
import lac_pro.license as lic
import lac_pro.autopilot as ap
import backend.cookbook.benchmark as bench


def _client():
    app = flask.Flask(__name__); plugin.ProPlugin().register_api(app); return app.test_client()


def test_benchmark_unlicensed(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: None)
    assert _client().post("/api/pro/benchmark", json={"model": "m"}).get_json() == {"state": "not_licensed"}


def test_benchmark_accepts_and_threads(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    ran = {}
    monkeypatch.setattr(ap, "run_benchmark", lambda m: ran.setdefault("m", m))
    import lac_pro.plugin as pl
    monkeypatch.setattr(pl.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda s: target()})())
    r = _client().post("/api/pro/benchmark", json={"model": "m"})
    assert r.get_json() == {"accepted": True}
    assert ran["m"] == "m"


def test_benchmark_missing_model(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    assert _client().post("/api/pro/benchmark", json={}).status_code == 400


def test_benchmark_history(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    monkeypatch.setattr(bench, "history",
                        lambda: [{"model": "m", "tokens_per_second": 40.0, "time_to_first_token_ms": 120, "timestamp": 1},
                                 {"model": "x", "tokens_per_second": 9.0, "timestamp": 2},
                                 {"model": "m", "tokens_per_second": 52.0, "time_to_first_token_ms": 90, "timestamp": 3}])
    runs = _client().get("/api/pro/benchmark-history?model=m").get_json()["runs"]
    assert [r["tokens_per_second"] for r in runs] == [52.0, 40.0]   # newest first, only model m
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — ensure `import threading` at the top of `plugin.py`; add inside `register_api`:

```python
        @app.route("/api/pro/benchmark", methods=["POST"])
        def _pro_benchmark():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            data = request.get_json(silent=True)
            model = data.get("model") if isinstance(data, dict) else None
            if not isinstance(model, str) or not model.strip():
                return jsonify({"error": "model required"}), 400
            from lac_pro.autopilot import run_benchmark
            threading.Thread(target=lambda: run_benchmark(model.strip()), daemon=True).start()
            return jsonify({"accepted": True}), 200

        @app.route("/api/pro/benchmark-history")
        def _pro_benchmark_history():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            model = request.args.get("model", "").strip()
            from backend.cookbook.benchmark import history
            runs = [r for r in history() if r.get("model") == model]
            runs = list(reversed(runs))[:20]
            return jsonify({"state": "ok", "runs": runs}), 200
```

(In `test_benchmark_accepts_and_threads`, monkeypatch `pl.threading.Thread` as shown so the target runs inline.)

- [ ] **Step 4: Run tests + full suite + commit.**
```bash
git add lac_pro/plugin.py tests/test_cockpit_benchmark.py
git commit -m "feat(pro): POST /api/pro/benchmark + GET /api/pro/benchmark-history"
```

---

### Task R5: Autopilot-log + import-history routes

**Files:** Modify `lac_pro/plugin.py` (add `GET /api/pro/autopilot-log` + `GET /api/pro/import-history`);
Test `lac-pro/tests/test_cockpit_logs.py`.

**Interfaces:** Reads `lac_pro.autopilot._read_status()` (all models) and `lac_pro.hf_import.IMPORT_STATUS_PATH`
(all repos). Produces `{"state":"ok","entries":[…]}` | `not_licensed`.

- [ ] **Step 1: Failing tests**

```python
# lac-pro/tests/test_cockpit_logs.py
import json, flask, pytest
import lac_pro.plugin as plugin
import lac_pro.license as lic
import lac_pro.autopilot as ap
import lac_pro.hf_import as hf


def _client():
    app = flask.Flask(__name__); plugin.ProPlugin().register_api(app); return app.test_client()


def test_autopilot_log_unlicensed(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: None)
    assert _client().get("/api/pro/autopilot-log").get_json() == {"state": "not_licensed"}


def test_autopilot_log_entries(monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    monkeypatch.setattr(ap, "_read_status",
                        lambda: {"m1": {"state": "done", "tokens_per_second": 100.0, "updated_at": 2},
                                 "m2": {"state": "failed_silent", "updated_at": 1}})
    entries = _client().get("/api/pro/autopilot-log").get_json()["entries"]
    assert {e["model"] for e in entries} == {"m1", "m2"}
    assert any(e["model"] == "m1" and e["tokens_per_second"] == 100.0 for e in entries)


def test_import_history_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    p = tmp_path / "imp.json"
    p.write_text(json.dumps({"org/x": {"state": "done", "model_name": "x", "quant": "q4_K_M", "updated_at": 3}}))
    monkeypatch.setattr(hf, "IMPORT_STATUS_PATH", p)
    entries = _client().get("/api/pro/import-history").get_json()["entries"]
    assert entries[0]["repo_id"] == "org/x"
    assert entries[0]["model_name"] == "x"


def test_import_history_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "check", lambda: object())
    monkeypatch.setattr(hf, "IMPORT_STATUS_PATH", tmp_path / "nope.json")
    assert _client().get("/api/pro/import-history").get_json() == {"state": "ok", "entries": []}
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** — add inside `register_api`:

```python
        @app.route("/api/pro/autopilot-log")
        def _pro_autopilot_log():
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            from lac_pro.autopilot import _read_status
            data = _read_status() or {}
            entries = [{"model": m, **v} for m, v in data.items()]
            entries.sort(key=lambda e: e.get("updated_at", 0), reverse=True)
            return jsonify({"state": "ok", "entries": entries}), 200

        @app.route("/api/pro/import-history")
        def _pro_import_history():
            import json as _json
            from lac_pro.license import check
            if check() is None:
                return jsonify({"state": "not_licensed"}), 200
            from lac_pro.hf_import import IMPORT_STATUS_PATH
            try:
                data = _json.loads(IMPORT_STATUS_PATH.read_text())
            except Exception:  # noqa: BLE001
                data = {}
            entries = [{"repo_id": rid, **v} for rid, v in data.items()]
            entries.sort(key=lambda e: e.get("updated_at", 0), reverse=True)
            return jsonify({"state": "ok", "entries": entries}), 200
```

- [ ] **Step 4: Run tests + full suite + commit.**
```bash
git add lac_pro/plugin.py tests/test_cockpit_logs.py
git commit -m "feat(pro): GET /api/pro/autopilot-log + /api/pro/import-history"
```

---

# PART U — `model-hub` frontend (`C:/Users/User/repos/model-hub/web`)

No web test runner is configured; every U-task verifies with `npm run typecheck && npm run build` (both exit 0)
and is behaviorally covered by the final manual smoke. Match the existing component style (`Card`/`Button`/`Input`
in `web/src/components/ui/`; `useAsync`/`useInterval` in `web/src/lib/hooks`; the polling pattern in
`web/src/lib/installer.ts`; the badge/table idiom in `web/src/pages/scan.tsx`).

### Task U1: API client methods

**Files:** Modify `web/src/lib/api.ts`.

**Interfaces produced (add to the `api` object):**
- `proTune(model): Promise<{accepted?:boolean; state?:"not_licensed"}>`
- `proTuneStatus(model): Promise<any>` (states: `idle|running|done|failed|not_licensed`)
- `proTuneApply(model, num_gpu, num_ctx?): Promise<{state:string; tuned_model?:string; message?:string}>`
- `proInsights(threshold?): Promise<{state:string; rows?:any[]}>`
- `proBenchmark(model): Promise<{accepted?:boolean; state?:string}>`
- `proBenchmarkHistory(model): Promise<{state:string; runs?:any[]}>`
- `proAutopilotLog(): Promise<{state:string; entries?:any[]}>`
- `proImportHistory(): Promise<{state:string; entries?:any[]}>`

- [ ] **Step 1: Implement** (match the existing fetch-wrapper style; GET via `fetch(url).then(r=>r.json())`,
POST with the JSON body + Content-Type header as `activatePro` does):

```ts
proTune: (model: string) =>
  fetch("/api/pro/tune", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) }).then((r) => r.json()),
proTuneStatus: (model: string) => fetch(`/api/pro/tune-status?model=${encodeURIComponent(model)}`).then((r) => r.json()),
proTuneApply: (model: string, num_gpu: number, num_ctx?: number) =>
  fetch("/api/pro/tune-apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model, num_gpu, num_ctx }) }).then((r) => r.json()),
proInsights: (threshold?: number) => fetch(`/api/pro/insights${threshold != null ? `?threshold=${threshold}` : ""}`).then((r) => r.json()),
proBenchmark: (model: string) =>
  fetch("/api/pro/benchmark", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) }).then((r) => r.json()),
proBenchmarkHistory: (model: string) => fetch(`/api/pro/benchmark-history?model=${encodeURIComponent(model)}`).then((r) => r.json()),
proAutopilotLog: () => fetch("/api/pro/autopilot-log").then((r) => r.json()),
proImportHistory: () => fetch("/api/pro/import-history").then((r) => r.json()),
```

- [ ] **Step 2:** `npm run typecheck` → exit 0. **Commit** `feat(web): api client for the Pro cockpit routes`.

---

### Task U2: `/pro` page shell + nav + Pro-status header + unlicensed teaser

**Files:** Create `web/src/pages/pro.tsx`; Modify `web/src/components/sidebar.tsx` (add a "Pro" NAV entry),
`web/src/App.tsx` (add `<Route path="/pro" element={<Pro />} />`).

**Interfaces produced:** `Pro` page component; renders a status header (from `api.proStatus()`), and — when
`licensed` is false — a **locked teaser** (what the cockpit does + an Activate CTA `<Link to="/settings">`).
When licensed, it renders placeholder slots for the five panels (filled by U3–U5): a full-width hero slot and a
2×2 grid of four panel slots (layout A).

- [ ] **Step 1: Build the shell.** `sidebar.tsx`: import `Sparkles` from `lucide-react`; add
`{ to: "/pro", label: "Pro", icon: Sparkles, end: false }` to the `NAV` array (after Downloads). `App.tsx`:
`import { Pro } from "./pages/pro"` and add `<Route path="/pro" element={<Pro />} />`.

`pro.tsx` structure (fill panel slots in later tasks; here the shell + status + teaser):
```tsx
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { PageHeader } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";

export function Pro() {
  const status = useAsync(() => api.proStatus());
  const licensed = status.data?.licensed;

  if (status.loading) return <PageHeader title="Pro" subtitle="Loading…" />;

  if (!licensed) {
    return (
      <>
        <PageHeader title="LAC Pro" subtitle="The tuning cockpit — model tuning, insights, benchmarking, autopilot, and custom imports." />
        <Card className="max-w-2xl p-6">
          <div className="flex items-center gap-2 text-sm font-semibold"><Sparkles className="h-4 w-4 text-verdant" /> Unlock the Pro cockpit</div>
          <p className="mt-2 text-[13px] text-fg-muted">Tune any model to your exact hardware with before→after proof, track measured speed over time, and import any Hugging Face model. Activate Pro to turn it on.</p>
          <Button className="mt-4" asChild><Link to="/settings">Activate Pro</Link></Button>
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader title="LAC Pro"
        subtitle={`Active · ${status.data?.plan ?? "pro"} · ${status.data?.expires_human ?? ""}`} />
      <div className="grid gap-5">
        {/* U3 */}  <TuneHero />
        <div className="grid gap-5 lg:grid-cols-2">
          {/* U4 */} <InsightsPanel /> <AutopilotPanel />
          {/* U5 */} <BenchmarkPanel /> <ImportPanel />
        </div>
      </div>
    </>
  );
}
```
For this task, define `TuneHero`/`InsightsPanel`/`AutopilotPanel`/`BenchmarkPanel`/`ImportPanel` as trivial
placeholder components in `pro.tsx` (`return <Card className="p-5 text-[13px] text-fg-muted">…coming in the next task…</Card>`)
so the shell typechecks and builds; later tasks replace each.

- [ ] **Step 2:** `npm run typecheck && npm run build` → exit 0. Manually confirm `/pro` renders (shell) and the
sidebar shows Pro. **Commit** `feat(web): /pro cockpit shell + nav + Pro-status header + unlicensed teaser`.

---

### Task U3: Tune hero panel (the centerpiece)

**Files:** Create `web/src/components/pro/tune-hero.tsx`; Modify `web/src/pages/pro.tsx` (use it).

**Consumes:** `api.proTune`, `api.proTuneStatus` (poll every 2s while `running`), `api.proTuneApply`;
`api.installed()` (or the existing installed-models source — check `installed.tsx`/`api.ts`) for the model list.

**Behavior (implements the approved design):**
1. A model `<Select>` of installed models + a **Run sweep** button → `api.proTune(model)`; then poll
   `api.proTuneStatus(model)` every 2s via `useInterval` until `state !== "running"`.
2. `running` → a progress state ("Benchmarking configs on your hardware…").
3. `done` → render:
   - **before→after**: `baseline_tps → winner.median_tps` with `+N%` (compute `((winner-baseline)/baseline)*100`;
     if `baseline_tps == null`, show just the winner tok/s, no delta). Winner label + "full GPU offload" when
     `num_gpu === layers`.
   - a **config table**: one row per `results[]` entry — plain-English label (`auto` → "auto (Ollama decides)";
     `num_gpu === layers` → "all-N layers · full GPU offload"; else "N layers · partial offload"), a bar
     (`median_tps / max(median_tps)` width; winner uses the verdant accent, others muted per the `dataviz`
     palette), `median_tps` (monospace), and a per-row **Apply** button. The **winner row is expanded** showing
     `num_gpu`, the `runs` array (per-run tok/s), and spread `= (max(runs)-min(runs))/median*100`%. Other rows
     have a ▸ toggle that expands the same detail. **Hide Apply when `num_gpu === null`** (auto — nothing to apply)
     and instead show "Ollama's automatic split is already optimal".
   - **Apply** → `api.proTuneApply(model, row.num_gpu, row.num_ctx)` → on `applied`, toast/inline
     "Created `<tuned_model>`"; on `failed`, inline message.
4. `failed` → inline error (`message`) + a Retry (re-run sweep).

Build the bars/typography against the `dataviz` skill palette (load it before writing chart colors). Keep the
component data-driven off the `tune-status` `done` shape defined in Task R1.

- [ ] **Step 1:** Implement `tune-hero.tsx` per the above, wire it into `pro.tsx`.
- [ ] **Step 2:** `npm run typecheck && npm run build` → exit 0. **Commit** `feat(web): Pro tune hero (sweep → before→after → apply)`.

---

### Task U4: Insights + Autopilot panels

**Files:** Create `web/src/components/pro/insights-panel.tsx`, `web/src/components/pro/autopilot-panel.tsx`;
Modify `web/src/pages/pro.tsx`.

**Insights panel** — `api.proInsights()` on mount. Render a table of `rows[]`:
`model · baseline_tps · recent_tps · Δ% · [regression badge]`. Δ% colored (negative = warning, positive =
success per the `dataviz`/existing badge palette); `regression:true` rows get a red "regression" badge. Empty
`rows` → "Benchmark a few models to build speed history." `state === "not_licensed"` can't happen here (page is
gated) but handle defensively → the teaser is already shown by the page.

**Autopilot panel** — `api.proAutopilotLog()` on mount. Render `entries[]`:
`model · state · tokens_per_second? · relative time from updated_at`. Map states to friendly labels
(`done` → "optimized", `running` → "optimizing…", `failed_silent` → "skipped", `idle` → "—"). Empty → "Autopilot
runs automatically after each model install."

- [ ] **Step 1:** Implement both panels (they are read-only tables; mirror the `scan.tsx` table/badge idiom),
wire into `pro.tsx`.
- [ ] **Step 2:** `npm run typecheck && npm run build` → exit 0. **Commit** `feat(web): Pro insights + autopilot panels`.

---

### Task U5: Benchmark + Import panels

**Files:** Create `web/src/components/pro/benchmark-panel.tsx`, `web/src/components/pro/import-panel.tsx`;
Modify `web/src/pages/pro.tsx`.

**Benchmark panel** — a model `<Select>` (installed models) + **Benchmark now** → `api.proBenchmark(model)`;
then re-fetch `api.proBenchmarkHistory(model)` (poll a few times / on a short interval) and render the latest
`tokens_per_second` + `time_to_first_token_ms` prominently, plus a short recent-runs list. Empty history → "Run a
benchmark to measure this model's speed on your hardware."

**Import panel** — a repo-id `<Input>` + a **quant** `<Select>` (options: `auto` (default, sends no quant),
`q4_K_M`, `q8_0`, `F16` — matching the backend's accepted createable quants) + **Import** button →
`api.importModel(repoId, quant)` (the existing method used by Browse's `importModelWithToast`; if it doesn't take
a quant arg yet, extend it to pass `quant` in the POST body — the backend route already accepts it) → poll
`api.importStatus(repoId)` (existing) through the state machine and show progress. Below, an **import history**
list from `api.proImportHistory()` (`repo_id · state · model_name?/quant? · error message? · relative time`).

(Reuse the existing import status types + polling shape from `web/src/lib/installer.ts`; do not remove Browse's
import card.)

- [ ] **Step 1:** Implement both panels, wire into `pro.tsx`.
- [ ] **Step 2:** `npm run typecheck && npm run build` → exit 0. **Commit** `feat(web): Pro benchmark + elevated import panels`.

---

## Final: manual E2E smoke (controller/Duan-gated; record in `.superpowers/sdd/progress.md`)

On the packaged exe with Pro licensed + Ollama up + a small real model:
1. [ ] Open `/pro` — status header shows active; all five panels render.
2. [ ] Tune: select a model → Run sweep → `running` → `done` with real before→after + a detail-rich config table;
   Apply the winner → a `<model>-tuned` variant is created (visible in Installed / `ollama list`).
3. [ ] Benchmark now → latest tok/s + TTFT appear; Insights + Autopilot + Import-history populate with real data.
4. [ ] Import: paste a small HF repo id + pick a quant → progresses to done; appears in import history.
5. [ ] On an unlicensed build, `/pro` shows the locked teaser + Activate CTA, and every `/api/pro/*` cockpit route
   returns `{"state":"not_licensed"}`.
6. [ ] Charts read correctly in both light and dark.

---

## Self-review (completed by plan author)

**Spec coverage:** §4 Panel 1 → R1+R2+U3; Panel 2 → R3+U4; Panel 3 → R4+U5; Panel 4 → R5+U4; Panel 5 → R5(history)+U5;
page shell/nav/teaser → U2; charts → U3/U4 (dataviz); status header → U2. §6 error handling → honest states in every
route + `failed`/empty UI states. §7 testing → R-task unit tests + U typecheck/build + final smoke. Boundary → all
routes in `lac-pro`; U-tasks add no `lac_pro` import (S2 guard holds).

**Placeholder scan:** no TBD/TODO. The R3 test note (monkeypatch `backend.cookbook.benchmark.history` rather than a
`_cockpit_history` stub) is an explicit correction, not a placeholder. Frontend panels (U3–U5) are specified against
the exact JSON contracts from the R-tasks with concrete field names + behavior; the implementer builds the JSX from
the app's existing `ui/` primitives (named), not from a vague sketch.

**Type consistency:** `tune-status` `done` shape (`layers/results[{label,num_gpu,median_tps,runs}]/winner/baseline_tps`)
is identical R1 ↔ U3; `tune-apply` (`{state,tuned_model}`) R2 ↔ U3; `insights` (`rows[{model,runs,baseline_tps,recent_tps,delta_pct,regression}]`)
R3 ↔ U4; `benchmark-history` (`runs[{tokens_per_second,time_to_first_token_ms,timestamp}]`) R4 ↔ U5;
`autopilot-log`/`import-history` (`entries[]`) R5 ↔ U4/U5; api client names (U1) match every consumer.

**Known assumptions to verify during execution:** the exact installed-models source the panels use (`api.installed()`
vs the source `installed.tsx` uses — check first); whether `api.importModel` already forwards `quant` (extend if not);
the `dataviz` palette tokens for winner-vs-muted bars (load the skill before writing chart colors).
