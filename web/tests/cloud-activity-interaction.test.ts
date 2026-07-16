// @vitest-environment jsdom

import { createElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { getAllByText, getByRole, getByText, queryByRole, queryByText } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CloudActivity } from "@/pages/cloud-activity";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  cancelCloudJob: vi.fn(),
  cloudJobEvents: vi.fn(),
  cloudJobs: vi.fn(),
  productState: vi.fn(),
}));

const intervalMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/lib/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/lib/hooks")>("@/lib/hooks");
  return { ...actual, useInterval: intervalMock };
});
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

const JOB_ID = "00000000-0000-4000-8000-000000000001";
const TERMINAL_JOB_ID = "00000000-0000-4000-8000-000000000003";
const WORKSPACE_ID = "00000000-0000-4000-8000-000000000002";

const baseProduct = {
  schema_version: 1,
  execution_default: "local",
  local: { state: "ready" },
  local_pro: { state: "absent" },
};

const connectedProduct = {
  ...baseProduct,
  cloud: {
    state: "connected",
    execution_available: false,
    account: {
      id: "acct_123",
      primary_email: "duan@example.com",
      display_name: "Duan Krynen",
      avatar_url: null,
      status: "active",
      created_at: 1_752_537_600_000,
    },
    entitlements: [],
    usage: {
      monthlyCredits: 0,
      weeklyCredits: 0,
      shortWindowCredits: 0,
      activeJobs: 1,
      queuedJobs: 0,
      resetAt: {
        monthly: 1_755_216_000_000,
        weekly: 1_753_142_400_000,
        five_hour: 1_752_555_600_000,
      },
    },
  },
};

const activeJob = {
  id: JOB_ID,
  workspace_id: WORKSPACE_ID,
  model_alias: "code" as const,
  status: "running" as const,
  reserved_credits: 40,
  actual_credits: null,
  failure_code: null,
  created_at: 1_752_537_600,
  updated_at: 1_752_537_610,
  started_at: 1_752_537_605,
  finished_at: null,
};

const terminalJob = {
  ...activeJob,
  id: TERMINAL_JOB_ID,
  model_alias: "smart" as const,
  status: "succeeded" as const,
  actual_credits: 31,
  created_at: 1_752_537_400,
  updated_at: 1_752_537_500,
  started_at: 1_752_537_420,
  finished_at: 1_752_537_500,
};

const progress = {
  event_id: "runner:job:transition:running",
  sequence: 4,
  phase: "running" as const,
  message: "Runner transition: running",
  percent: 25,
  occurred_at: 1_752_537_610_000,
};

const activeSnapshot = {
  job: {
    id: JOB_ID,
    revision: 5,
    phase: "running" as const,
    latest_sequence: 4,
    latest_progress: progress,
    pending_approval: null,
    last_approval: null,
  },
  events: [progress],
};

const terminalSnapshot = {
  job: {
    ...activeSnapshot.job,
    id: TERMINAL_JOB_ID,
    revision: 6,
    phase: "completed" as const,
    latest_sequence: -1,
    latest_progress: null,
  },
  events: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function terminalEvent(sequence: number, occurredAt = 1_752_537_610_000) {
  return {
    event_id: `runner:event:${sequence}`,
    sequence,
    phase: "completed" as const,
    message: `Completed event ${sequence}`,
    percent: 100,
    occurred_at: occurredAt + sequence,
  };
}

function terminalEventPage(first: number, last: number, latestSequence = 149) {
  const events = Array.from(
    { length: last - first + 1 },
    (_, index) => terminalEvent(first + index),
  );
  return {
    job: {
      ...terminalSnapshot.job,
      latest_sequence: latestSequence,
      latest_progress: terminalEvent(latestSequence),
    },
    events,
  };
}

function productWithCloud(cloud: Record<string, unknown>) {
  return { ...baseProduct, cloud };
}

function mountPage(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  act(() => root.render(createElement(
    MemoryRouter,
    { future: { v7_startTransition: true, v7_relativeSplatPath: true } },
    createElement(CloudActivity),
  )));
  return { container, root };
}

async function flushAsyncWork() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("Cloud Activity connection and job controls", () => {
  let root: Root | null = null;

  beforeEach(() => {
    apiMocks.cancelCloudJob.mockReset();
    apiMocks.cloudJobEvents.mockReset();
    apiMocks.cloudJobs.mockReset();
    apiMocks.productState.mockReset();
    intervalMock.mockReset();
    apiMocks.cancelCloudJob.mockResolvedValue({ job: { id: JOB_ID, status: "cancelling" } });
    apiMocks.cloudJobEvents.mockImplementation((jobId: string) => (
      Promise.resolve(jobId === TERMINAL_JOB_ID ? terminalSnapshot : activeSnapshot)
    ));
    apiMocks.cloudJobs.mockResolvedValue({ jobs: [activeJob, terminalJob] });
  });

  afterEach(() => {
    if (root) act(() => root?.unmount());
    root = null;
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  it.each([
    ["not_configured", "Cloud is not configured"],
    ["signed_out", "Sign in to view Cloud activity"],
    ["authorizing", "Cloud sign-in is in progress"],
    ["unreachable", "Cloud is unreachable"],
  ])("does not request job data while Cloud is %s", async (state, message) => {
    const cloud = state === "unreachable"
      ? { state, execution_available: false, error: { code: "provider_unavailable" } }
      : { state, execution_available: false };
    apiMocks.productState.mockResolvedValue(productWithCloud(cloud));
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    expect(getByText(mounted.container, message)).toBeTruthy();
    expect(apiMocks.cloudJobs).not.toHaveBeenCalled();
    expect(apiMocks.cloudJobEvents).not.toHaveBeenCalled();
    expect(intervalMock.mock.calls.every((call) => call[1] === null)).toBe(true);
  });

  it("does not request or poll job data when product-state verification fails", async () => {
    apiMocks.productState.mockRejectedValue(new Error("Product state unavailable"));
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    expect(getByText(mounted.container, "Cloud state unavailable")).toBeTruthy();
    expect(getByText(mounted.container, "Product state unavailable")).toBeTruthy();
    expect(apiMocks.cloudJobs).not.toHaveBeenCalled();
    expect(apiMocks.cloudJobEvents).not.toHaveBeenCalled();
    expect(intervalMock.mock.calls.every((call) => call[1] === null)).toBe(true);
  });

  it("shows an honest empty state without offering job submission", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    apiMocks.cloudJobs.mockResolvedValue({ jobs: [] });
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    expect(getByText(mounted.container, "No Cloud jobs")).toBeTruthy();
    expect(queryByRole(mounted.container, "button", { name: /submit|create|new job/i })).toBeNull();
    expect(apiMocks.cloudJobEvents).not.toHaveBeenCalled();
  });

  it("renders active history and events, exposes only active cancellation, and refreshes after cancel", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    apiMocks.cloudJobs
      .mockResolvedValueOnce({ jobs: [activeJob, terminalJob] })
      .mockResolvedValue({ jobs: [{ ...activeJob, status: "cancelling" }, terminalJob] });
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    expect(getByText(mounted.container, "Runner transition: running")).toBeTruthy();
    const activeRow = getByRole(mounted.container, "button", { name: new RegExp(`Code.*Running`, "i") });
    const terminalRow = getByRole(mounted.container, "button", { name: new RegExp(`Smart.*Succeeded`, "i") });
    expect(activeRow).toBeTruthy();
    expect(terminalRow).toBeTruthy();
    expect(getByRole(mounted.container, "button", { name: `Cancel cloud job ${JOB_ID}` })).toBeTruthy();

    await act(async () => userEvent.setup().click(terminalRow));
    expect(queryByRole(mounted.container, "button", { name: `Cancel cloud job ${TERMINAL_JOB_ID}` })).toBeNull();

    await act(async () => userEvent.setup().click(activeRow));
    const cancel = getByRole(mounted.container, "button", { name: `Cancel cloud job ${JOB_ID}` });
    await act(async () => userEvent.setup().click(cancel));
    await flushAsyncWork();

    expect(apiMocks.cancelCloudJob).toHaveBeenCalledWith(JOB_ID);
    expect(apiMocks.cloudJobs).toHaveBeenCalledTimes(2);
    expect(queryByRole(mounted.container, "button", { name: `Cancel cloud job ${JOB_ID}` })).toBeNull();
  });

  it("does not replace a newly selected snapshot when cancellation resolves late", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    const cancellation = deferred<{ job: { id: string; status: "cancelling" } }>();
    apiMocks.cancelCloudJob.mockReturnValue(cancellation.promise);
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    const cancel = getByRole(mounted.container, "button", { name: `Cancel cloud job ${JOB_ID}` });
    const terminalRow = getByRole(mounted.container, "button", { name: new RegExp(`Smart.*Succeeded`, "i") });
    await act(async () => userEvent.setup().click(cancel));
    await act(async () => userEvent.setup().click(terminalRow));
    await flushAsyncWork();

    await act(async () => {
      cancellation.resolve({ job: { id: JOB_ID, status: "cancelling" } });
      await cancellation.promise;
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(apiMocks.cloudJobEvents.mock.calls.map((call) => call[0])).toEqual([JOB_ID, TERMINAL_JOB_ID]);
    expect(terminalRow.getAttribute("aria-pressed")).toBe("true");
    expect(queryByText(mounted.container, "Runner transition: running")).toBeNull();
  });

  it("shows approval metadata without offering an approval mutation", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    apiMocks.cloudJobs.mockResolvedValue({ jobs: [{ ...activeJob, status: "awaiting_approval" }] });
    apiMocks.cloudJobEvents.mockResolvedValue({
      job: {
        ...activeSnapshot.job,
        phase: "awaiting_approval",
        pending_approval: {
          approval_id: "approval_123",
          kind: "repository_write",
          summary: "Write the generated patch",
          requested_at: 1_752_537_611_000,
          expires_at: 1_752_541_211_000,
        },
      },
      events: [],
    });
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    expect(getByText(mounted.container, "Pending approval")).toBeTruthy();
    expect(getByText(mounted.container, "Write the generated patch")).toBeTruthy();
    expect(queryByRole(mounted.container, "button", { name: /approve|reject/i })).toBeNull();
  });

  it("keeps existing job data visible when a later refresh fails", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    apiMocks.cloudJobs
      .mockResolvedValueOnce({ jobs: [activeJob] })
      .mockRejectedValueOnce(new Error("Cloud activity unavailable"));
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    const refresh = getByRole(mounted.container, "button", { name: "Refresh Cloud activity" });
    await act(async () => userEvent.setup().click(refresh));
    await flushAsyncWork();

    expect(getByRole(mounted.container, "button", { name: new RegExp(`Code.*Running`, "i") })).toBeTruthy();
    expect(getByText(mounted.container, "Cloud activity unavailable")).toBeTruthy();
  });

  it("does not overlap slow silent job or event polls", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    const slowJobs = deferred<{ jobs: typeof activeJob[] }>();
    apiMocks.cloudJobs.mockReturnValue(slowJobs.promise);
    const jobPoll = [...intervalMock.mock.calls].reverse().find((call) => call[1] === 4_000)?.[0];
    expect(jobPoll).toBeTypeOf("function");
    act(() => {
      jobPoll();
      jobPoll();
    });
    expect(apiMocks.cloudJobs).toHaveBeenCalledTimes(2);

    await act(async () => {
      slowJobs.resolve({ jobs: [activeJob, terminalJob] });
      await slowJobs.promise;
    });

    const slowEvents = deferred<typeof activeSnapshot>();
    apiMocks.cloudJobEvents.mockReturnValue(slowEvents.promise);
    const eventPoll = [...intervalMock.mock.calls].reverse().find((call) => call[1] === 2_000)?.[0];
    expect(eventPoll).toBeTypeOf("function");
    act(() => {
      eventPoll();
      eventPoll();
    });
    expect(apiMocks.cloudJobEvents).toHaveBeenCalledTimes(2);

    await act(async () => {
      slowEvents.resolve(activeSnapshot);
      await slowEvents.promise;
    });
  });

  it("paginates terminal event history beyond the 100-event response limit", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    apiMocks.cloudJobs.mockResolvedValue({ jobs: [terminalJob] });
    apiMocks.cloudJobEvents.mockImplementation((_jobId: string, afterSequence: number) => {
      if (afterSequence === -1) return Promise.resolve(terminalEventPage(0, 99));
      if (afterSequence === 99) return Promise.resolve(terminalEventPage(100, 149));
      throw new Error(`Unexpected cursor ${afterSequence}`);
    });
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();
    await flushAsyncWork();

    expect(apiMocks.cloudJobEvents.mock.calls.map((call) => call[1])).toEqual([-1, 99]);
    expect(getByText(mounted.container, "Completed event 149")).toBeTruthy();
    expect(intervalMock.mock.calls.every((call) => call[1] === null)).toBe(true);
  });

  it("supports retry and refresh recovery after terminal pagination failures", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    apiMocks.cloudJobs.mockResolvedValue({ jobs: [terminalJob] });
    let mode: "fail" | "success" = "fail";
    apiMocks.cloudJobEvents.mockImplementation((_jobId: string, afterSequence: number) => {
      if (afterSequence === -1) return Promise.resolve(terminalEventPage(0, 99));
      if (afterSequence === 99 && mode === "fail") return Promise.reject(new Error("Event page unavailable"));
      if (afterSequence === 99) return Promise.resolve(terminalEventPage(100, 149));
      throw new Error(`Unexpected cursor ${afterSequence}`);
    });
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();
    await flushAsyncWork();

    expect(getByText(mounted.container, "Event page unavailable")).toBeTruthy();
    expect(getByText(mounted.container, "Completed event 99")).toBeTruthy();
    mode = "success";
    await act(async () => userEvent.setup().click(
      getByRole(mounted.container, "button", { name: "Retry Cloud job events" }),
    ));
    await flushAsyncWork();
    expect(queryByText(mounted.container, "Event page unavailable")).toBeNull();
    expect(getByText(mounted.container, "Completed event 149")).toBeTruthy();

    mode = "fail";
    await act(async () => userEvent.setup().click(
      getByRole(mounted.container, "button", { name: "Refresh Cloud activity" }),
    ));
    await flushAsyncWork();
    await flushAsyncWork();
    expect(getByText(mounted.container, "Event page unavailable")).toBeTruthy();

    mode = "success";
    await act(async () => userEvent.setup().click(
      getByRole(mounted.container, "button", { name: "Refresh Cloud activity" }),
    ));
    await flushAsyncWork();
    await flushAsyncWork();
    expect(queryByText(mounted.container, "Event page unavailable")).toBeNull();
    expect(getByText(mounted.container, "Completed event 149")).toBeTruthy();
    expect(apiMocks.cloudJobEvents.mock.calls.filter((call) => call[1] === -1)).toHaveLength(3);
  });

  it("renders out-of-range Date values without crashing", async () => {
    const invalidTimestamp = Number.MAX_SAFE_INTEGER;
    const invalidJob = {
      ...terminalJob,
      created_at: invalidTimestamp,
      updated_at: invalidTimestamp,
      started_at: invalidTimestamp,
      finished_at: invalidTimestamp,
    };
    const invalidEvent = terminalEvent(0, invalidTimestamp);
    apiMocks.productState.mockResolvedValue(connectedProduct);
    apiMocks.cloudJobs.mockResolvedValue({ jobs: [invalidJob] });
    apiMocks.cloudJobEvents.mockResolvedValue({
      job: {
        ...terminalSnapshot.job,
        latest_sequence: 0,
        latest_progress: invalidEvent,
      },
      events: [invalidEvent],
    });
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    expect(getAllByText(mounted.container, "Invalid timestamp").length).toBeGreaterThanOrEqual(3);
    expect(mounted.container.querySelector("time")?.hasAttribute("datetime")).toBe(false);
  });

  it("enables polling for a connected active selection and stops it for terminal history", async () => {
    apiMocks.productState.mockResolvedValue(connectedProduct);
    const mounted = mountPage();
    root = mounted.root;
    await flushAsyncWork();

    expect(intervalMock.mock.calls.some((call) => call[1] === 4_000)).toBe(true);
    expect(intervalMock.mock.calls.some((call) => call[1] === 2_000)).toBe(true);

    act(() => root?.unmount());
    root = null;
    document.body.replaceChildren();
    intervalMock.mockClear();
    apiMocks.cloudJobs.mockResolvedValue({ jobs: [terminalJob] });
    const terminalMounted = mountPage();
    root = terminalMounted.root;
    await flushAsyncWork();

    expect(intervalMock.mock.calls.every((call) => call[1] === null)).toBe(true);
  });
});
