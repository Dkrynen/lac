import { describe, expect, it } from "vitest";

import publicDesktopContract from "../../../tests/fixtures/public-desktop-jobs.v1.json";

import {
  decodeCloudJobCancelResponse,
  decodeCloudJobEventsResponse,
  decodeCloudJobsResponse,
  isCloudJobActive,
  isCloudJobCancellable,
  normalizeCloudAfterSequence,
} from "./cloud-activity.ts";

const JOB_ID = "00000000-0000-4000-8000-000000000001";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000002";

function job(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

function event(overrides: Record<string, unknown> = {}) {
  return {
    event_id: "runner:job:transition:running",
    sequence: 4,
    phase: "running",
    message: "Runner transition: running",
    percent: 25,
    occurred_at: 1_752_537_610_000,
    ...overrides,
  };
}

function snapshot(overrides: Record<string, unknown> = {}) {
  const latest = event();
  return {
    job: {
      id: JOB_ID,
      revision: 5,
      phase: "running",
      latest_sequence: 4,
      latest_progress: latest,
      pending_approval: null,
      last_approval: null,
    },
    events: [latest],
    ...overrides,
  };
}

describe("Cloud job list decoder", () => {
  it("accepts the exact bounded public history contract", () => {
    const terminal = job({
      id: "00000000-0000-4000-8000-000000000003",
      model_alias: "smart_plus",
      status: "succeeded",
      actual_credits: 31,
      updated_at: 1_752_537_500,
      started_at: 1_752_537_420,
      finished_at: 1_752_537_500,
      created_at: 1_752_537_400,
    });
    const decoded = decodeCloudJobsResponse({ jobs: [job(), terminal] });

    expect(decoded.jobs).toHaveLength(2);
    expect(decoded.jobs[0]?.status).toBe("running");
    expect(decoded.jobs[1]?.model_alias).toBe("smart_plus");
  });

  it("accepts the canonical 128-character failure code bound", () => {
    expect(decodeCloudJobsResponse({ jobs: [job({ failure_code: "a".repeat(128) })] })
      .jobs[0]?.failure_code).toHaveLength(128);
    expect(() => decodeCloudJobsResponse({ jobs: [job({ failure_code: "a".repeat(129) })] }))
      .toThrow(/invalid cloud jobs response/i);
  });

  it.each([
    ["extra envelope field", { jobs: [], token: "secret" }],
    ["extra row field", { jobs: [job({ access_token: "secret" })] }],
    ["unknown model alias", { jobs: [job({ model_alias: "unbounded-model" })] }],
    ["unknown status", { jobs: [job({ status: "starting" })] }],
    ["zero reservation", { jobs: [job({ reserved_credits: 0 })] }],
    ["oversized reservation", { jobs: [job({ reserved_credits: 1_001 })] }],
    ["actual above reservation", { jobs: [job({ actual_credits: 41 })] }],
    ["invalid failure code", { jobs: [job({ failure_code: "Bad failure" })] }],
    ["backwards update time", { jobs: [job({ updated_at: 1_752_537_599 })] }],
    ["invalid UUID", { jobs: [job({ id: "job-1" })] }],
    ["duplicate jobs", { jobs: [job(), job()] }],
    ["out of order jobs", { jobs: [job(), job({
      id: "00000000-0000-4000-8000-000000000004",
      created_at: 1_752_537_700,
      updated_at: 1_752_537_700,
      started_at: null,
    })] }],
  ])("rejects %s", (_name, value) => {
    expect(() => decodeCloudJobsResponse(value)).toThrow(/invalid cloud jobs response/i);
  });

  it("rejects histories above the server limit", () => {
    const jobs = Array.from({ length: 101 }, (_, index) => job({
      id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      created_at: 1_752_537_600 - index,
      updated_at: 1_752_537_610 - index,
      started_at: 1_752_537_605 - index,
    }));
    expect(() => decodeCloudJobsResponse({ jobs })).toThrow(/invalid cloud jobs response/i);
  });
});

describe("Cloud authoritative event snapshot decoder", () => {
  it("accepts exact progress and approval metadata", () => {
    const waitingEvent = event({
      event_id: "runner:approval:waiting",
      sequence: 5,
      phase: "awaiting_approval",
      message: "Approval required",
      percent: null,
      occurred_at: 1_752_537_611_000,
    });
    const value = {
      job: {
        id: JOB_ID,
        revision: 6,
        phase: "awaiting_approval",
        latest_sequence: 5,
        latest_progress: waitingEvent,
        pending_approval: {
          approval_id: "approval_123",
          kind: "repository_write",
          summary: "Write the generated patch",
          requested_at: 1_752_537_611_000,
          expires_at: 1_752_541_211_000,
        },
        last_approval: {
          approval_id: "approval_122",
          decision: "rejected",
          resolved_at: 1_752_537_000_000,
        },
      },
      events: [event(), waitingEvent],
    };

    const decoded = decodeCloudJobEventsResponse(value, JOB_ID, 3);
    expect(decoded.job.pending_approval?.kind).toBe("repository_write");
    expect(decoded.events.map((row) => row.sequence)).toEqual([4, 5]);
  });

  it("accepts an omitted or older latest progress within the authoritative sequence", () => {
    expect(decodeCloudJobEventsResponse(snapshot({
      job: { ...snapshot().job, latest_sequence: 5, latest_progress: null },
      events: [],
    }), JOB_ID, 4).job.latest_progress).toBeNull();
    expect(decodeCloudJobEventsResponse(snapshot({
      job: { ...snapshot().job, latest_sequence: 5 },
      events: [],
    }), JOB_ID, 4).job.latest_progress?.sequence).toBe(4);
  });

  it.each([
    ["extra envelope field", { ...snapshot(), access_token: "secret" }],
    ["extra state field", { ...snapshot(), job: { ...snapshot().job, socket_token: "secret" } }],
    ["route identity mismatch", snapshot({ job: { ...snapshot().job, id: WORKSPACE_ID } })],
    ["unknown phase", snapshot({ job: { ...snapshot().job, phase: "starting" } })],
    ["latest progress above authoritative sequence", snapshot({
      job: { ...snapshot().job, latest_progress: event({ sequence: 5 }) },
    })],
    ["event above latest", snapshot({ events: [event({ sequence: 5 })] })],
    ["event at cursor", snapshot()],
    ["duplicate sequence", snapshot({ events: [event(), event({ event_id: "runner:other" })] })],
    ["event id containing a dot", snapshot({
      job: { ...snapshot().job, latest_progress: event({ event_id: "runner.event" }) },
      events: [event({ event_id: "runner.event" })],
    })],
    ["invalid percent", snapshot({
      job: { ...snapshot().job, latest_progress: event({ percent: 101 }) },
      events: [event({ percent: 101 })],
    })],
    ["oversized message", snapshot({
      job: { ...snapshot().job, latest_progress: event({ message: "x".repeat(2_001) }) },
      events: [event({ message: "x".repeat(2_001) })],
    })],
    ["control character", snapshot({
      job: { ...snapshot().job, latest_progress: event({ message: "bad\u0085message" }) },
      events: [event({ message: "bad\u0085message" })],
    })],
    ["invalid approval lifetime", snapshot({
      job: {
        ...snapshot().job,
        phase: "awaiting_approval",
        pending_approval: {
          approval_id: "approval_123",
          kind: "repository_write",
          summary: "Write patch",
          requested_at: 1_752_537_611_000,
          expires_at: 1_753_142_411_001,
        },
      },
    })],
  ])("rejects %s", (_name, value) => {
    const afterSequence = _name === "event at cursor" ? 4 : 2;
    expect(() => decodeCloudJobEventsResponse(value, JOB_ID, afterSequence))
      .toThrow(/invalid cloud job events response/i);
  });

  it("rejects more than 100 events", () => {
    const events = Array.from({ length: 101 }, (_, index) => event({
      event_id: `runner:event:${index}`,
      sequence: index,
    }));
    expect(() => decodeCloudJobEventsResponse({
      job: {
        ...snapshot().job,
        latest_sequence: 100,
        latest_progress: events[100],
      },
      events,
    }, JOB_ID, -1)).toThrow(/invalid cloud job events response/i);
  });
});

type PublicDesktopVector = {
  name: string;
  parser: string;
  valid: boolean;
  value: unknown;
};

const supportedCanonicalVectors = (publicDesktopContract.vectors as PublicDesktopVector[])
  .filter((vector) => [
    "job_list_response_v1",
    "job_event_snapshot_response_v1",
    "job_cancel_response_v1",
  ].includes(vector.parser));

function decodeCanonicalVector(vector: PublicDesktopVector): unknown {
  if (vector.parser === "job_list_response_v1") return decodeCloudJobsResponse(vector.value);
  const jobId = (vector.value as { job: { id: string } }).job.id;
  if (vector.parser === "job_event_snapshot_response_v1") {
    return decodeCloudJobEventsResponse(vector.value, jobId, -1);
  }
  return decodeCloudJobCancelResponse(vector.value, jobId);
}

describe("canonical public desktop contract vectors", () => {
  it.each(supportedCanonicalVectors)("$name", (vector) => {
    const operation = () => decodeCanonicalVector(vector);
    if (vector.valid) expect(operation).not.toThrow();
    else expect(operation).toThrow();
  });
});

describe("Cloud activity request and action guards", () => {
  it("normalizes the exact event cursor range", () => {
    expect(normalizeCloudAfterSequence(-1)).toBe(-1);
    expect(normalizeCloudAfterSequence(Number.MAX_SAFE_INTEGER)).toBe(Number.MAX_SAFE_INTEGER);
    expect(() => normalizeCloudAfterSequence(-2)).toThrow(/invalid cloud event cursor/i);
    expect(() => normalizeCloudAfterSequence(1.5)).toThrow(/invalid cloud event cursor/i);
  });

  it("accepts only the endpoint-bound cancellation response", () => {
    expect(decodeCloudJobCancelResponse({
      job: { id: JOB_ID, status: "cancelling" },
    }, JOB_ID)).toEqual({ job: { id: JOB_ID, status: "cancelling" } });
    expect(() => decodeCloudJobCancelResponse({
      job: { id: WORKSPACE_ID, status: "cancelling" },
    }, JOB_ID)).toThrow(/invalid cloud job cancellation response/i);
    expect(() => decodeCloudJobCancelResponse({
      job: { id: JOB_ID, status: "cancelling", token: "secret" },
    }, JOB_ID)).toThrow(/invalid cloud job cancellation response/i);
  });

  it("distinguishes polling and cancellation statuses", () => {
    expect(isCloudJobActive("cancelling")).toBe(true);
    expect(isCloudJobCancellable("awaiting_approval")).toBe(true);
    expect(isCloudJobCancellable("cancelling")).toBe(false);
    expect(isCloudJobActive("succeeded")).toBe(false);
  });
});
