import type { AgentApprovalAnswerBody, AgentApprovalDecision } from "./types";

export interface ApprovalRun {
  runId: string;
  approvalToken: string;
}

export interface PendingApproval {
  runId: string;
  askId: string;
  tool: string;
  target: unknown;
  key: string;
  rememberable: boolean;
  submitting: boolean;
  submitted: boolean;
  error: string | null;
}

export interface ApprovalState {
  run: ApprovalRun | null;
  pending: PendingApproval | null;
}

export type ApprovalAction =
  | { type: "run"; runId: string; approvalToken: string }
  | {
      type: "ask";
      runId: string;
      askId: string;
      tool: string;
      target: unknown;
      key: string;
      rememberable?: boolean;
    }
  | { type: "submit_started"; runId: string; askId: string }
  | { type: "submit_succeeded"; runId: string; askId: string }
  | { type: "submit_failed"; runId: string; askId: string; message: string }
  | { type: "resolved"; runId: string; askId: string }
  | { type: "closed"; runId: string }
  | { type: "reset" };

export interface ApprovalAnswer {
  runId: string;
  approvalToken: string;
  body: AgentApprovalAnswerBody;
}

export const initialApprovalState: ApprovalState = {
  run: null,
  pending: null,
};

function hasValue(value: string): boolean {
  return value.trim().length > 0;
}

function matchesPending(
  state: ApprovalState,
  runId: string,
  askId: string
): state is ApprovalState & { run: ApprovalRun; pending: PendingApproval } {
  return Boolean(
    state.run &&
      state.pending &&
      state.run.runId === runId &&
      state.pending.runId === runId &&
      state.pending.askId === askId
  );
}

export function reduceApproval(state: ApprovalState, action: ApprovalAction): ApprovalState {
  switch (action.type) {
    case "run": {
      if (!hasValue(action.runId) || !hasValue(action.approvalToken)) {
        return initialApprovalState;
      }
      if (
        state.run?.runId === action.runId &&
        state.run.approvalToken === action.approvalToken
      ) {
        return state;
      }
      return {
        run: { runId: action.runId, approvalToken: action.approvalToken },
        pending: null,
      };
    }

    case "ask": {
      if (
        !state.run ||
        state.run.runId !== action.runId ||
        !hasValue(action.askId) ||
        !hasValue(action.tool) ||
        !hasValue(action.key)
      ) {
        return state;
      }
      if (state.pending) {
        return state;
      }
      return {
        ...state,
        pending: {
          runId: action.runId,
          askId: action.askId,
          tool: action.tool,
          target: action.target,
          key: action.key,
          rememberable: action.rememberable === true,
          submitting: false,
          submitted: false,
          error: null,
        },
      };
    }

    case "submit_started": {
      if (
        !matchesPending(state, action.runId, action.askId) ||
        state.pending.submitting ||
        state.pending.submitted
      ) {
        return state;
      }
      return {
        ...state,
        pending: { ...state.pending, submitting: true, error: null },
      };
    }

    case "submit_succeeded": {
      if (!matchesPending(state, action.runId, action.askId) || !state.pending.submitting) {
        return state;
      }
      return {
        ...state,
        pending: { ...state.pending, submitting: false, submitted: true, error: null },
      };
    }

    case "submit_failed": {
      if (!matchesPending(state, action.runId, action.askId) || !state.pending.submitting) {
        return state;
      }
      return {
        ...state,
        pending: {
          ...state.pending,
          submitting: false,
          submitted: false,
          error: action.message,
        },
      };
    }

    case "resolved": {
      if (!matchesPending(state, action.runId, action.askId)) {
        return state;
      }
      return { ...state, pending: null };
    }

    case "closed": {
      if (state.run?.runId !== action.runId) {
        return state;
      }
      return initialApprovalState;
    }

    case "reset":
      return initialApprovalState;
  }
}

export function createApprovalAnswer(
  state: ApprovalState,
  runId: string,
  askId: string,
  decision: AgentApprovalDecision,
  requestRemember: boolean
): ApprovalAnswer | null {
  if (
    !matchesPending(state, runId, askId) ||
    state.pending.submitting ||
    state.pending.submitted
  ) {
    return null;
  }

  return {
    runId,
    approvalToken: state.run.approvalToken,
    body: {
      ask_id: askId,
      decision,
      remember:
        decision === "allow" &&
        requestRemember === true &&
        state.pending.rememberable === true,
    },
  };
}

export function approvalFailureMessage(status: number): string {
  if (status === 404) return "This agent run no longer exists.";
  if (status === 409) return "This approval is no longer pending.";
  if (status === 410) return "This approval has expired.";
  if (status === 504) {
    return "The agent did not acknowledge this decision; it was denied.";
  }
  return "The approval decision failed. The tool was not approved.";
}
