import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import {
  ApiError,
  api,
  getProTuneApplyCandidate,
  isProTuneRunningConflict,
} from "../src/lib/api.ts";

const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");

const tuneHeroSource = readFileSync(
  new URL("../src/components/pro/tune-hero.tsx", import.meta.url),
  "utf8"
);

function doneStatus(overrides: Record<string, unknown> = {}) {
  return {
    state: "done",
    sweep_id: "sweep-1",
    completed_at: 100,
    expires_at: 200,
    apply_state: "available",
    layers: 33,
    results: [
      { label: "auto", num_gpu: null, median_tps: 50, runs: [50] },
      { label: "gpu", num_gpu: 33, median_tps: 60, runs: [60] },
    ],
    winner: { label: "gpu", num_gpu: 33, median_tps: 60, runs: [60] },
    baseline_tps: 50,
    apply_decision: {
      allowed: true,
      reason: "verified",
      num_gpu: 33,
      auto_tps: 50,
      reference_tps: 50,
      candidate_tps: 60,
      improvement_ratio: 0.2,
    },
    ...overrides,
  };
}

test("proTuneApply sends only the model and backend-issued sweep identity", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl: string | URL | Request | undefined;
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = (async (url, init) => {
    capturedUrl = url;
    capturedInit = init;
    return new Response(JSON.stringify({ state: "applied", tuned_model: "model-tuned" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const result = await api.proTuneApply("model:latest", "sweep/verified-1");

    assert.deepEqual(result, { state: "applied", tuned_model: "model-tuned" });
    assert.equal(capturedUrl, "/api/pro/tune-apply");
    assert.equal(capturedInit?.method, "POST");
    assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
      model: "model:latest",
      sweep_id: "sweep/verified-1",
    });
    assert.doesNotMatch(String(capturedInit?.body), /num_gpu|num_ctx/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("TuneHero applies only the backend-authorized candidate by sweep identity", () => {
  assert.match(tuneHeroSource, /status\.apply_decision\.allowed/);
  assert.match(tuneHeroSource, /r\.num_gpu === status\.apply_decision\.num_gpu/);
  assert.match(tuneHeroSource, /r\.median_tps === status\.apply_decision\.candidate_tps/);
  assert.match(tuneHeroSource, /api\.proTuneApply\(model, status\.sweep_id\)/);
  assert.doesNotMatch(tuneHeroSource, /api\.proTuneApply\([^\n]*num_gpu/);
  assert.doesNotMatch(tuneHeroSource, /api\.proTuneApply\([^\n]*num_ctx/);
});

test("TuneHero explains that blocked or no-gain sweeps keep Ollama automatic", () => {
  assert.match(tuneHeroSource, /!status\.apply_decision\.allowed/);
  assert.match(tuneHeroSource, /Ollama automatic remains selected\./);
  assert.match(tuneHeroSource, /status\.apply_decision\.reason/);
});

test("TuneHero does not claim that a layer count proves full GPU residency", () => {
  assert.doesNotMatch(tuneHeroSource, /full GPU offload/);
  assert.match(tuneHeroSource, /explicit offload/);
});

test("done tune status carries numeric expiry and exact apply lifecycle metadata", () => {
  assert.match(apiSource, /completed_at: number/);
  assert.match(apiSource, /expires_at: number/);
  assert.match(
    apiSource,
    /"available"\s*\|\s*"unavailable"\s*\|\s*"applying"\s*\|\s*"applied"\s*\|\s*"failed"\s*\|\s*"expired"\s*\|\s*"stale"/
  );
  assert.match(apiSource, /applied_sweep_id\?: string/);
  assert.match(apiSource, /tuned_model\?: string/);
});

test("only an available, live, unconsumed exact candidate can be applied", async () => {
  assert.deepEqual(getProTuneApplyCandidate(doneStatus(), 150), doneStatus().results[1]);
  assert.equal(getProTuneApplyCandidate(doneStatus({ expires_at: 150 }), 150), undefined);
  assert.equal(getProTuneApplyCandidate(doneStatus(), Number.NaN), undefined);
  assert.equal(getProTuneApplyCandidate(doneStatus({ applied_sweep_id: "sweep-1" }), 150), undefined);
  assert.equal(
    getProTuneApplyCandidate(
      doneStatus({ apply_decision: { ...doneStatus().apply_decision, allowed: false } }),
      150
    ),
    undefined
  );
  assert.equal(
    getProTuneApplyCandidate(
      doneStatus({ apply_decision: { ...doneStatus().apply_decision, allowed: "yes" } }) as never,
      150
    ),
    undefined
  );

  for (const apply_state of ["unavailable", "applying", "applied", "failed", "expired", "stale"]) {
    assert.equal(getProTuneApplyCandidate(doneStatus({ apply_state }), 150), undefined, apply_state);
  }
});

test("blocked sweeps present retained automatic throughput instead of a selected winner", () => {
  assert.match(tuneHeroSource, /const retainedAutomatic/);
  assert.match(tuneHeroSource, /status\.apply_decision\.auto_tps/);
  assert.match(tuneHeroSource, /Retained:/);
  assert.match(tuneHeroSource, /Ollama automatic/);
  assert.doesNotMatch(tuneHeroSource, /Winner:/);
  assert.match(tuneHeroSource, /status\.apply_decision\.allowed\s*&&\s*status\.baseline_tps/);
});

test("TuneHero hides apply for consumed and non-available sweeps and consumes locally before dispatch", () => {
  assert.match(tuneHeroSource, /getProTuneApplyCandidate\(status/);
  assert.match(tuneHeroSource, /apply_state: "applying"/);
  assert.match(tuneHeroSource, /applied_sweep_id: sweepId/);
  assert.match(tuneHeroSource, /apply_state: "applied"/);
  assert.match(tuneHeroSource, /tuned_model: res\.tuned_model/);
  assert.match(tuneHeroSource, /const tuneBusy/);
  assert.match(tuneHeroSource, /disabled=\{tuneBusy\}/);
});

test("a duplicate tune 409 running response is recognized for polling resume", async () => {
  assert.equal(
    isProTuneRunningConflict(new ApiError(409, "Conflict", { state: "running", error: "already active" })),
    true
  );
  assert.equal(isProTuneRunningConflict(new ApiError(409, "Conflict", { state: "failed" })), false);
  assert.equal(isProTuneRunningConflict(new ApiError(400, "Bad Request", { state: "running" })), false);
  assert.equal(isProTuneRunningConflict(new Error("409")), false);
  assert.match(tuneHeroSource, /isProTuneRunningConflict\(e\)/);
});

test("proTune preserves a duplicate-running 409 as an inspectable ApiError", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ state: "running", error: "A tune is already active." }), {
      status: 409,
      statusText: "Conflict",
      headers: { "Content-Type": "application/json" },
    })) as typeof fetch;

  try {
    await assert.rejects(api.proTune("model:latest"), (error: unknown) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 409);
      assert.deepEqual(error.body, { state: "running", error: "A tune is already active." });
      assert.equal(isProTuneRunningConflict(error), true);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
