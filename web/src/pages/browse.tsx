import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ExternalLink, SlidersHorizontal, Plus } from "lucide-react";
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

function isHuggingFacePageUrl(value: string): boolean {
  return /^https?:\/\/(www\.)?huggingface\.co\//i.test(value.trim());
}

export function Browse() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [capability, setCapability] = useState("");
  const [sort, setSort] = useState("pulls");
  const [compatible, setCompatible] = useState(false);
  const [limit, setLimit] = useState(36);
  const [newModel, setNewModel] = useState("");
  const [hfRepoId, setHfRepoId] = useState("");

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

  function pullOrImport(value: string) {
    const target = value.trim();
    if (!target) return;
    if (isHuggingFacePageUrl(target)) {
      importModelWithToast(target, undefined, lib.reload);
    } else {
      pullWithToast(target, lib.reload);
    }
    setNewModel("");
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
      <Card className="mb-4 flex flex-wrap items-center gap-2 p-2.5">
        <Plus className="ml-1 h-4 w-4 text-fg-muted" />
        <span className="text-[13px] text-fg-muted">Pull Ollama/GGUF ref</span>
        <Input
          placeholder="llama3.2:3b or hf.co/bartowski/Qwen3-30B-A3B:Q4_K_M"
          value={newModel}
          onChange={(e) => setNewModel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newModel.trim()) {
              pullOrImport(newModel);
            }
          }}
          className="h-9 max-w-[420px] flex-1"
        />
        <Button
          size="sm"
          disabled={!newModel.trim()}
          onClick={() => {
            pullOrImport(newModel);
          }}
        >
          Pull
        </Button>
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
              {hf.data?.models.map((m) => (
                <Card key={m.repo_id} className="flex min-h-[150px] flex-col justify-between p-4">
                  <div>
                    <div className="break-all font-mono text-sm font-semibold text-fg">{m.repo_id}</div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {m.gated && <Badge variant="warning">gated</Badge>}
                      <Badge variant="neutral">{m.gguf_files} GGUF</Badge>
                      <Badge variant="neutral">{Number(m.downloads ?? 0).toLocaleString()} downloads</Badge>
                    </div>
                    {m.quants.length ? (
                      <div className="mt-3 flex flex-wrap gap-1">
                        {m.quants.slice(0, 6).map((q) => (
                          <span key={q} className="rounded border border-line bg-panel-2 px-1.5 py-0.5 text-[11px] text-fg-muted">
                            {q}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div className="mt-4 flex gap-2">
                    <Button size="sm" onClick={() => importModelWithToast(m.repo_id, undefined, lib.reload)}>
                      Import
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => window.open(`https://huggingface.co/${m.repo_id}`, "_blank")}>
                      <ExternalLink /> Open
                    </Button>
                  </div>
                </Card>
              ))}
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
