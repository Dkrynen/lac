import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  CircleStop,
  CloudOff,
  History,
  Inbox,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  WifiOff,
} from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import {
  isCloudJobActive,
  isCloudJobCancellable,
  type CloudJob,
  type CloudJobEvent,
  type CloudJobEventsResponse,
  type CloudJobStatus,
} from "@/lib/cloud-activity";
import { useAsync, useInterval } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const JOB_POLL_MS = 4_000;
const EVENT_POLL_MS = 2_000;
const MAX_RETAINED_EVENTS = 500;
const MAX_EVENT_PAGES_PER_LOAD = 100;

function displayLabel(value: string): string {
  return value
    .split("_")
    .map((part) => part ? `${part[0]!.toUpperCase()}${part.slice(1)}` : "")
    .join(" ");
}

function statusVariant(status: CloudJobStatus): BadgeProps["variant"] {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "awaiting_approval") return "warning";
  if (status === "running" || status === "cancelling") return "info";
  return "outline";
}

function formatJobTime(value: number | null): string {
  if (value === null) return "Not recorded";
  const date = new Date(value * 1_000);
  if (Number.isNaN(date.getTime())) return "Invalid timestamp";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatEventTime(value: number): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Invalid timestamp";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function StatePanel({ icon, title, detail, action }: {
  icon: ReactNode;
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex min-h-[260px] flex-col items-center justify-center rounded border border-line bg-panel px-6 py-10 text-center">
      <div className="mb-3 text-fg-faint">{icon}</div>
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 max-w-sm text-[13px] text-fg-muted">{detail}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

function JobRow({ job, selected, onSelect }: {
  job: CloudJob;
  selected: boolean;
  onSelect: () => void;
}) {
  const model = displayLabel(job.model_alias);
  const status = displayLabel(job.status);
  return (
    <button
      type="button"
      aria-label={`${model} ${status} cloud job ${job.id}`}
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "grid min-h-[74px] w-full grid-cols-[minmax(0,1fr)_auto] items-start gap-3 border-b border-line px-4 py-3 text-left transition-colors last:border-b-0",
        selected ? "bg-verdant-soft" : "hover:bg-panel-2",
      )}
    >
      <span className="min-w-0">
        <span className="block truncate text-[13px] font-semibold">{model}</span>
        <span className="mt-1 block break-all font-mono text-[10.5px] text-fg-faint">{job.id}</span>
        <span className="mt-1 block text-[11px] text-fg-muted">{formatJobTime(job.created_at)}</span>
      </span>
      <Badge variant={statusVariant(job.status)} className="mt-0.5 shrink-0">{status}</Badge>
    </button>
  );
}

function EventRow({ event }: { event: CloudJobEvent }) {
  const occurredAt = new Date(event.occurred_at);
  const eventDateTime = Number.isNaN(occurredAt.getTime()) ? undefined : occurredAt.toISOString();
  return (
    <li className="grid grid-cols-[42px_minmax(0,1fr)] gap-x-3 gap-y-1 border-b border-line px-4 py-3 last:border-b-0 sm:grid-cols-[42px_minmax(0,1fr)_auto]">
      <span className="break-all pt-0.5 font-mono text-[10.5px] text-fg-faint">#{event.sequence}</span>
      <span className="min-w-0">
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold uppercase text-fg-muted">{displayLabel(event.phase)}</span>
          {event.percent !== null && <span className="text-[10.5px] text-fg-faint">{event.percent}%</span>}
        </span>
        <span className="mt-1 block break-words text-[12.5px] leading-5 text-fg">{event.message}</span>
      </span>
      <time className="col-start-2 whitespace-nowrap text-[10.5px] text-fg-faint sm:col-start-auto sm:pt-0.5" dateTime={eventDateTime}>
        {formatEventTime(event.occurred_at)}
      </time>
    </li>
  );
}

function mergeEvents(current: CloudJobEvent[], incoming: CloudJobEvent[]): CloudJobEvent[] {
  const rows = new Map(current.map((event) => [event.sequence, event]));
  for (const event of incoming) rows.set(event.sequence, event);
  return [...rows.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-MAX_RETAINED_EVENTS);
}

export function CloudActivity() {
  const product = useAsync(() => api.productState());
  const connected = product.error === null && product.data?.cloud.state === "connected";
  const [jobs, setJobs] = useState<CloudJob[]>([]);
  const [jobsLoaded, setJobsLoaded] = useState(false);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<CloudJobEventsResponse | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const selectedJobIdRef = useRef<string | null>(null);
  const jobsAbortRef = useRef<AbortController | null>(null);
  const eventsAbortRef = useRef<AbortController | null>(null);
  const jobsRequestRef = useRef(0);
  const eventsRequestRef = useRef(0);
  const jobsInFlightRef = useRef<number | null>(null);
  const eventsInFlightRef = useRef<number | null>(null);
  const eventCursorRef = useRef(-1);
  selectedJobIdRef.current = selectedJobId;

  const selectedJob = useMemo(
    () => jobs.find((job) => job.id === selectedJobId) ?? null,
    [jobs, selectedJobId],
  );

  const loadJobs = useCallback(async (silent = false, skipIfBusy = false): Promise<void> => {
    if (!connected) return;
    if (skipIfBusy && jobsInFlightRef.current !== null) return;
    jobsAbortRef.current?.abort();
    const controller = new AbortController();
    jobsAbortRef.current = controller;
    const requestId = ++jobsRequestRef.current;
    jobsInFlightRef.current = requestId;
    if (!silent) setJobsLoading(true);
    try {
      const response = await api.cloudJobs(controller.signal);
      if (controller.signal.aborted || requestId !== jobsRequestRef.current) return;
      setJobs(response.jobs);
      setJobsLoaded(true);
      setJobsError(null);
      const current = selectedJobIdRef.current;
      const next = current && response.jobs.some((job) => job.id === current)
        ? current
        : response.jobs[0]?.id ?? null;
      selectedJobIdRef.current = next;
      setSelectedJobId(next);
    } catch (error) {
      if (controller.signal.aborted || requestId !== jobsRequestRef.current) return;
      setJobsLoaded(true);
      setJobsError(error instanceof Error ? error.message : "Cloud activity unavailable");
    } finally {
      if (requestId === jobsRequestRef.current) {
        jobsAbortRef.current = null;
        jobsInFlightRef.current = null;
        setJobsLoading(false);
      }
    }
  }, [connected]);

  const loadEvents = useCallback(async (
    reset = false,
    silent = false,
    skipIfBusy = false,
  ): Promise<void> => {
    const jobId = selectedJobId;
    if (!connected || !jobId) return;
    if (skipIfBusy && eventsInFlightRef.current !== null) return;
    eventsAbortRef.current?.abort();
    const controller = new AbortController();
    eventsAbortRef.current = controller;
    const requestId = ++eventsRequestRef.current;
    eventsInFlightRef.current = requestId;
    let cursor = reset ? -1 : eventCursorRef.current;
    let replaceEvents = reset;
    if (reset) {
      eventCursorRef.current = -1;
      setSnapshot(null);
      setEventsError(null);
    }
    if (!silent) setEventsLoading(true);
    try {
      for (let page = 0; page < MAX_EVENT_PAGES_PER_LOAD; page += 1) {
        const response = await api.cloudJobEvents(jobId, cursor, controller.signal);
        if (controller.signal.aborted || requestId !== eventsRequestRef.current) return;
        const lastSequence = response.events.at(-1)?.sequence;
        if (lastSequence !== undefined) {
          cursor = lastSequence;
          eventCursorRef.current = lastSequence;
        }
        setSnapshot((current) => ({
          job: response.job,
          events: replaceEvents || current?.job.id !== jobId
            ? response.events
            : mergeEvents(current.events, response.events),
        }));
        replaceEvents = false;
        setEventsError(null);

        if (cursor >= response.job.latest_sequence) break;
        if (lastSequence === undefined) {
          throw new Error("Cloud event history did not advance");
        }
        if (page === MAX_EVENT_PAGES_PER_LOAD - 1) {
          throw new Error("Cloud event history pagination limit reached");
        }
      }
    } catch (error) {
      if (controller.signal.aborted || requestId !== eventsRequestRef.current) return;
      setEventsError(error instanceof Error ? error.message : "Cloud events unavailable");
    } finally {
      if (requestId === eventsRequestRef.current) {
        eventsAbortRef.current = null;
        eventsInFlightRef.current = null;
        setEventsLoading(false);
      }
    }
  }, [connected, selectedJobId]);

  useEffect(() => {
    if (!connected) {
      jobsAbortRef.current?.abort();
      eventsAbortRef.current?.abort();
      jobsRequestRef.current += 1;
      eventsRequestRef.current += 1;
      jobsAbortRef.current = null;
      eventsAbortRef.current = null;
      jobsInFlightRef.current = null;
      eventsInFlightRef.current = null;
      eventCursorRef.current = -1;
      setJobs([]);
      setJobsLoaded(false);
      setJobsLoading(false);
      setJobsError(null);
      selectedJobIdRef.current = null;
      setSelectedJobId(null);
      setSnapshot(null);
      setEventsLoading(false);
      setEventsError(null);
      return;
    }
    void loadJobs();
    return () => jobsAbortRef.current?.abort();
  }, [connected, loadJobs]);

  useEffect(() => {
    eventsAbortRef.current?.abort();
    eventsRequestRef.current += 1;
    eventCursorRef.current = -1;
    setSnapshot(null);
    setEventsError(null);
    if (connected && selectedJobId) void loadEvents(true);
    return () => eventsAbortRef.current?.abort();
  }, [connected, selectedJobId, loadEvents]);

  const hasActiveJobs = jobs.some((job) => isCloudJobActive(job.status));
  const selectedIsActive = selectedJob !== null && isCloudJobActive(selectedJob.status);
  useInterval(
    () => {
      void loadJobs(true, true);
    },
    connected && hasActiveJobs ? JOB_POLL_MS : null,
  );
  useInterval(
    () => {
      void loadEvents(false, true, true);
    },
    connected && selectedJobId && selectedIsActive ? EVENT_POLL_MS : null,
  );

  const refresh = () => {
    void product.reload();
    if (connected) {
      void loadJobs(true);
      if (selectedJobIdRef.current) void loadEvents(true);
    }
  };

  const cancelJob = async (job: CloudJob) => {
    if (!connected || !isCloudJobCancellable(job.status) || cancellingJobId !== null) return;
    setCancellingJobId(job.id);
    try {
      const response = await api.cancelCloudJob(job.id);
      setJobs((current) => current.map((item) => (
        item.id === job.id ? { ...item, status: response.job.status } : item
      )));
      await loadJobs(true);
      if (selectedJobIdRef.current === job.id) await loadEvents(false, true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Cloud job cancellation failed");
    } finally {
      setCancellingJobId(null);
    }
  };

  const cloud = product.data?.cloud;
  let content: ReactNode;
  if (!product.data && product.loading) {
    content = (
      <StatePanel
        icon={<LoaderCircle className="h-5 w-5 animate-spin" />}
        title="Checking Cloud connection"
        detail="Loading account state"
      />
    );
  } else if (product.error || !cloud) {
    content = (
      <StatePanel
        icon={<WifiOff className="h-5 w-5" />}
        title="Cloud state unavailable"
        detail={product.error ?? "Account state could not be loaded"}
        action={<Button variant="secondary" onClick={product.reload}><RefreshCw /> Retry</Button>}
      />
    );
  } else if (cloud.state === "not_configured") {
    content = (
      <StatePanel
        icon={<CloudOff className="h-5 w-5" />}
        title="Cloud is not configured"
        detail="Cloud endpoint configuration is required"
        action={<Button asChild variant="secondary"><Link to="/account">Open Account</Link></Button>}
      />
    );
  } else if (cloud.state === "signed_out") {
    content = (
      <StatePanel
        icon={<CloudOff className="h-5 w-5" />}
        title="Sign in to view Cloud activity"
        detail="No Cloud session is active"
        action={<Button asChild variant="secondary"><Link to="/account">Open Account</Link></Button>}
      />
    );
  } else if (cloud.state === "authorizing") {
    content = (
      <StatePanel
        icon={<LoaderCircle className="h-5 w-5 animate-spin" />}
        title="Cloud sign-in is in progress"
        detail="Complete authorization in the browser"
      />
    );
  } else if (cloud.state === "unreachable") {
    content = (
      <StatePanel
        icon={<WifiOff className="h-5 w-5" />}
        title="Cloud is unreachable"
        detail="The account service did not return a valid response"
        action={<Button variant="secondary" onClick={product.reload}><RefreshCw /> Retry</Button>}
      />
    );
  } else if (!jobsLoaded && jobsLoading) {
    content = (
      <StatePanel
        icon={<LoaderCircle className="h-5 w-5 animate-spin" />}
        title="Loading Cloud activity"
        detail="Fetching job history"
      />
    );
  } else if (jobs.length === 0 && jobsError) {
    content = (
      <StatePanel
        icon={<ShieldAlert className="h-5 w-5" />}
        title="Cloud activity unavailable"
        detail={jobsError}
        action={<Button variant="secondary" onClick={() => void loadJobs()}><RefreshCw /> Retry</Button>}
      />
    );
  } else if (jobs.length === 0) {
    content = (
      <StatePanel
        icon={<Inbox className="h-5 w-5" />}
        title="No Cloud jobs"
        detail="Job history is empty"
      />
    );
  } else {
    content = (
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.2fr)]">
        <section aria-labelledby="cloud-job-history" aria-busy={jobsLoading} className="min-w-0 overflow-hidden rounded border border-line bg-panel">
          <div className="flex min-h-[50px] items-center justify-between gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0">
              <h2 id="cloud-job-history" className="text-[13px] font-semibold">Job history</h2>
              <p className="mt-0.5 text-[11px] text-fg-faint">{jobs.length} recorded</p>
            </div>
            {hasActiveJobs && <Badge variant="info" dot>Live</Badge>}
          </div>
          <div className="max-h-[520px] overflow-y-auto xl:max-h-[calc(100vh-10.5rem)]">
            {jobs.map((job) => (
              <JobRow
                key={job.id}
                job={job}
                selected={job.id === selectedJobId}
                 onSelect={() => {
                   selectedJobIdRef.current = job.id;
                   setSelectedJobId(job.id);
                 }}
              />
            ))}
          </div>
        </section>

        <section aria-labelledby="cloud-job-events" aria-busy={eventsLoading} className="min-w-0 overflow-hidden rounded border border-line bg-panel">
          {selectedJob && (
            <>
              <div className="grid min-h-[64px] grid-cols-[minmax(0,1fr)_auto] items-start gap-3 border-b border-line px-4 py-3">
                <div className="min-w-0">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <h2 id="cloud-job-events" className="text-[13px] font-semibold">{displayLabel(selectedJob.model_alias)}</h2>
                    <Badge variant={statusVariant(selectedJob.status)}>{displayLabel(selectedJob.status)}</Badge>
                    <span className="sr-only" aria-live="polite">Selected job status: {displayLabel(selectedJob.status)}</span>
                  </div>
                  <p className="mt-1 break-all font-mono text-[10.5px] text-fg-faint">{selectedJob.id}</p>
                </div>
                {isCloudJobCancellable(selectedJob.status) && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Cancel cloud job ${selectedJob.id}`}
                    title="Cancel cloud job"
                    aria-busy={cancellingJobId === selectedJob.id}
                    disabled={cancellingJobId !== null}
                    onClick={() => void cancelJob(selectedJob)}
                  >
                    {cancellingJobId === selectedJob.id
                      ? <LoaderCircle className="animate-spin" />
                      : <CircleStop />}
                  </Button>
                )}
              </div>

              <div className="grid grid-cols-2 border-b border-line sm:grid-cols-4">
                {[
                  ["Reserved", selectedJob.reserved_credits.toLocaleString()],
                  ["Actual", selectedJob.actual_credits?.toLocaleString() ?? "Pending"],
                  ["Started", formatJobTime(selectedJob.started_at)],
                  ["Finished", formatJobTime(selectedJob.finished_at)],
                ].map(([label, value], index) => (
                  <div
                    key={label}
                    className={cn(
                      "min-w-0 px-3 py-2.5",
                      index < 2 && "border-b border-line sm:border-b-0",
                      index % 2 === 0 && "border-r border-line",
                      (index === 1 || index === 2) && "sm:border-r sm:border-line",
                    )}
                  >
                    <div className="text-[10px] uppercase text-fg-faint">{label}</div>
                    <div className="mt-1 truncate text-[11.5px] font-medium" title={value}>{value}</div>
                  </div>
                ))}
              </div>

              {snapshot?.job.pending_approval && (
                <div className="border-b border-warning/30 bg-warning-soft px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[11px] font-semibold uppercase text-warning">Pending approval</span>
                    <Badge variant="warning">{displayLabel(snapshot.job.pending_approval.kind)}</Badge>
                  </div>
                  <p className="mt-2 break-words text-[12.5px] leading-5">{snapshot.job.pending_approval.summary}</p>
                  <p className="mt-1 break-all font-mono text-[10.5px] text-fg-faint">{snapshot.job.pending_approval.approval_id}</p>
                </div>
              )}

              {eventsError && (
                <div role="status" className="flex items-center justify-between gap-3 border-b border-danger/30 bg-danger-soft px-4 py-2 text-[12px] text-danger">
                  <span className="min-w-0 break-words">{eventsError}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label="Retry Cloud job events"
                    disabled={eventsLoading}
                    onClick={() => void loadEvents(false)}
                  >
                    Retry
                  </Button>
                </div>
              )}
              <div className="max-h-[520px] overflow-y-auto xl:max-h-[calc(100vh-18rem)]">
                {eventsLoading && !snapshot ? (
                  <div className="flex min-h-[180px] items-center justify-center text-fg-faint">
                    <LoaderCircle className="h-5 w-5 animate-spin" aria-label="Loading job events" />
                  </div>
                ) : snapshot?.events.length ? (
                  <ol aria-label="Cloud job events">
                    {snapshot.events.map((event) => <EventRow key={event.sequence} event={event} />)}
                  </ol>
                ) : (
                  <div className="flex min-h-[180px] flex-col items-center justify-center px-4 text-center">
                    <History className="h-5 w-5 text-fg-faint" />
                    <p className="mt-2 text-[12.5px] text-fg-muted">No recorded events</p>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    );
  }

  return (
    <>
      <PageHeader title="Cloud Activity" subtitle="Hosted job history and control">
        {connected && <Badge variant="success" dot>Connected</Badge>}
        <Button
          variant="ghost"
          size="icon"
          aria-label="Refresh Cloud activity"
          title="Refresh Cloud activity"
          disabled={product.loading || jobsLoading}
          onClick={refresh}
        >
          <RefreshCw className={cn(product.loading || jobsLoading ? "animate-spin" : "")} />
        </Button>
      </PageHeader>
      {jobsError && jobs.length > 0 && (
        <div role="status" className="mb-4 rounded border border-danger/30 bg-danger-soft px-4 py-2.5 text-[12px] text-danger">
          {jobsError}
        </div>
      )}
      {content}
    </>
  );
}
