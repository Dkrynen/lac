import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalFailureMessage,
  createApprovalAnswer,
  initialApprovalState,
  reduceApproval,
} from "../src/lib/agent-approval.ts";

const run = { type: "run" as const, runId: "run-a", approvalToken: "token-a" };
const ask = {
  type: "ask" as const,
  runId: "run-a",
  askId: "ask-a",
  tool: "write_file",
  target: "src/app.py",
  key: "edit",
  rememberable: true,
};

test("answers stay bound to the exact active run and ask", () => {
  const active = reduceApproval(reduceApproval(initialApprovalState, run), ask);

  assert.equal(createApprovalAnswer(active, "run-b", "ask-a", "allow", false), null);
  assert.equal(createApprovalAnswer(active, "run-a", "ask-b", "allow", false), null);
  assert.deepEqual(createApprovalAnswer(active, "run-a", "ask-a", "allow", false), {
    runId: "run-a",
    approvalToken: "token-a",
    body: { ask_id: "ask-a", decision: "allow", remember: false },
  });
});

test("beginning submission synchronously blocks a duplicate answer", () => {
  const active = reduceApproval(reduceApproval(initialApprovalState, run), ask);
  const submitting = reduceApproval(active, {
    type: "submit_started",
    runId: "run-a",
    askId: "ask-a",
  });

  assert.equal(createApprovalAnswer(submitting, "run-a", "ask-a", "deny", false), null);
  assert.equal(
    reduceApproval(submitting, { type: "submit_started", runId: "run-a", askId: "ask-a" }),
    submitting
  );
});

test("stale resolution and failure events cannot mutate a newer ask", () => {
  const active = reduceApproval(reduceApproval(initialApprovalState, run), ask);
  const staleResolved = reduceApproval(active, {
    type: "resolved",
    runId: "run-a",
    askId: "older-ask",
  });
  const staleFailure = reduceApproval(active, {
    type: "submit_failed",
    runId: "older-run",
    askId: "ask-a",
    message: "stale",
  });

  assert.equal(staleResolved, active);
  assert.equal(staleFailure, active);
});

test("remember is sent only for an allow on a backend-rememberable ask", () => {
  const rememberable = reduceApproval(reduceApproval(initialApprovalState, run), ask);
  assert.equal(
    createApprovalAnswer(rememberable, "run-a", "ask-a", "allow", true)?.body.remember,
    true
  );
  assert.equal(
    createApprovalAnswer(rememberable, "run-a", "ask-a", "deny", true)?.body.remember,
    false
  );

  const unsafe = reduceApproval(reduceApproval(initialApprovalState, run), {
    ...ask,
    rememberable: false,
  });
  assert.equal(createApprovalAnswer(unsafe, "run-a", "ask-a", "allow", true)?.body.remember, false);
});

test("approval failures distinguish missing, stale, expired, and acknowledgement timeout", () => {
  assert.equal(approvalFailureMessage(404), "This agent run no longer exists.");
  assert.equal(approvalFailureMessage(409), "This approval is no longer pending.");
  assert.equal(approvalFailureMessage(410), "This approval has expired.");
  assert.equal(approvalFailureMessage(504), "The agent did not acknowledge this decision; it was denied.");
  assert.equal(approvalFailureMessage(500), "The approval decision failed. The tool was not approved.");
});
