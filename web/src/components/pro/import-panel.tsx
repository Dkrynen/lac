import { useState } from "react";
import { KeyRound, RotateCcw, Search, Trash2, UploadCloud, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useAsync, useInterval } from "@/lib/hooks";
import { importModelWithToast, normalizeRepoId } from "@/lib/installer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

interface ImportEntry {
  repo_id: string;
  state: string;
  model_name?: string;
  quant?: string;
  error_type?: string;
  message?: string;
  current_file?: string;
  bytes_done?: number;
  bytes_total?: number;
  stage?: string;
  updated_at: number;
}

interface ImportHistoryResponse {
  state: string;
  entries?: ImportEntry[];
}

interface ImportResolveResponse {
  state: string;
  repo_id?: string;
  strategy?: "gguf" | "safetensors";
  selected_file?: string;
  selected_size?: number;
  quant?: string;
  params_b?: number;
  context?: number;
  suggested_gguf_repos?: string[];
  error_type?: string;
  message?: string;
}

const QUANT_OPTIONS = [
  { value: "auto", label: "Auto" },
  { value: "Q4_K_M", label: "Q4_K_M" },
  { value: "Q8", label: "Q8" },
  { value: "F16", label: "F16" },
];

const ACTIVE_STATES = new Set(["starting", "checking", "downloading", "uploading", "converting", "cancel_requested"]);

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

function formatBytes(n?: number): string {
  if (n == null || n < 0) return "";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

export function ImportPanel() {
  const [repoId, setRepoId] = useState("");
  const [quant, setQuant] = useState("auto");
  const [pending, setPending] = useState<ImportEntry[]>([]);
  const [hfToken, setHfToken] = useState("");
  const [savingToken, setSavingToken] = useState(false);
  const [sourceCheck, setSourceCheck] = useState<ImportResolveResponse | null>(null);
  const [checkingSource, setCheckingSource] = useState(false);

  const history = useAsync(() => api.proImportHistory());
  const tokenStatus = useAsync(() => api.hfTokenStatus().catch(() => null));
  const data = history.data as ImportHistoryResponse | null;
  const entries = data?.state === "ok" ? data.entries ?? [] : [];
  const known = new Set(entries.map((entry) => entry.repo_id));
  const displayEntries = [...pending.filter((entry) => !known.has(entry.repo_id)), ...entries];
  const hasActiveImport = displayEntries.some((entry) => ACTIVE_STATES.has(entry.state));

  const normalizedInput = repoId.trim() ? normalizeRepoId(repoId.trim()) : "";
  const checkedRepoId = sourceCheck?.repo_id ? normalizeRepoId(sourceCheck.repo_id) : normalizedInput;
  const sourceCheckMatchesInput = Boolean(sourceCheck && normalizedInput && checkedRepoId === normalizedInput);
  const sourceReady = sourceCheckMatchesInput && sourceCheck?.state === "ok";
  const directGgufReady = sourceReady && sourceCheck?.strategy === "gguf";
  const advancedConvertReady = sourceReady && sourceCheck?.strategy === "safetensors";

  useInterval(history.reload, hasActiveImport ? 3000 : null);

  function handleImport() {
    const id = repoId.trim();
    if (!id) return;
    if (!sourceReady) {
      toast.info("Analyze the source before importing.");
      return;
    }
    const normalized = normalizeRepoId(id);
    setPending((rows) => [
      { repo_id: normalized, state: "starting", updated_at: Date.now() / 1000 },
      ...rows.filter((row) => row.repo_id !== normalized),
    ]);
    importModelWithToast(
      id,
      quant === "auto" ? undefined : quant,
      history.reload,
      () => {
        setPending((rows) => rows.filter((row) => row.repo_id !== normalized));
        history.reload();
      }
    );
    window.setTimeout(history.reload, 800);
    setRepoId("");
    setSourceCheck(null);
  }

  async function checkSource() {
    const id = repoId.trim();
    if (!id) return;
    setCheckingSource(true);
    try {
      const result = await api.resolveImport(id, quant === "auto" ? undefined : quant);
      setSourceCheck(result);
      if (result.state === "not_licensed") {
        toast.info("Activate Pro to check Hugging Face import compatibility.");
      }
    } catch (e) {
      toast.error("Could not analyze import source", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setCheckingSource(false);
    }
  }

  function retryImport(entry: ImportEntry) {
    setPending((rows) => [
      { repo_id: entry.repo_id, state: "starting", updated_at: Date.now() / 1000 },
      ...rows.filter((row) => row.repo_id !== entry.repo_id),
    ]);
    importModelWithToast(entry.repo_id, entry.quant, history.reload, () => {
      setPending((rows) => rows.filter((row) => row.repo_id !== entry.repo_id));
      history.reload();
    });
    window.setTimeout(history.reload, 800);
  }

  async function cancelImport(entry: ImportEntry) {
    try {
      await api.cancelImport(entry.repo_id);
      history.reload();
      toast.info(`Cancel requested for ${entry.repo_id}`);
    } catch (e) {
      toast.error("Could not cancel import", { description: e instanceof Error ? e.message : String(e) });
    }
  }

  async function saveToken() {
    const token = hfToken.trim();
    if (!token) return;
    setSavingToken(true);
    try {
      const result = await api.saveHfToken(token);
      if (result.state === "not_licensed") {
        toast.info("Activate Pro to store a Hugging Face token.");
      } else {
        toast.success("Hugging Face token saved");
        setHfToken("");
        tokenStatus.reload();
      }
    } catch (e) {
      toast.error("Could not save token", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSavingToken(false);
    }
  }

  async function clearToken() {
    setSavingToken(true);
    try {
      await api.clearHfToken();
      toast.success("Hugging Face token removed");
      tokenStatus.reload();
    } catch (e) {
      toast.error("Could not remove token", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSavingToken(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <UploadCloud className="h-4 w-4 text-verdant" /> Import
      </div>

      {tokenStatus.data?.state === "ok" && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded border border-line bg-panel-2/60 p-2.5">
          <KeyRound className="ml-1 h-4 w-4 text-fg-muted" />
          <span className="text-[13px] text-fg-muted">
            HF token {tokenStatus.data.configured ? "saved" : "optional"}
          </span>
          <Input
            type="password"
            placeholder="hf_..."
            value={hfToken}
            onChange={(e) => setHfToken(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && hfToken.trim()) saveToken();
            }}
            className="h-9 min-w-[180px] flex-1"
            autoComplete="off"
            spellCheck={false}
          />
          <Button size="sm" onClick={saveToken} disabled={!hfToken.trim() || savingToken}>
            Save
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9"
            onClick={clearToken}
            disabled={!tokenStatus.data.configured || savingToken}
            aria-label="Remove Hugging Face token"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          placeholder="Hugging Face repo id, e.g. TheBloke/Mistral-7B-GGUF"
          value={repoId}
          onChange={(e) => {
            setRepoId(e.target.value);
            setSourceCheck(null);
          }}
          className="h-9 min-w-[180px] flex-1"
        />
        <Select
          value={quant}
          onValueChange={(value) => {
            setQuant(value);
            setSourceCheck(null);
          }}
        >
          <SelectTrigger className="h-9 w-[130px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {QUANT_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" onClick={checkSource} disabled={!repoId.trim() || checkingSource}>
          <Search className="h-4 w-4" />
          {checkingSource ? "Analyzing" : "Analyze source"}
        </Button>
        <Button size="sm" variant="secondary" onClick={handleImport} disabled={!directGgufReady}>
          Import checked GGUF
        </Button>
      </div>

      {sourceCheck && sourceCheck.state !== "not_licensed" && (
        <div className="mb-4 rounded border border-line bg-panel-2/60 p-3 text-[13px]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 font-medium">
              <Badge variant={directGgufReady ? "success" : advancedConvertReady ? "warning" : "outline"}>
                {sourceCheck.strategy === "gguf"
                  ? "GGUF direct import"
                  : sourceCheck.strategy === "safetensors"
                    ? "Advanced convert"
                    : "Import check"}
              </Badge>
              {sourceCheck.quant && (
                <span className="rounded border border-line px-2 py-0.5 font-mono text-[11px]">
                  {sourceCheck.quant}
                </span>
              )}
            </div>
            {advancedConvertReady && (
              <Button size="sm" variant="secondary" onClick={handleImport}>
                Start advanced convert
              </Button>
            )}
          </div>
          {advancedConvertReady && (
            <p className="mt-2 text-[12px] leading-relaxed text-warning">
              GGUF is the happy path. Conversion is slower, uses scratch disk, and can fail if the architecture is unsupported.
            </p>
          )}
          {sourceCheck.message && <p className="mt-2 text-fg-muted">{sourceCheck.message}</p>}
          {sourceCheck.selected_file && (
            <p className="mt-1 font-mono text-[12px] text-fg-faint">
              {sourceCheck.selected_file}
              {sourceCheck.selected_size ? ` - ${formatBytes(sourceCheck.selected_size)}` : ""}
            </p>
          )}
          {sourceCheck.suggested_gguf_repos && sourceCheck.suggested_gguf_repos.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {sourceCheck.suggested_gguf_repos.map((suggestion) => (
                <Button
                  key={suggestion}
                  variant="ghost"
                  size="sm"
                  className="h-7 font-mono text-[12px]"
                  onClick={() => {
                    setRepoId(suggestion);
                    setSourceCheck(null);
                  }}
                >
                  {suggestion}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}

      {history.loading ? (
        <Skeleton className="h-20 w-full" />
      ) : displayEntries.length === 0 ? (
        <p className="text-[13px] text-fg-muted">No imports yet - paste a Hugging Face repo id above.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Repo</th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-right font-semibold">Action</th>
                <th className="px-4 py-2 text-right font-semibold">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {displayEntries.map((entry, i) => (
                <tr key={`${entry.repo_id}-${i}`} className="transition-colors hover:bg-panel-3/40">
                  <td className="px-4 py-3 font-mono text-[13px] font-medium">{entry.repo_id}</td>
                  <td className="px-4 py-3 text-[13px] text-fg-muted">
                    <div>{entry.state}</div>
                    {entry.current_file && (
                      <div className="text-[11px] text-fg-faint">
                        {entry.current_file}
                        {entry.bytes_total ? ` - ${formatBytes(entry.bytes_done)} / ${formatBytes(entry.bytes_total)}` : ""}
                      </div>
                    )}
                    {(entry.model_name || entry.quant) && (
                      <div className="text-[11px] text-fg-faint">
                        {[entry.model_name, entry.quant].filter(Boolean).join(" - ")}
                      </div>
                    )}
                    {entry.message && <div className="text-[11px] text-danger">{entry.message}</div>}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {ACTIVE_STATES.has(entry.state) ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => cancelImport(entry)}
                        aria-label={`Cancel import of ${entry.repo_id}`}
                      >
                        <XCircle className="h-4 w-4" />
                      </Button>
                    ) : entry.state === "failed" || entry.state === "cancelled" ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        onClick={() => retryImport(entry)}
                        aria-label={`Retry import of ${entry.repo_id}`}
                      >
                        <RotateCcw className="h-4 w-4" />
                      </Button>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-right text-[12px] text-fg-faint">{relativeTime(entry.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
