import { Download as DownloadIcon, Clock } from "lucide-react";
import { PageHeader, EmptyState, ErrorState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync, useInterval } from "@/lib/hooks";
import { api } from "@/lib/api";
import type { DownloadEntry } from "@/lib/types";

const TERMINAL_STATES = new Set(["completed", "failed", "cancelled"]);

export function Downloads() {
  const dl = useAsync(() => api.downloads());
  const pulls = useAsync(() => api.pullStatus());
  useInterval(() => {
    pulls.reload();
    if ((pulls.data?.active ?? 0) > 0) dl.reload();
  }, 2000);

  const activeRows = (pulls.data?.pulls ?? [])
    .filter((r) => !TERMINAL_STATES.has(String(r.state || "").toLowerCase()))
    .map((r) => ({
      ...r,
      status: formatPullStatus(r),
      timestamp: r.updated_at,
    }));
  const rows: DownloadEntry[] = [...activeRows, ...(dl.data ?? []).slice().reverse()];

  return (
    <>
      <PageHeader title="Downloads" subtitle="History of models pulled through LAC." />

      {dl.error ? (
        <ErrorState message={`Couldn't load download history: ${dl.error}`} onRetry={dl.reload} />
      ) : dl.loading ? (
        <Card className="p-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="my-2 h-10 w-full" />
          ))}
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<DownloadIcon className="h-8 w-8" />}
          title="No downloads yet"
          hint="Install a model from Browse or the Dashboard and it'll appear here."
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Model</th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-right font-semibold">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((r, i) => {
                const rawStatus = String(r.status || r.state || "").toLowerCase();
                const ok = rawStatus.includes("ok") || rawStatus === "success" || rawStatus === "completed";
                const bad = rawStatus.includes("error") || rawStatus === "failed";
                return (
                  <tr key={i} className="transition-colors hover:bg-panel-3/40">
                    <td className="px-4 py-2.5 font-mono text-[13px]">{r.model || "-"}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={ok ? "success" : bad ? "danger" : "neutral"} dot>
                        {r.status || r.state || "-"}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 text-right text-[12.5px] text-fg-muted">
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {formatTimestamp(r.timestamp)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function formatPullStatus(r: DownloadEntry) {
  const status = String(r.status || r.state || "running");
  const pct = Number(r.percent ?? 0);
  return pct > 0 && pct < 100 ? `${status} ${pct}%` : status;
}

function formatTimestamp(value: DownloadEntry["timestamp"]) {
  if (value == null || value === "") return "-";
  const numeric = Number(value);
  if (Number.isFinite(numeric)) {
    const ms = numeric < 10_000_000_000 ? numeric * 1000 : numeric;
    return new Date(ms).toLocaleString();
  }
  return new Date(value).toLocaleString();
}
