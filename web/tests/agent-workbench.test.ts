import assert from "node:assert/strict";
import test from "node:test";

import {
  STAGED_SNAPSHOT_LABEL,
  approvalLockKey,
  approvalDecisionIntent,
  buildNeedsProjectRoot,
  isApprovalResponseRelevant,
  isCurrentGeneration,
  isCurrentSessionAction,
  releaseApprovalLock,
  shouldCommitStagedList,
  stagedActionFailure,
  stagedFullPath,
  workbenchControlsDisabled,
  workbenchSendDisabled,
  workbenchSendLabel,
} from "../src/lib/agent-workbench.ts";

test("approval actions preserve the run and ask rendered by the card", () => {
  const intent = approvalDecisionIntent(
    { runId: "run-rendered", askId: "ask-rendered" },
    "allow",
    true
  );

  assert.deepEqual(intent, {
    runId: "run-rendered",
    askId: "ask-rendered",
    decision: "allow",
    remember: true,
  });
});

test("approval responses are relevant only while their exact run and ask remain pending", () => {
  const newerAsk = {
    run: { runId: "run-a", approvalToken: "token-a" },
    pending: { runId: "run-a", askId: "ask-b" },
  };

  assert.equal(isApprovalResponseRelevant(newerAsk, "run-a", "ask-a"), false);
  assert.equal(isApprovalResponseRelevant(newerAsk, "run-a", "ask-b"), true);
  assert.equal(isApprovalResponseRelevant(newerAsk, "run-b", "ask-b"), false);
  assert.equal(
    isApprovalResponseRelevant({ run: newerAsk.run, pending: null }, "run-a", "ask-a"),
    false
  );
});

test("approval lock release clears only the exact lock owner", () => {
  const lockA = approvalLockKey("run-a", "ask-a");
  const lockB = approvalLockKey("run-a", "ask-b");

  assert.equal(releaseApprovalLock(lockA, "run-a", "ask-a"), "");
  assert.equal(releaseApprovalLock(lockB, "run-a", "ask-a"), lockB);
  assert.equal(releaseApprovalLock(lockA, "run-b", "ask-a"), lockA);
});

test("Build requires a nonempty project root and labels its disabled send action", () => {
  assert.equal(buildNeedsProjectRoot("build", ""), true);
  assert.equal(buildNeedsProjectRoot("build", "   "), true);
  assert.equal(buildNeedsProjectRoot("build", "C:\\repo"), false);
  assert.equal(buildNeedsProjectRoot("plan", ""), false);
  assert.equal(workbenchSendLabel("build", ""), "Set project root");
  assert.equal(workbenchSendLabel("build", "C:\\repo"), "Send");
});

test("session loading disables Workbench controls and send even with otherwise valid input", () => {
  assert.equal(workbenchControlsDisabled(false, true), true);
  assert.equal(workbenchControlsDisabled(false, false), false);
  assert.equal(
    workbenchSendDisabled({
      model: "qwen",
      mode: "plan",
      projectRoot: "",
      input: "Inspect this",
      warming: false,
      streaming: false,
      sessionLoading: true,
    }),
    true
  );
  assert.equal(
    workbenchSendDisabled({
      model: "qwen",
      mode: "plan",
      projectRoot: "",
      input: "Inspect this",
      warming: false,
      streaming: false,
      sessionLoading: false,
    }),
    false
  );
});

test("only the latest staged-list request for the active session may commit", () => {
  assert.equal(
    shouldCommitStagedList("session-a", 4, { sessionId: "session-a", sequence: 4 }),
    true
  );
  assert.equal(
    shouldCommitStagedList("session-a", 4, { sessionId: "session-a", sequence: 3 }),
    false
  );
  assert.equal(
    shouldCommitStagedList("session-b", 4, { sessionId: "session-a", sequence: 4 }),
    false
  );
});

test("staged actions and stream updates are rejected after their generation changes", () => {
  assert.equal(
    isCurrentSessionAction("session-a", 8, { sessionId: "session-a", generation: 8 }),
    true
  );
  assert.equal(
    isCurrentSessionAction("session-a", 9, { sessionId: "session-a", generation: 8 }),
    false
  );
  assert.equal(
    isCurrentSessionAction("session-b", 8, { sessionId: "session-a", generation: 8 }),
    false
  );
  assert.equal(isCurrentGeneration(12, 12), true);
  assert.equal(isCurrentGeneration(13, 12), false);
});

test("staged identities display the full root, path, and immutable snapshot wording", () => {
  assert.equal(stagedFullPath("C:\\work\\repo", "src\\app.ts"), "C:\\work\\repo\\src\\app.ts");
  assert.equal(stagedFullPath("/work/repo/", "/src/app.ts"), "/work/repo/src/app.ts");
  assert.equal(STAGED_SNAPSHOT_LABEL, "Snapshot at staging");
});

test("staged 409 responses distinguish disk conflicts from no-longer-pending state", () => {
  assert.deepEqual(
    stagedActionFailure("apply", 409, {
      status: "conflict",
      disk_hash: "disk",
      base_hash: "base",
    }),
    {
      title: "Apply blocked by a disk conflict",
      description: "The file changed after staging. Nothing was overwritten.",
    }
  );
  assert.deepEqual(
    stagedActionFailure("apply", 409, { status: "not_pending", current: "applied" }),
    {
      title: "Could not apply staged change",
      description: "This staged change is no longer pending (current status: applied).",
    }
  );
  assert.deepEqual(
    stagedActionFailure("reject", 409, { status: "not_pending", current: "rejected" }),
    {
      title: "Could not reject staged change",
      description: "This staged change is no longer pending (current status: rejected).",
    }
  );
});
