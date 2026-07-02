# Plan: Web Technical Controls (calibration + GPU tuning + benchmark launcher)

**Branch:** `feat/web-technical-controls` (from `master` @ 31ac1cd)
**Date:** 2026-07-02
**Goal:** Bring the web UI to feature-parity with the CLI's technical surface — let the browser reflect calibration, expose GPU-offload tuning, and launch benchmarks that feed the calibration loop. End state: a user can open the web app, benchmark a model, and watch the recommendations shift to reflect real measured performance — all without dropping to the terminal.

## Current state (verified)

- Calibration shipped to **CLI only** (`cli.py` `cmd_recommend` wires `load_calibration`). `/api/recommend` (`backend/api.py:136`) calls `recommend()` with **no calibration** and the response object omits `speed_source`/`speed_band_pct`.
- `_serialize_split_plan` exists and the `SplitPlan` is serialized into every rec response, but the React `Scan` table (`web/src/pages/scan.tsx`) never renders it.
- Benchmark logic (`_benchmark_metrics`, `_benchmark_log`, repeat loop, fingerprint stamping) lives in `cli.py` — not importable by the API. No `/api/benchmark` endpoint exists.
- Frontend stack: React 18 + TS + Vite + Tailwind + shadcn/ui (Radix Switch/Select/Dialog/Progress + lucide + sonner). `sse()` async-generator client + `useAsync` hook already exist. `web/dist` is pre-built; `npm run build` rebuilds it; `npm run typecheck` validates types.
- The `Scan` page already has a controls Card with a use-case `Select` and a native VRAM range slider — the established pattern for new controls.

## Architecture decisions

1. **Shared benchmark module.** Extract `_benchmark_metrics`, `_benchmark_log`, and the repeat/median/fingerprint logic from `cli.py` into `backend/cookbook/benchmark.py`. Both `cli.py` and `api.py` import it. This avoids duplicating ~60 lines of tested logic in the API and keeps the CLI's behavior identical.
2. **Calibration built per-request in `/api/recommend`** — mirror the CLI wiring exactly (`detect_stack(info=info)` + `load_calibration(info, stack, results_path)`). Cheap (results.jsonl is small, `load_models` is `lru_cache`d).
3. **GPU controls = `info` pre-processing.** `recommend()` takes `info` and computes the split internally; we can't change its signature. Instead, `/api/recommend` accepts optional `gpu_mask` (which device indices to use) and `allow_spill` (bool) params, applies them to `info` (drop disabled GPUs / zero RAM tier) before calling `recommend()`. Surface-level, no scoring-engine change.
4. **Benchmark endpoint = SSE stream.** Reuse the `ollama_pull`/`ollama_chat` streaming pattern: POST `/api/benchmark` streams `{run, tps, ...}` per iteration + a final `{median, runs, done}`. Frontend consumes via the existing `sse()` client.
5. **Frontend rec table gains a source Badge** (success=measured, info=calibrated, neutral=estimated) and an expandable split-plan row — additive, no table restructuring.

## Tasks (8, TDD where applicable)

### Backend

**Task 1 — Extract `backend/cookbook/benchmark.py` (shared module).**
Move `build_metrics()` (was `_benchmark_metrics`), `log_result()` (was `_benchmark_log`), and a new `run_repeat(model, repeat, ollama_fn, ...)` helper from `cli.py` into the new module. Refactor `cli.py` `cmd_benchmark` to import from it. Keep CLI behavior + output byte-identical. Move/adapt `tests/test_benchmark.py` to cover the shared module.
*Verify:* existing benchmark tests pass; CLI `apt benchmark --help` and a dry-run shape unchanged; full suite green.

**Task 2 — Wire calibration into `/api/recommend`.**
In `api_recommend()`: build `_cal = load_calibration(info, detect_stack(info=info), <results.jsonl path>)`; pass `calibration=_cal` into `recommend()`. Add `speed_source` + `speed_band_pct` to the serialized rec object. Honor an optional `?no_calibration=1` escape hatch (mirrors CLI `--no-calibration`).
*Verify:* new test that the endpoint returns `speed_source` values; recs shift when real benchmarks are present.

**Task 3 — Add `POST /api/benchmark` (SSE).**
New endpoint streams a benchmark run via the shared module: params `{model, prompt?, num_predict?, temperature?, repeat?}`. Each iteration yields `{run: i, tps, eval_count, ttft_ms, response_len}`; final yield `{done: true, median_tps, runs: [...]}`. Stamps fingerprint+stack, logs each run to results.jsonl (so calibration picks it up on the next `/api/recommend`).
*Verify:* test with a mocked Ollama response asserting the SSE frames + that a results.jsonl row is written.

**Task 4 — GPU-selection params on `/api/recommend`.**
Accept `?gpu_mask=0,1` and `?allow_spill=0|1`. Apply to `info` before `recommend()`: drop GPUs whose `device_index` isn't in the mask; if `allow_spill=0`, zero the RAM tier so spilled recs can't surface. Return the effective `info` in the response so the UI can label it.
*Verify:* tests that masking a GPU changes the rec set; spill toggle excludes multi-GPU/CPU-offload recs.

### Frontend (each task ends with `npm run typecheck && npm run build`)

**Task 5 — Types + calibration source tags in the rec table.**
Add `speed_source: "measured"|"calibrated"|"estimated"` and `speed_band_pct: number` to `Recommendation` in `lib/types.ts`. In `scan.tsx`, render a source Badge (success/info/neutral) next to each rec's quant badge + a `±N%` band tooltip. Use the existing `Badge` `dot` prop for a leading indicator.
*Verify:* typecheck clean; built dist shows tags; recs reflect calibrated ranking.

**Task 6 — Split-plan display.**
In `scan.tsx`, add an expandable row (or a secondary line under each rec) showing `run_mode` + the `split_plan.summary` + per-tier layer allocation when `run_mode !== "gpu"`. Use lucide `ChevronDown`/`Layers` icons. Collapsed by default; click to expand.
*Verify:* typecheck clean; multi-GPU/offload recs show their split; GPU-only recs show nothing extra.

**Task 7 — GPU-offload controls.**
In the Scan controls Card, add a `Switch` per detected GPU (from `api.scan()` `gpus[]`) labeled with name+VRAM, plus a "Allow RAM spill" `Switch`. Wire both to the `api.recommend()` call via new `gpu_mask` + `allow_spill` params. Re-fetch on toggle (existing `useAsync` deps pattern).
*Verify:* typecheck clean; toggling a GPU off changes the recs; disabling spill hides multi-GPU/offload recs.

**Task 8 — Benchmark launcher.**
New `BenchmarkDialog` component (Radix Dialog) triggered by a "Benchmark" button on the Scan page (and/or Installed page). Fields: model (select from installed), repeat (number/select 1–5), prompt override (optional). On submit, call `api.benchmark(model, {repeat}, signal)` via `sse()`, show a `Progress` bar + per-run tok/s, then the median. On done: sonner toast + trigger a recs refetch (so calibration bites immediately).
*Verify:* typecheck clean; dialog launches; against a running Ollama it streams + completes; recs refresh after.

## Sequencing & dependencies

```
T1 (extract module) ─┬─► T3 (benchmark API)  ──► T8 (benchmark UI)
                     └─► (cli still works)
T2 (calibration API) ─────────────────────────► T5 (source-tag UI)
T4 (GPU params API)  ─────────────────────────► T7 (GPU controls UI)
                                                T6 (split-plan UI) — no backend dep
```
Optimal order: **T2 → T1 → T3 → T4 → T5 → T6 → T7 → T8.** T2 first (smallest, unblocks the most-visible win); T6 can land anytime after T5.

## Verification gates (whole branch)

- `pytest -q` green (Python, every backend task).
- `npm run typecheck` clean + `npm run build` succeeds (every frontend task).
- End-to-end smoke on the live server: benchmark a small model via the dialog → confirm recs shift from `estimated` → `measured`/`calibrated` in the table.
- CLI regression: `apt recommend` and `apt benchmark` unchanged after T1.

## Out of scope (explicit)

- Quant-override (letting the user force a specific quant per model) — recommend picks the best; surfacing alternates is a separate UX.
- Benchmark scheduling / auto-calibration daemons.
- Redesigning the rec table layout — additive only.
