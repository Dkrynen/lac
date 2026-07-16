export const CLOUD_JOB_STATUSES = [
  "reserved_pending",
  "queued",
  "reserved",
  "running",
  "awaiting_approval",
  "cancelling",
  "cancelled",
  "succeeded",
  "failed",
] as const;

export type CloudJobStatus = (typeof CLOUD_JOB_STATUSES)[number];

export const CLOUD_JOB_PHASES = [
  "queued",
  "running",
  "awaiting_approval",
  "completed",
  "failed",
  "cancelled",
] as const;

export type CloudJobPhase = (typeof CLOUD_JOB_PHASES)[number];

export type CloudModelAlias = "fast" | "smart" | "code" | "smart_plus" | "code_plus" | "expert";
export type CloudApprovalKind =
  | "repository_write"
  | "pr_create"
  | "network_widening"
  | "secret_access"
  | "destructive_command"
  | "port_exposure";
export type CloudApprovalDecision = "approved" | "rejected" | "expired";

export interface CloudJob {
  id: string;
  workspace_id: string;
  model_alias: CloudModelAlias;
  status: CloudJobStatus;
  reserved_credits: number;
  actual_credits: number | null;
  failure_code: string | null;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface CloudJobsResponse {
  jobs: CloudJob[];
}

export interface CloudJobEvent {
  event_id: string;
  sequence: number;
  phase: CloudJobPhase;
  message: string;
  percent: number | null;
  occurred_at: number;
}

export interface CloudPendingApproval {
  approval_id: string;
  kind: CloudApprovalKind;
  summary: string;
  requested_at: number;
  expires_at: number;
}

export interface CloudResolvedApproval {
  approval_id: string;
  decision: CloudApprovalDecision;
  resolved_at: number;
}

export interface CloudJobEventState {
  id: string;
  revision: number;
  phase: CloudJobPhase;
  latest_sequence: number;
  latest_progress: CloudJobEvent | null;
  pending_approval: CloudPendingApproval | null;
  last_approval: CloudResolvedApproval | null;
}

export interface CloudJobEventsResponse {
  job: CloudJobEventState;
  events: CloudJobEvent[];
}

export interface CloudJobCancelResponse {
  job: { id: string; status: CloudJobStatus };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const EVENT_ID = /^[A-Za-z0-9][A-Za-z0-9:_-]{0,255}$/u;
const APPROVAL_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/u;
const FAILURE_CODE = /^[a-z][a-z0-9_]{0,127}$/u;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const MAX_EVENTS = 100;
const MAX_JOBS = 100;
const MAX_RESERVED_CREDITS = 1_000;
const MAX_APPROVAL_LIFETIME_MS = 7 * 24 * 60 * 60 * 1_000;

const JOB_STATUS_SET = new Set<string>(CLOUD_JOB_STATUSES);
const ACTIVE_STATUS_SET = new Set<CloudJobStatus>([
  "reserved_pending", "queued", "reserved", "running", "awaiting_approval", "cancelling",
]);
const CANCELLABLE_STATUS_SET = new Set<CloudJobStatus>([
  "reserved_pending", "queued", "reserved", "running", "awaiting_approval",
]);
const JOB_PHASE_SET = new Set<string>(CLOUD_JOB_PHASES);
const MODEL_ALIASES = new Set<string>(["fast", "smart", "code", "smart_plus", "code_plus", "expert"]);
const APPROVAL_KINDS = new Set<string>([
  "repository_write", "pr_create", "network_widening", "secret_access",
  "destructive_command", "port_exposure",
]);
const APPROVAL_DECISIONS = new Set<string>(["approved", "rejected", "expired"]);

function fail(label: string): never {
  throw new Error(`Invalid ${label}`);
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail(label);
  return value as Record<string, unknown>;
}

function exact(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  const result = record(value, label);
  const actual = Object.keys(result).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    return fail(label);
  }
  return result;
}

function safeInteger(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
}

function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maximum &&
    !CONTROL_CHARACTERS.test(value);
}

function jobStatus(value: unknown, label: string): CloudJobStatus {
  if (typeof value !== "string" || !JOB_STATUS_SET.has(value)) return fail(label);
  return value as CloudJobStatus;
}

function jobPhase(value: unknown, label: string): CloudJobPhase {
  if (typeof value !== "string" || !JOB_PHASE_SET.has(value)) return fail(label);
  return value as CloudJobPhase;
}

export function normalizeCloudJobId(value: string): string {
  if (typeof value !== "string" || !UUID.test(value)) throw new Error("Invalid cloud job identity");
  return value;
}

export function normalizeCloudAfterSequence(value: number): number {
  if (!safeInteger(value, -1)) throw new Error("Invalid cloud event cursor");
  return value;
}

function decodeJob(value: unknown, label: string): CloudJob {
  const row = exact(value, [
    "id", "workspace_id", "model_alias", "status", "reserved_credits", "actual_credits",
    "failure_code", "created_at", "updated_at", "started_at", "finished_at",
  ], label);
  const status = jobStatus(row.status, label);
  if (
    typeof row.id !== "string" || !UUID.test(row.id) ||
    typeof row.workspace_id !== "string" || !UUID.test(row.workspace_id) ||
    typeof row.model_alias !== "string" || !MODEL_ALIASES.has(row.model_alias) ||
    !safeInteger(row.reserved_credits, 1, MAX_RESERVED_CREDITS) ||
    !(row.actual_credits === null || safeInteger(row.actual_credits, 0, row.reserved_credits)) ||
    !(row.failure_code === null || (typeof row.failure_code === "string" && FAILURE_CODE.test(row.failure_code))) ||
    !safeInteger(row.created_at) || !safeInteger(row.updated_at) || row.updated_at < row.created_at ||
    !(row.started_at === null || (safeInteger(row.started_at) && row.started_at >= row.created_at)) ||
    !(row.finished_at === null || (safeInteger(row.finished_at) &&
      row.finished_at >= (row.started_at ?? row.created_at)))
  ) return fail(label);
  return { ...row, status } as CloudJob;
}

export function decodeCloudJobsResponse(value: unknown): CloudJobsResponse {
  const label = "cloud jobs response";
  const envelope = exact(value, ["jobs"], label);
  if (!Array.isArray(envelope.jobs) || envelope.jobs.length > MAX_JOBS) return fail(label);
  const jobs = envelope.jobs.map((row) => decodeJob(row, label));
  if (new Set(jobs.map((row) => row.id)).size !== jobs.length) return fail(label);
  for (let index = 1; index < jobs.length; index += 1) {
    if (jobs[index]!.created_at > jobs[index - 1]!.created_at) return fail(label);
  }
  return { jobs };
}

function decodeEvent(value: unknown, label: string): CloudJobEvent {
  const row = exact(value, ["event_id", "sequence", "phase", "message", "percent", "occurred_at"], label);
  const phase = jobPhase(row.phase, label);
  if (
    typeof row.event_id !== "string" || !EVENT_ID.test(row.event_id) ||
    !safeInteger(row.sequence) ||
    !boundedText(row.message, 2_000) ||
    !(row.percent === null || safeInteger(row.percent, 0, 100)) ||
    !safeInteger(row.occurred_at)
  ) return fail(label);
  return { ...row, phase } as CloudJobEvent;
}

function decodePendingApproval(value: unknown, label: string): CloudPendingApproval | null {
  if (value === null) return null;
  const row = exact(value, ["approval_id", "kind", "summary", "requested_at", "expires_at"], label);
  if (
    typeof row.approval_id !== "string" || !APPROVAL_ID.test(row.approval_id) ||
    typeof row.kind !== "string" || !APPROVAL_KINDS.has(row.kind) ||
    !boundedText(row.summary, 4_000) ||
    !safeInteger(row.requested_at) || !safeInteger(row.expires_at) ||
    row.expires_at <= row.requested_at ||
    row.expires_at - row.requested_at > MAX_APPROVAL_LIFETIME_MS
  ) return fail(label);
  return row as unknown as CloudPendingApproval;
}

function decodeLastApproval(value: unknown, label: string): CloudResolvedApproval | null {
  if (value === null) return null;
  const row = exact(value, ["approval_id", "decision", "resolved_at"], label);
  if (
    typeof row.approval_id !== "string" || !APPROVAL_ID.test(row.approval_id) ||
    typeof row.decision !== "string" || !APPROVAL_DECISIONS.has(row.decision) ||
    !safeInteger(row.resolved_at)
  ) return fail(label);
  return row as unknown as CloudResolvedApproval;
}

export function decodeCloudJobEventsResponse(
  value: unknown,
  expectedJobId: string,
  afterSequence = -1,
): CloudJobEventsResponse {
  const label = "cloud job events response";
  const routeJobId = normalizeCloudJobId(expectedJobId);
  const cursor = normalizeCloudAfterSequence(afterSequence);
  const envelope = exact(value, ["job", "events"], label);
  const state = exact(envelope.job, [
    "id", "revision", "phase", "latest_sequence", "latest_progress", "pending_approval", "last_approval",
  ], label);
  const phase = jobPhase(state.phase, label);
  const latestProgress = state.latest_progress === null ? null : decodeEvent(state.latest_progress, label);
  const pendingApproval = decodePendingApproval(state.pending_approval, label);
  const lastApproval = decodeLastApproval(state.last_approval, label);
  if (
    state.id !== routeJobId || !safeInteger(state.revision) ||
    !safeInteger(state.latest_sequence, -1) ||
    (state.latest_sequence === -1 && latestProgress !== null) ||
    (latestProgress !== null && latestProgress.sequence > state.latest_sequence)
  ) return fail(label);
  if (!Array.isArray(envelope.events) || envelope.events.length > MAX_EVENTS) return fail(label);
  const events = envelope.events.map((row) => decodeEvent(row, label));
  let previousSequence = cursor;
  for (const row of events) {
    if (row.sequence <= previousSequence || row.sequence > state.latest_sequence) {
      return fail(label);
    }
    previousSequence = row.sequence;
  }
  return {
    job: {
      id: routeJobId,
      revision: state.revision,
      phase,
      latest_sequence: state.latest_sequence,
      latest_progress: latestProgress,
      pending_approval: pendingApproval,
      last_approval: lastApproval,
    } as CloudJobEventState,
    events,
  };
}

export function decodeCloudJobCancelResponse(
  value: unknown,
  expectedJobId: string,
): CloudJobCancelResponse {
  const label = "cloud job cancellation response";
  const routeJobId = normalizeCloudJobId(expectedJobId);
  const envelope = exact(value, ["job"], label);
  const row = exact(envelope.job, ["id", "status"], label);
  const status = jobStatus(row.status, label);
  if (row.id !== routeJobId) return fail(label);
  return { job: { id: routeJobId, status } };
}

export function isCloudJobActive(status: CloudJobStatus): boolean {
  return ACTIVE_STATUS_SET.has(status);
}

export function isCloudJobCancellable(status: CloudJobStatus): boolean {
  return CANCELLABLE_STATUS_SET.has(status);
}
