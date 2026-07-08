import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Download, ExternalLink, Loader2, Search, SlidersHorizontal, Plus } from "lucide-react";
import { PageHeader, ErrorState, EmptyState } from "@/components/page";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ModelCard } from "@/components/model-card";
import { Skeleton } from "@/components/ui/skeleton";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { pullWithToast, importModelWithToast } from "@/lib/installer";
import { FitBar, VerdictBadge, type Verdict } from "@/components/verdict";
import { fmtBytes } from "@/lib/utils";
import type { HfGgufFile, HfGgufModel, InstallPreflightResponse } from "@/lib/types";

const CAPS = [
  { v: "", l: "All capabilities" },
  { v: "vision", l: "Vision" },
  { v: "tools", l: "Tools" },
  { v: "thinking", l: "Thinking" },
  { v: "embedding", l: "Embedding" },
];
const SORTS = [
  { v: "pulls", l: "Popular" },
  { v: "name", l: "Name" },
  { v: "vram", l: "VRAM (low)" },
  { v: "params", l: "Params (high)" },
];

function asVerdict(fit: string | undefined): Verdict {
  if (fit === "fits" || fit === "offload" || fit === "too_large") return fit;
  return "unknown";
}

function hfFileOptions(model: HfGgufModel): HfGgufFile[] {
  return (model.files ?? []).filter((file) => file.selection || file.filename);
}

function selectedHfFile(model: HfGgufModel, selectedValue: string | undefined): HfGgufFile | undefined {
  const files = hfFileOptions(model);
  return (
    files.find((file) => (file.selection ?? file.filename) === selectedValue || file.filename === selectedValue) ??
    files.find((file) => file.filename === model.recommended_file) ??
    files[0]
  );
}

function hfFileLabel(model: HfGgufModel, file: HfGgufFile): string {
  const quant = file.quant ?? "GGUF";
  const size = file.size_gb ? ` - ${fmtBytes(file.size_gb)}` : "";
  const duplicateQuant = (model.files ?? []).filter((f) => f.quant === file.quant).length > 1;
  const compatibility = file.compatibility_note ? " - not supported" : "";
  return duplicateQuant ? `${quant}${size} - ${file.filename}${compatibility}` : `${quant}${size}${compatibility}`;
}

export function Browse() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [capability, setCapability] = useState("");
  const [sort, setSort] = useState("pulls");
  const [compatible, setCompatible] = useState(false);
  const [limit, setLimit] = useState(36);
  const [newModel, setNewModel] = useState("");
  const [manualPreflight, setManualPreflight] = useState<InstallPreflightResponse | null>(null);
  const [manualPreflightTarget, setManualPreflightTarget] = useState("");
  const [manualPreflightLoading, setManualPreflightLoading] = useState(false);
  const [manualPreflightError, setManualPreflightError] = useState<string | null>(null);
  const [hfRepoId, setHfRepoId] = useState("");
  const [hfSelectionByRepo, setHfSelectionByRepo] = useState<Record<string, string>>({});

  const lib = useAsync(
    () => api.library({ q, capability, sort, compatible: compatible ? "gpu" : "" }),
    [q, capability, sort, compatible]
  );
  const hfQuery = q.trim();
  const hf = useAsync(
    () => hfQuery.length >= 2 ? api.hfGgufSearch(hfQuery, 12) : Promise.resolve({ query: hfQuery, total: 0, models: [] }),
    [hfQuery]
  );

  const totalVram = lib.data?.system_vram ?? undefined;
  const models = (lib.data?.models ?? []).slice(0, limit);
  const manualTarget = newModel.trim();
  const manualPreflightFresh = manualPreflight && manualPreflightTarget === manualTarget ? manualPreflight : null;
  const manualInstallBlocked = manualPreflightFresh?.state === "blocked";

  async function checkManualInstall(value = newModel): Promise<InstallPreflightResponse | null> {
    const target = value.trim();
    if (!target) return null;
    setManualPreflightLoading(true);
    setManualPreflightError(null);
    try {
      const preflight = await api.installPreflight(target);
      setManualPreflight(preflight);
      setManualPreflightTarget(target);
      return preflight;
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not check install target";
      setManualPreflight(null);
      setManualPreflightTarget("");
      setManualPreflightError(message);
      return null;
    } finally {
      setManualPreflightLoading(false);
    }
  }

  async function installManual(value = newModel) {
    const target = value.trim();
    if (!target) return;
    const preflight = manualPreflightFresh ?? await checkManualInstall(target);
    if (!preflight || preflight.state === "blocked") return;
    if (preflight.action === "import") {
      importModelWithToast(
        preflight.repo_id ?? preflight.normalized,
        preflight.selected_quant,
        lib.reload,
        undefined,
        preflight.selected_file
      );
    } else {
      pullWithToast(preflight.model_ref ?? preflight.normalized, lib.reload);
    }
    setNewModel("");
    setManualPreflight(null);
    setManualPreflightTarget("");
    setManualPreflightError(null);
  }

  return (
    <>
      <PageHeader title="Browse models" subtitle="The full Ollama library — filtered to your hardware." />

      <Card className="mb-4 flex flex-wrap items-center gap-2 p-2.5">
        <form
          className="relative min-w-[200px] flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(q.trim());
            setParams(q.trim() ? { q: q.trim() } : {});
            setLimit(36);
          }}
        >
          <Input
            placeholder="Search by name or description…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="pr-16"
          />
          <Button type="submit" size="sm" className="absolute right-1 top-1 h-7">
            Search
          </Button>
        </form>

        <Select
          value={capability || "all"}
          onValueChange={(v) => {
            setCapability(v === "all" ? "" : v);
            setLimit(36);
          }}
        >
          <SelectTrigger className="h-9 w-[170px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CAPS.map((c) => (
              <SelectItem key={c.v || "all"} value={c.v || "all"}>
                {c.l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className="h-9 w-[150px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SORTS.map((s) => (
              <SelectItem key={s.v} value={s.v}>
                {s.l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded border border-line bg-panel-2 px-3 text-[13px] text-fg-muted">
          <SlidersHorizontal className="h-3.5 w-3.5" />
          Fits my GPU
          <Switch checked={compatible} onCheckedChange={setCompatible} />
        </label>
      </Card>

      {/* Pull any model:tag from the Ollama registry — full availability */}
      <Card className="mb-4 p-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Plus className="ml-1 h-4 w-4 text-fg-muted" />
          <span className="text-[13px] text-fg-muted">Install model ref</span>
          <Input
            placeholder="llama3.2:3b or hf.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M"
            value={newModel}
            onChange={(e) => {
              setNewModel(e.target.value);
              setManualPreflightError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && manualTarget) {
                void installManual(newModel);
              }
            }}
            className="h-9 min-w-[260px] max-w-[520px] flex-1"
          />
          <Button
            size="sm"
            variant="secondary"
            disabled={!manualTarget || manualPreflightLoading}
            onClick={() => {
              void checkManualInstall(newModel);
            }}
          >
            {manualPreflightLoading ? <Loader2 className="animate-spin" /> : <Search />}
            Check
          </Button>
          <Button
            size="sm"
            disabled={!manualTarget || manualPreflightLoading || manualInstallBlocked}
            onClick={() => {
              void installManual(newModel);
            }}
          >
            <Download />
            {manualPreflightFresh?.action === "import" ? "Import" : manualPreflightFresh?.action === "pull" ? "Pull" : "Install"}
          </Button>
        </div>
        {manualPreflightFresh ? <InstallPreflightStrip preflight={manualPreflightFresh} /> : null}
        {manualPreflightError ? <div className="mt-2 text-[12px] text-danger">{manualPreflightError}</div> : null}
      </Card>

      {/* LAC Pro: download, convert, and install a custom model straight from a HF repo ID */}
      <Card className="p-4 mb-4">
        <h3 className="text-sm font-semibold mb-2">Import from Hugging Face</h3>
        <p className="text-xs text-fg-muted mb-3">
          LAC Pro can install compatible GGUF repos directly, or convert supported safetensors repos into Ollama models.
        </p>
        <div className="flex gap-2">
          <Input
            placeholder="e.g. deepreinforce-ai/Ornith-1.0-9B"
            value={hfRepoId}
            onChange={(e) => setHfRepoId(e.target.value)}
          />
          <Button
            onClick={() => {
              if (!hfRepoId.trim()) return;
              importModelWithToast(hfRepoId.trim(), undefined, () => setHfRepoId(""));
            }}
          >
            Import
          </Button>
        </div>
      </Card>

      {hfQuery.length >= 2 && (
        <section className="mb-4">
          <div className="mb-2 flex items-center gap-2">
            <h3 className="text-sm font-semibold">Hugging Face GGUF</h3>
            <Badge variant="neutral">{hf.data?.total ?? 0} repos</Badge>
          </div>
          {hf.error || hf.data?.error ? (
            <ErrorState message={`Could not search Hugging Face: ${hf.error ?? hf.data?.error}`} onRetry={hf.reload} />
          ) : hf.loading ? (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Card key={i} className="h-[150px] p-4">
                  <Skeleton className="h-4 w-44" />
                  <Skeleton className="mt-3 h-3 w-full" />
                  <Skeleton className="mt-2 h-3 w-2/3" />
                </Card>
              ))}
            </div>
          ) : (hf.data?.models.length ?? 0) === 0 ? (
            <Card className="p-4 text-[13px] text-fg-muted">
              No importable GGUF repos found on Hugging Face for "{hfQuery}".
            </Card>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {hf.data?.models.map((m) => {
                const selectedValue = hfSelectionByRepo[m.repo_id] ?? m.recommended_file ?? m.recommended_quant;
                const selectedFile = selectedHfFile(m, selectedValue);
                const options = hfFileOptions(m);
                const verdict = asVerdict(selectedFile?.fit ?? m.fit);
                const selectedSize = selectedFile?.size_gb ?? m.recommended_size_gb;
                const selectedVram = selectedFile?.vram_gb ?? m.vram_gb;
                const selectedKey = selectedFile?.selection ?? selectedFile?.filename ?? selectedFile?.quant;
                const selectedPreflight = selectedFile?.preflight ?? m.preflight;
                return (
                  <Card key={m.repo_id} className="flex min-h-[246px] flex-col justify-between p-4">
                    <div>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 break-all font-mono text-sm font-semibold text-fg">{m.repo_id}</div>
                        <VerdictBadge verdict={verdict} className="shrink-0" />
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {m.gated && <Badge variant="warning">gated</Badge>}
                        {m.license && <Badge variant="outline">{m.license}</Badge>}
                        <Badge variant="neutral">{m.gguf_files} GGUF</Badge>
                        <Badge variant="neutral">{Number(m.downloads ?? 0).toLocaleString()} downloads</Badge>
                      </div>
                      {m.base_model ? (
                        <div className="mt-2 truncate text-[12px] text-fg-muted" title={m.base_model}>
                          base: <span className="font-mono">{m.base_model}</span>
                        </div>
                      ) : null}
                      <div className="mt-3 grid grid-cols-[1fr_auto] items-end gap-2">
                        <div>
                          <div className="mb-1 text-[10px] uppercase tracking-[0.06em] text-fg-faint">Quant</div>
                          {options.length ? (
                            <Select
                              value={selectedKey}
                              onValueChange={(value) => setHfSelectionByRepo((prev) => ({ ...prev, [m.repo_id]: value }))}
                            >
                              <SelectTrigger className="h-8">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {options.map((file) => (
                                  <SelectItem
                                    key={`${m.repo_id}-${file.filename}`}
                                    value={file.selection ?? file.filename}
                                    disabled={!file.importable}
                                  >
                                    {hfFileLabel(m, file)}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          ) : (
                            <div className="flex h-8 items-center rounded border border-line bg-panel-2 px-2 text-[13px] text-fg-muted">
                              auto
                            </div>
                          )}
                        </div>
                        {selectedFile?.filename === m.recommended_file ? <Badge variant="success">recommended</Badge> : null}
                      </div>
                      <div className="mt-3 grid grid-cols-3 gap-3">
                        <HfMetric label="Size" value={fmtBytes(selectedSize)} />
                        <HfMetric label="VRAM" value={selectedVram ? `${selectedVram.toFixed(1)} GB` : "unknown"} />
                        <HfMetric label="Likes" value={Number(m.likes ?? 0).toLocaleString()} />
                      </div>
                      {selectedVram && (hf.data?.system_vram ?? 0) > 0 ? (
                        <div className="mt-3">
                          <FitBar req={selectedVram} total={hf.data?.system_vram ?? 0} verdict={verdict} />
                          <div className="mt-1 text-[11px] text-fg-faint">
                            {Math.round((selectedVram / (hf.data?.system_vram ?? 1)) * 100)}% of {hf.data?.system_vram} GB VRAM
                          </div>
                        </div>
                      ) : null}
                      {selectedPreflight ? (
                        <div className="mt-3 rounded border border-line bg-panel-2 p-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge
                              variant={
                                selectedPreflight.state === "ok"
                                  ? "success"
                                  : selectedPreflight.state === "blocked"
                                    ? "danger"
                                    : "warning"
                              }
                            >
                              {selectedPreflight.state === "ok"
                                ? "disk ok"
                                : selectedPreflight.state === "blocked"
                                  ? "space blocked"
                                  : "disk unknown"}
                            </Badge>
                            <span className="text-[11px] text-fg-faint">
                              stage {fmtBytes(selectedPreflight.scratch.free_gb)} free
                            </span>
                            <span className="text-[11px] text-fg-faint">
                              store {fmtBytes(selectedPreflight.model_store.free_gb)} free
                            </span>
                          </div>
                          {selectedPreflight.warnings.length > 0 ? (
                            <div className="mt-1 text-[11px] text-danger">
                              {selectedPreflight.warnings[0]}
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      {selectedFile?.filename ? (
                        <div className="mt-2 truncate font-mono text-[11px] text-fg-faint" title={selectedFile.filename}>
                          {selectedFile.filename}
                        </div>
                      ) : null}
                    </div>
                    <div className="mt-4 flex gap-2">
                      <Button
                        size="sm"
                        onClick={() => importModelWithToast(
                          m.repo_id,
                          selectedFile?.quant ?? m.recommended_quant,
                          lib.reload,
                          undefined,
                          selectedFile?.filename
                        )}
                        disabled={(options.length > 0 && !selectedFile?.importable) || selectedPreflight?.state === "blocked"}
                      >
                        Import {selectedFile?.quant ?? ""}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => window.open(`https://huggingface.co/${m.repo_id}`, "_blank")}>
                        <ExternalLink /> Open
                      </Button>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </section>
      )}

      {lib.error ? (
        <ErrorState message={`Couldn’t load library: ${lib.error}`} onRetry={lib.reload} />
      ) : lib.loading ? (
        <GridSkeleton />
      ) : models.length === 0 ? (
        <EmptyState title="No models match" hint="Try a different search or loosen the filters." />
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2 text-[12px] text-fg-faint">
            <Badge variant="neutral">{lib.data?.total ?? 0} models</Badge>
            {totalVram ? <span>· filtering against {totalVram} GB VRAM</span> : null}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {models.map((m) => (
              <ModelCard
                key={m.name}
                model={{
                  name: m.name,
                  description: m.description,
                  params_b: m.params_b,
                  vram_gb: m.vram_q4,
                  capabilities: (m.capabilities ?? []).slice(0, 4),
                }}
                vramLabel="VRAM Q4"
                totalVram={totalVram}
                onPrimary={() => pullWithToast(m.name, lib.reload)}
              />
            ))}
          </div>
          {(lib.data?.models?.length ?? 0) > limit && (
            <div className="mt-5 flex justify-center">
              <Button variant="secondary" onClick={() => setLimit((l) => l + 36)}>
                Show more
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );
}

function HfMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">{label}</div>
      <div className="truncate font-mono text-[12.5px] font-medium text-fg">{value}</div>
    </div>
  );
}

function InstallPreflightStrip({ preflight }: { preflight: InstallPreflightResponse }) {
  const disk = preflight.preflight;
  const stateVariant =
    preflight.state === "ok"
      ? "success"
      : preflight.state === "blocked" || preflight.state === "error"
        ? "danger"
        : "warning";
  const actionLabel = preflight.action === "import" ? "HF import" : "Ollama pull";
  const selectedLabel =
    preflight.selected_file ??
    preflight.selected_quant ??
    preflight.model_ref ??
    preflight.repo_id ??
    preflight.normalized;

  return (
    <div className="mt-2 rounded border border-line bg-panel-2 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="neutral">{actionLabel}</Badge>
        <Badge variant={stateVariant}>
          {preflight.state === "ok"
            ? "ready"
            : preflight.state === "blocked"
              ? "blocked"
              : preflight.state === "error"
                ? "check failed"
                : "needs review"}
        </Badge>
        {preflight.selected_size_gb ? <Badge variant="outline">{fmtBytes(preflight.selected_size_gb)}</Badge> : null}
        <span className="min-w-0 truncate font-mono text-[11.5px] text-fg-muted" title={selectedLabel}>
          {selectedLabel}
        </span>
      </div>
      <div className="mt-1 text-[12px] text-fg-muted">{preflight.message}</div>
      {disk ? (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-fg-faint">
          <span>stage {fmtBytes(disk.scratch.free_gb)} free</span>
          <span>store {fmtBytes(disk.model_store.free_gb)} free</span>
          {disk.selected_size_gb ? <span>needs {fmtBytes(disk.selected_size_gb)} stage</span> : null}
        </div>
      ) : preflight.model_store ? (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-fg-faint">
          <span>store {fmtBytes(preflight.model_store.free_gb)} free</span>
          {preflight.model_store_dir ? <span className="truncate font-mono">{preflight.model_store_dir}</span> : null}
        </div>
      ) : null}
      {preflight.warnings.length > 0 ? (
        <div className="mt-1 text-[11px] text-danger">{preflight.warnings[0]}</div>
      ) : null}
    </div>
  );
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 9 }).map((_, i) => (
        <Card key={i} className="h-[210px] p-4">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="mt-2 h-3 w-full" />
          <Skeleton className="mt-1.5 h-3 w-2/3" />
          <Skeleton className="mt-5 h-1.5 w-full" />
        </Card>
      ))}
    </div>
  );
}
