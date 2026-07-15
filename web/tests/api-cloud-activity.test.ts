import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "vitest";

import { api } from "../src/lib/api.ts";

const JOB_ID = "00000000-0000-4000-8000-000000000001";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000002";

const job = {
  id: JOB_ID,
  workspace_id: WORKSPACE_ID,
  model_alias: "code",
  status: "running",
  reserved_credits: 40,
  actual_credits: null,
  failure_code: null,
  created_at: 1_752_537_600,
  updated_at: 1_752_537_610,
  started_at: 1_752_537_605,
  finished_at: null,
};

const progress = {
  event_id: "runner:job:transition:running",
  sequence: 4,
  phase: "running",
  message: "Runner transition: running",
  percent: 25,
  occurred_at: 1_752_537_610_000,
};

let originalFetch: typeof fetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("Cloud activity APIs use exact no-store endpoints without browser credentials", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const responses = [
    { jobs: [job] },
    {
      job: {
        id: JOB_ID,
        revision: 5,
        phase: "running",
        latest_sequence: 4,
        latest_progress: progress,
        pending_approval: null,
        last_approval: null,
      },
      events: [progress],
    },
    { job: { id: JOB_ID, status: "cancelling" } },
  ];
  globalThis.fetch = (async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify(responses.shift()), {
      status: calls.length === 3 ? 202 : 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const controller = new AbortController();
  assert.equal((await api.cloudJobs(controller.signal)).jobs[0]?.id, JOB_ID);
  assert.equal((await api.cloudJobEvents(JOB_ID, 3, controller.signal)).events[0]?.sequence, 4);
  assert.equal((await api.cancelCloudJob(JOB_ID)).job.status, "cancelling");

  assert.deepEqual(calls.map((call) => call.url), [
    "/api/cloud/jobs",
    `/api/cloud/jobs/${JOB_ID}/events?after_sequence=3`,
    `/api/cloud/jobs/${JOB_ID}/cancel`,
  ]);
  assert.equal(calls[0]?.init?.cache, "no-store");
  assert.equal(calls[1]?.init?.cache, "no-store");
  assert.equal(calls[0]?.init?.credentials, "omit");
  assert.equal(calls[1]?.init?.credentials, "omit");
  assert.equal(calls[2]?.init?.credentials, "omit");
  assert.equal(calls[0]?.init?.signal, controller.signal);
  assert.equal(calls[1]?.init?.signal, controller.signal);
  assert.equal(calls[2]?.init?.method, "POST");
  assert.equal(calls[2]?.init?.body, undefined);
  assert.equal(new Headers(calls[2]?.init?.headers).get("Content-Type"), null);
  for (const call of calls) {
    const headers = new Headers(call.init?.headers);
    assert.equal(headers.get("Authorization"), null);
    assert.equal(headers.get("Cookie"), null);
    assert.equal(String(call.init?.body ?? "").includes("token"), false);
  }
});

test("Cloud activity APIs reject unsafe route inputs before fetch", async () => {
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    throw new Error("fetch must not run");
  }) as typeof fetch;

  await assert.rejects(() => api.cloudJobEvents("job/../secret", -1), /invalid cloud job identity/i);
  await assert.rejects(() => api.cloudJobEvents(JOB_ID, -2), /invalid cloud event cursor/i);
  await assert.rejects(() => api.cancelCloudJob("job?token=secret"), /invalid cloud job identity/i);
  assert.equal(calls, 0);
});

test("Cloud activity APIs fail closed on malformed response fields", async () => {
  globalThis.fetch = (async () => new Response(JSON.stringify({
    jobs: [{ ...job, access_token: "must-not-enter-browser-state" }],
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })) as typeof fetch;

  await assert.rejects(() => api.cloudJobs(), /invalid cloud jobs response/i);
});
