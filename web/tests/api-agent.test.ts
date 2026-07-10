import assert from "node:assert/strict";
import test from "node:test";

import { api, ApiError } from "../src/lib/api.ts";

test("answerApproval encodes the run id and sends the exact capability body", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl: string | URL | Request | undefined;
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = (async (url, init) => {
    capturedUrl = url;
    capturedInit = init;
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const result = await api.answerApproval(
      "run/one ?#",
      "capability-token",
      { ask_id: "ask-1", decision: "allow", remember: true }
    );

    assert.deepEqual(result, { ok: true });
    assert.equal(capturedUrl, "/api/agent/runs/run%2Fone%20%3F%23/answer");
    assert.equal(capturedInit?.method, "POST");
    assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
      ask_id: "ask-1",
      approval_token: "capability-token",
      decision: "allow",
      remember: true,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

for (const status of [404, 409, 410, 504]) {
  test(`answerApproval preserves ApiError status ${status}`, async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: `failure-${status}` }), {
        status,
        headers: { "Content-Type": "application/json" },
      })) as typeof fetch;

    try {
      await assert.rejects(
        () =>
          api.answerApproval("run-1", "capability-token", {
            ask_id: "ask-1",
            decision: "deny",
            remember: false,
          }),
        (error: unknown) => {
          assert.ok(error instanceof ApiError);
          assert.equal(error.status, status);
          assert.deepEqual(error.body, { error: `failure-${status}` });
          return true;
        }
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
}
