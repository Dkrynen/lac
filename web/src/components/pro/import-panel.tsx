import { useState } from "react";
import { UploadCloud } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { importModelWithToast } from "@/lib/installer";

interface ImportEntry {
  repo_id: string;
  state: string;
  model_name?: string;
  quant?: string;
  error_type?: string;
  message?: string;
  updated_at: number;
}

interface ImportHistoryResponse {
  state: string;
  entries?: ImportEntry[];
}

const QUANT_OPTIONS = ["auto", "q4_K_M", "q8_0", "F16"];

/** Compact "Nh ago" / "Nd ago" from a Unix epoch-seconds timestamp. */
function relativeTime(epochSeconds: number): string {
  const diffMs = Date.now() - epochSeconds * 1000;
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

export function ImportPanel() {
  const [repoId, setRepoId] = useState("");
  const [quant, setQuant] = useState("auto");
  const history = useAsync(() => api.proImportHistory());
  const data = history.data as ImportHistoryResponse | null;
  const entries = data?.state === "ok" ? data.entries ?? [] : [];

  function handleImport() {
    const id = repoId.trim();
    if (!id) return;
    importModelWithToast(id, quant === "auto" ? undefined : quant, history.reload);
    setRepoId("");
  }

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <UploadCloud className="h-4 w-4 text-verdant" /> Import
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          placeholder="Hugging Face repo id, e.g. TheBloke/Mistral-7B-GGUF"
          value={repoId}
          onChange={(e) => setRepoId(e.target.value)}
          className="h-9 min-w-[180px] flex-1"
        />
        <Select value={quant} onValueChange={setQuant}>
          <SelectTrigger className="h-9 w-[130px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {QUANT_OPTIONS.map((q) => (
              <SelectItem key={q} value={q}>
                {q}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" onClick={handleImport} disabled={!repoId.trim()}>
          Import
        </Button>
      </div>

      {history.loading ? (
        <Skeleton className="h-20 w-full" />
      ) : entries.length === 0 ? (
        <p className="text-[13px] text-fg-muted">No imports yet — paste a Hugging Face repo id above.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Repo</th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-right font-semibold">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {entries.map((e, i) => (
                <tr key={`${e.repo_id}-${i}`} className="transition-colors hover:bg-panel-3/40">
                  <td className="px-4 py-3 font-mono text-[13px] font-medium">{e.repo_id}</td>
                  <td className="px-4 py-3 text-[13px] text-fg-muted">
                    <div>{e.state}</div>
                    {(e.model_name || e.quant) && (
                      <div className="text-[11px] text-fg-faint">
                        {[e.model_name, e.quant].filter(Boolean).join(" · ")}
                      </div>
                    )}
                    {e.message && <div className="text-[11px] text-danger">{e.message}</div>}
                  </td>
                  <td className="px-4 py-3 text-right text-[12px] text-fg-faint">{relativeTime(e.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
