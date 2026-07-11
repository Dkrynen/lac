import React, { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Gauge, Loader2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAsync, useInterval } from "@/lib/hooks";
import {
  api,
  getProTuneApplyCandidate,
  isProTuneRunningConflict,
  type ProTuneApplyResult as ApplyResult,
  type ProTuneConfigResult as TuneConfigResult,
  type ProTuneStatus as TuneStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** Plain-English label for one sweep config row. */
function rowLabel(r: TuneConfigResult, layers: number | null): string {
  if (r.num_gpu === null) return "auto (Ollama decides)";
  if (layers != null && r.num_gpu === layers) return `${layers} layers · explicit offload`;
  return `${r.num_gpu} layers · partial offload`;
}

/** Per-run tok/s spread as a % of the median (guards against a zero median). */
function spreadPct(r: TuneConfigResult): number {
  if (!r.runs.length || !r.median_tps) return 0;
  const max = Math.max(...r.runs);
  const min = Math.min(...r.runs);
  return Math.round(((max - min) / r.median_tps) * 100);
}

export function TuneHero() {
  const installed = useAsync(() => api.installed());
  const models = installed.data ?? [];

  const [model, setModel] = useState("");
  const [status, setStatus] = useState<TuneStatus>({ state: "idle" });
  const [started, setStarted] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [applyState, setApplyState] = useState<Record<string, ApplyResult | "pending">>({});
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000);
  const applyingSweepRef = useRef<string | null>(null);

  // Default the select to the first installed model once the list arrives.
  useEffect(() => {
    if (!model && models.length > 0) setModel(models[0].name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [models.length]);

  // Warm the selected model off the critical path so the sweep measures its
  // real speed, not the one-time cold load.
  useEffect(() => {
    if (model) api.warm(model);
  }, [model]);

  // Expand the winner row by default whenever a sweep completes.
  useEffect(() => {
    if (status.state === "done") setExpanded(status.winner.label);
  }, [status]);

  const tuneBusy =
    status.state === "running" ||
    (status.state === "done" && status.apply_state === "applying");
  const polling = started && (status.state === "idle" || status.state === "running");

  useInterval(() => {
    if (!model) return;
    api.proTuneStatus(model).then((s: TuneStatus) => setStatus(s));
  }, polling ? 2000 : null);

  const watchingExpiry = status.state === "done" && status.apply_state === "available";
  useInterval(() => setNowSeconds(Date.now() / 1000), watchingExpiry ? 1000 : null);

  function handleModelChange(v: string) {
    setModel(v);
    setStatus({ state: "idle" });
    setStarted(false);
    setExpanded(null);
    setApplyState({});
    applyingSweepRef.current = null;
  }

  async function runSweep() {
    if (!model || tuneBusy) return;
    setExpanded(null);
    setApplyState({});
    applyingSweepRef.current = null;
    setNowSeconds(Date.now() / 1000);
    setStarted(true);
    setStatus({ state: "running" });
    try {
      const res: { accepted?: boolean; state?: string } = await api.proTune(model);
      if (res?.state === "not_licensed") {
        setStarted(false);
        setStatus({ state: "not_licensed" });
        return;
      }
    } catch (e) {
      if (isProTuneRunningConflict(e)) {
        setStatus({ state: "running" });
        return;
      }
      setStarted(false);
      setStatus({ state: "failed", message: e instanceof Error ? e.message : String(e) });
      return;
    }
    // Kick an immediate status read so the UI doesn't sit idle for a full 2s
    // before the interval below picks it up.
    try {
      const s: TuneStatus = await api.proTuneStatus(model);
      setStatus(s);
    } catch {
      /* interval retries */
    }
  }

  async function applyVerifiedCandidate(row: TuneConfigResult) {
    if (status.state !== "done") return;
    const candidate = getProTuneApplyCandidate(status);
    if (row !== candidate) return;
    const sweepId = status.sweep_id;
    if (applyingSweepRef.current === sweepId) return;
    applyingSweepRef.current = sweepId;
    const key = row.label;
    setApplyState((prev) => ({ ...prev, [key]: "pending" }));
    setStatus((current) =>
      current.state === "done" && current.sweep_id === sweepId
        ? { ...current, apply_state: "applying", applied_sweep_id: sweepId }
        : current
    );
    try {
      const res: ApplyResult = await api.proTuneApply(model, status.sweep_id);
      setApplyState((prev) => ({ ...prev, [key]: res }));
      setStatus((current) => {
        if (current.state !== "done" || current.sweep_id !== sweepId) return current;
        if (res.state === "applied") {
          return {
            ...current,
            apply_state: "applied",
            applied_sweep_id: sweepId,
            tuned_model: res.tuned_model,
          };
        }
        if (res.state === "failed") {
          return { ...current, apply_state: "failed", applied_sweep_id: sweepId };
        }
        return { ...current, apply_state: "unavailable", applied_sweep_id: sweepId };
      });
    } catch (e) {
      setApplyState((prev) => ({
        ...prev,
        [key]: { state: "failed", message: e instanceof Error ? e.message : String(e) },
      }));
      setStatus((current) =>
        current.state === "done" && current.sweep_id === sweepId
          ? { ...current, apply_state: "failed", applied_sweep_id: sweepId }
          : current
      );
    }
  }

  const maxTps = status.state === "done" ? Math.max(...status.results.map((r) => r.median_tps), 1) : 1;
  const retainedAutomatic = status.state === "done" && !status.apply_decision.allowed;
  const automaticResult =
    status.state === "done"
      ? status.results.find(
          (result) =>
            result.num_gpu === null &&
            (status.apply_decision.auto_tps == null || result.median_tps === status.apply_decision.auto_tps)
        )
      : undefined;
  const heroTps =
    status.state === "done"
      ? retainedAutomatic
        ? status.apply_decision.auto_tps ?? automaticResult?.median_tps ?? null
        : status.winner.median_tps
      : null;
  const hasDelta =
    status.state === "done" &&
    status.apply_decision.allowed &&
    status.baseline_tps != null &&
    status.baseline_tps > 0;
  const deltaPct =
    status.state === "done" && hasDelta
      ? Math.round(((status.winner.median_tps - (status.baseline_tps as number)) / (status.baseline_tps as number)) * 100)
      : null;
  const verifiedCandidate =
    status.state === "done" ? getProTuneApplyCandidate(status, nowSeconds) : undefined;

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Gauge className="h-4 w-4 text-verdant" /> Tune
        </div>
        {installed.loading ? (
          <Skeleton className="h-9 w-64" />
        ) : models.length > 0 ? (
          <div className="flex items-center gap-2">
            <Select value={model} onValueChange={handleModelChange} disabled={tuneBusy}>
              <SelectTrigger className="h-9 w-[220px]">
                <SelectValue placeholder="Choose a model" />
              </SelectTrigger>
              <SelectContent>
                {models.map((m) => (
                  <SelectItem key={m.name} value={m.name}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button size="sm" onClick={runSweep} disabled={!model || tuneBusy}>
              {tuneBusy ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {status.state === "running" ? "Running…" : "Applying…"}
                </>
              ) : (
                "Run sweep"
              )}
            </Button>
          </div>
        ) : null}
      </div>

      {installed.loading ? (
        <Skeleton className="h-20 w-full" />
      ) : models.length === 0 ? (
        <p className="text-[13px] text-fg-muted">Install a model first, then come back here to tune it.</p>
      ) : status.state === "idle" ? (
        <p className="text-[13px] text-fg-muted">
          Pick a model and run a sweep to benchmark GPU-offload configs on your exact hardware.
        </p>
      ) : status.state === "not_licensed" ? (
        <p className="text-[13px] text-fg-muted">LAC Pro license required to tune models.</p>
      ) : status.state === "running" ? (
        <div className="flex items-center gap-2 text-[13px] text-fg-muted">
          <Loader2 className="h-4 w-4 animate-spin text-verdant" />
          Benchmarking offload configs on your hardware…
        </div>
      ) : status.state === "failed" ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-danger-soft bg-danger-soft p-3">
          <span className="text-[13px] text-danger">{status.message}</span>
          <Button size="sm" variant="secondary" onClick={runSweep}>
            Retry
          </Button>
        </div>
      ) : (
        <>
          {/* Verified candidate or retained automatic result. */}
          <div className="mb-5 rounded-lg border border-line bg-panel-2 p-4">
            <div className="flex flex-wrap items-baseline gap-3">
              {hasDelta && (
                <>
                  <span className="font-mono text-lg text-fg-muted">{(status.baseline_tps as number).toFixed(1)}</span>
                  <span className="text-fg-faint">→</span>
                </>
              )}
              <span className="font-mono text-2xl font-semibold text-verdant">
                {heroTps == null ? "Not measured" : heroTps.toFixed(1)}
              </span>
              {heroTps != null && <span className="text-[13px] text-fg-muted">tok/s</span>}
              {deltaPct != null && (
                <Badge variant="accent">
                  {deltaPct >= 0 ? "+" : ""}
                  {deltaPct}%
                </Badge>
              )}
            </div>
            <div className="mt-1.5 text-[12px] text-fg-muted">
              {retainedAutomatic ? (
                <>
                  Retained: <span className="text-fg">Ollama automatic</span>
                </>
              ) : (
                <>
                  {status.apply_state === "applied" ? "Applied candidate" : "Verified candidate"}: {" "}
                  <span className="text-fg">{rowLabel(status.winner, status.layers)}</span>
                </>
              )}
            </div>
          </div>

          {!status.apply_decision.allowed && (
            <div className="mb-5 rounded-lg border border-line bg-panel-2 p-3">
              <div className="text-[13px] font-medium text-fg">Ollama automatic remains selected.</div>
              <div className="mt-1 text-[12px] text-fg-muted">{status.apply_decision.reason}</div>
            </div>
          )}

          {status.apply_decision.allowed && status.apply_state === "applying" && (
            <div className="mb-5 flex items-center gap-2 rounded-lg border border-line bg-panel-2 p-3 text-[12px] text-fg-muted">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-verdant" />
              Applying the verified candidate…
            </div>
          )}

          {status.apply_decision.allowed &&
            (status.apply_state === "expired" ||
              status.apply_state === "stale" ||
              status.apply_state === "unavailable" ||
              (status.apply_state === "available" && nowSeconds >= status.expires_at) ||
              (status.apply_state === "available" && status.applied_sweep_id === status.sweep_id)) && (
              <div className="mb-5 rounded-lg border border-line bg-panel-2 p-3 text-[12px] text-fg-muted">
                This sweep is no longer available to apply. Run a new sweep for a fresh verified candidate.
              </div>
            )}

          {/* Per-config table */}
          <div className="overflow-hidden rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
                <tr>
                  <th className="px-4 py-2 text-left font-semibold">Config</th>
                  <th className="px-4 py-2 text-left font-semibold">Throughput</th>
                  <th className="px-4 py-2 text-right font-semibold">tok/s</th>
                  <th className="px-4 py-2 text-right font-semibold"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {status.results.map((r) => {
                  const isFastest = r.label === status.winner.label && r.num_gpu === status.winner.num_gpu;
                  const isDecisionCandidate =
                    status.apply_decision.allowed &&
                    r.num_gpu === status.apply_decision.num_gpu &&
                    r.median_tps === status.apply_decision.candidate_tps;
                  const isVerifiedCandidate = r === verifiedCandidate;
                  const isRetainedAutomatic = retainedAutomatic && r === automaticResult;
                  const isHighlighted =
                    isRetainedAutomatic ||
                    isVerifiedCandidate ||
                    (isDecisionCandidate && status.apply_state === "applied");
                  const isOpen = expanded === r.label;
                  const pct = Math.max(0, Math.min(100, Math.round((r.median_tps / maxTps) * 100)));
                  const outcome = applyState[r.label];
                  return (
                    <React.Fragment key={r.label}>
                      <tr className={cn("transition-colors", isHighlighted ? "bg-verdant-soft" : "hover:bg-panel-3/40")}>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            <button
                              aria-label={isOpen ? "Hide detail" : "Show detail"}
                              onClick={() => setExpanded(isOpen ? null : r.label)}
                              className="text-fg-faint hover:text-fg"
                            >
                              {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                            </button>
                            <span className={cn("text-[13px] font-medium", isHighlighted ? "text-verdant" : "text-fg")}>
                              {rowLabel(r, status.layers)}
                            </span>
                            {isRetainedAutomatic && <Badge variant="accent">retained</Badge>}
                            {isFastest && <Badge variant="neutral">fastest measured</Badge>}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="h-1.5 w-full max-w-[180px] overflow-hidden rounded-pill bg-panel-3">
                            <div
                              className={cn("h-full rounded-pill", isHighlighted ? "bg-verdant" : "bg-fg-muted")}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-[13px] text-fg-muted">
                          {r.median_tps.toFixed(1)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {isVerifiedCandidate ? (
                            <div className="flex flex-col items-end gap-1">
                              <Button
                                size="sm"
                                variant="primary"
                                disabled={outcome === "pending"}
                                onClick={() => applyVerifiedCandidate(r)}
                              >
                                {outcome === "pending" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Apply"}
                              </Button>
                              {outcome && outcome !== "pending" && outcome.state === "applied" && (
                                <span className="max-w-[220px] text-right text-[11px] text-success">
                                  Created <code className="font-mono">{outcome.tuned_model}</code> — pick it in Chat/Installed
                                </span>
                              )}
                              {outcome && outcome !== "pending" && outcome.state === "failed" && (
                                <span className="max-w-[220px] text-right text-[11px] text-danger">{outcome.message}</span>
                              )}
                              {outcome && outcome !== "pending" && outcome.state === "not_licensed" && (
                                <span className="max-w-[220px] text-right text-[11px] text-danger">
                                  LAC Pro license required.
                                </span>
                              )}
                            </div>
                          ) : isDecisionCandidate && status.apply_state === "applying" ? (
                            <span className="inline-flex items-center gap-1 text-[11px] text-fg-faint">
                              <Loader2 className="h-3 w-3 animate-spin" /> Applying…
                            </span>
                          ) : isDecisionCandidate && status.apply_state === "applied" ? (
                            <span className="max-w-[220px] text-right text-[11px] text-success">
                              Created <code className="font-mono">{status.tuned_model ?? "tuned model"}</code>
                            </span>
                          ) : isDecisionCandidate && status.apply_state === "failed" ? (
                            <span className="max-w-[220px] text-right text-[11px] text-danger">
                              {outcome && outcome !== "pending" && outcome.state === "failed"
                                ? outcome.message
                                : "Apply failed; run a new sweep to retry safely."}
                            </span>
                          ) : isDecisionCandidate && outcome && outcome !== "pending" && outcome.state === "not_licensed" ? (
                            <span className="max-w-[220px] text-right text-[11px] text-danger">
                              LAC Pro license required.
                            </span>
                          ) : r.num_gpu === null ? (
                            <span className="text-[11px] text-fg-faint">Ollama automatic</span>
                          ) : (
                            <span className="text-[11px] text-fg-faint">Measured only</span>
                          )}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-panel-2/60">
                          <td colSpan={4} className="px-4 py-3">
                            <div className="flex flex-wrap items-center gap-4 text-[12px] text-fg-muted">
                              <span>
                                num_gpu: <span className="font-mono text-fg">{r.num_gpu ?? "auto"}</span>
                              </span>
                              <span>
                                runs:{" "}
                                <span className="font-mono text-fg">{r.runs.map((v) => v.toFixed(1)).join(" · ")}</span>
                              </span>
                              <span>
                                spread: <span className="font-mono text-fg">{spreadPct(r)}%</span>
                              </span>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
