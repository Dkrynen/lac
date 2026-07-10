import type { AgentApprovalDecision } from "./types";

export type WorkbenchMode = "ask" | "plan" | "explore" | "build";

export interface ApprovalDecisionIntent {
  runId: string;
  askId: string;
  decision: AgentApprovalDecision;
  remember: boolean;
}

export interface ApprovalResponseState {
  run: { runId: string } | null;
  pending: { runId: string; askId: string } | null;
}

export interface WorkbenchSendState {
  model: string;
  mode: WorkbenchMode;
  projectRoot: string;
  input: string;
  warming: boolean;
  streaming: boolean;
  sessionLoading: boolean;
}

export interface SessionActionIdentity {
  sessionId: string;
  generation: number;
}

export interface StagedListIdentity {
  sessionId: string;
  sequence: number;
}

export interface StagedActionFailure {
  title: string;
  description: string;
}

export const STAGED_SNAPSHOT_LABEL = "Snapshot at staging";

export function approvalDecisionIntent(
  approval: { runId: string; askId: string },
  decision: AgentApprovalDecision,
  remember: boolean
): ApprovalDecisionIntent {
  return {
    runId: approval.runId,
    askId: approval.askId,
    decision,
    remember,
  };
}

export function approvalLockKey(runId: string, askId: string): string {
  return JSON.stringify([runId, askId]);
}

export function releaseApprovalLock(
  currentLock: string,
  runId: string,
  askId: string
): string {
  return currentLock === approvalLockKey(runId, askId) ? "" : currentLock;
}

export function isApprovalResponseRelevant(
  state: ApprovalResponseState,
  runId: string,
  askId: string
): boolean {
  return Boolean(
    state.run?.runId === runId &&
    state.pending?.runId === runId &&
    state.pending.askId === askId
  );
}

export function buildNeedsProjectRoot(mode: WorkbenchMode, projectRoot: string): boolean {
  return mode === "build" && projectRoot.trim().length === 0;
}

export function workbenchSendLabel(mode: WorkbenchMode, projectRoot: string): string {
  return buildNeedsProjectRoot(mode, projectRoot) ? "Set project root" : "Send";
}

export function workbenchControlsDisabled(streaming: boolean, sessionLoading: boolean): boolean {
  return streaming || sessionLoading;
}

export function workbenchSendDisabled(state: WorkbenchSendState): boolean {
  return (
    !state.model ||
    state.warming ||
    workbenchControlsDisabled(state.streaming, state.sessionLoading) ||
    !state.input.trim() ||
    buildNeedsProjectRoot(state.mode, state.projectRoot)
  );
}

export function isCurrentGeneration(current: number, expected: number): boolean {
  return current === expected;
}

export function isCurrentSessionAction(
  activeSessionId: string,
  currentGeneration: number,
  action: SessionActionIdentity
): boolean {
  return (
    activeSessionId === action.sessionId &&
    isCurrentGeneration(currentGeneration, action.generation)
  );
}

export function shouldCommitStagedList(
  activeSessionId: string,
  currentSequence: number,
  request: StagedListIdentity
): boolean {
  return activeSessionId === request.sessionId && currentSequence === request.sequence;
}

export function stagedFullPath(root: string, path: string): string {
  const trimmedRoot = root.trim().replace(/[\\/]+$/, "");
  const trimmedPath = path.trim().replace(/^[\\/]+/, "");
  if (!trimmedRoot) return trimmedPath;
  if (!trimmedPath) return trimmedRoot;
  const separator = trimmedRoot.includes("\\") ? "\\" : "/";
  return `${trimmedRoot}${separator}${trimmedPath}`;
}

function responseField(body: unknown, field: string): string {
  if (!body || typeof body !== "object" || !(field in body)) return "";
  const value = (body as Record<string, unknown>)[field];
  return typeof value === "string" ? value : "";
}

export function stagedActionFailure(
  action: "apply" | "reject",
  status: number,
  body: unknown
): StagedActionFailure | null {
  if (status !== 409) return null;

  const responseStatus = responseField(body, "status");
  if (action === "apply" && responseStatus === "conflict") {
    return {
      title: "Apply blocked by a disk conflict",
      description: "The file changed after staging. Nothing was overwritten.",
    };
  }

  if (responseStatus === "not_pending") {
    const current = responseField(body, "current") || "unknown";
    return {
      title: `Could not ${action} staged change`,
      description: `This staged change is no longer pending (current status: ${current}).`,
    };
  }

  const error = responseField(body, "error");
  return {
    title: `Could not ${action} staged change`,
    description: error || "The server rejected this action because the staged change state changed.",
  };
}
