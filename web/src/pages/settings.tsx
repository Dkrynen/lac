import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";
import { toast } from "sonner";
import {
  Activity,
  Check,
  CircleDot,
  Copy,
  Download,
  Github,
  HardDrive,
  Info,
  Monitor,
  Moon,
  Palette,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Terminal,
} from "lucide-react";
import { PageHeader, ErrorState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync, useInterval } from "@/lib/hooks";
import { api } from "@/lib/api";
import { useTheme, type Accent, type Density, type ThemeMode } from "@/components/theme";
import { ProActivation } from "@/components/pro-activation";
import { cn } from "@/lib/utils";

const MODE_OPTIONS: { value: ThemeMode; label: string; icon: ComponentType<{ className?: string }> }[] = [
  { value: "system", label: "System", icon: Monitor },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "light", label: "Light", icon: Sun },
];

const DENSITY_OPTIONS: { value: Density; label: string; hint: string }[] = [
  { value: "comfortable", label: "Comfortable", hint: "More room around controls" },
  { value: "compact", label: "Compact", hint: "Tighter scan-and-tune density" },
];

const ACCENTS: { value: Accent; label: string; swatch: string }[] = [
  { value: "verdant", label: "Verdant", swatch: "#4ade80" },
  { value: "blue", label: "Blue", swatch: "#60a5fa" },
  { value: "violet", label: "Violet", swatch: "#a78bfa" },
  { value: "amber", label: "Amber", swatch: "#fbbf24" },
  { value: "rose", label: "Rose", swatch: "#fb7185" },
];

export function Settings() {
  const cfg = useAsync(() => api.config());
  const ver = useAsync(() => api.version().catch(() => null));
  const status = useAsync(() => api.ollamaStatus().catch(() => null));
  const storage = useAsync(() => api.storage().catch(() => null));
  const { theme, mode, setTheme, accent, setAccent, density, setDensity } = useTheme();

  const [host, setHost] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [saving, setSaving] = useState(false);

  useInterval(() => status.reload(), 10000);

  useEffect(() => {
    if (cfg.data) {
      setHost(cfg.data.ollama_host);
      setDefaultModel(cfg.data.default_model ?? "");
    }
  }, [cfg.data]);

  const dirty = useMemo(() => {
    if (!cfg.data) return false;
    return host !== cfg.data.ollama_host || defaultModel !== (cfg.data.default_model ?? "");
  }, [cfg.data, defaultModel, host]);

  const save = async () => {
    setSaving(true);
    try {
      await api.saveConfig({
        ollama_host: host.trim(),
        default_model: defaultModel.trim(),
        theme,
      });
      toast.success("Settings saved");
      cfg.reload();
    } catch (e) {
      toast.error("Save failed", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const resetAppearance = () => {
    setTheme("dark");
    setAccent("verdant");
    setDensity("comfortable");
    toast.success("Appearance reset");
  };

  const copyWorkspace = async () => {
    const workspace = cfg.data?.workspace;
    if (!workspace) return;
    try {
      await navigator.clipboard.writeText(workspace);
      toast.success("Workspace path copied");
    } catch {
      toast.error("Could not copy workspace path");
    }
  };

  const copyStoragePath = async (path: string | undefined, label: string) => {
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      toast.success(`${label} copied`);
    } catch {
      toast.error(`Could not copy ${label.toLowerCase()}`);
    }
  };

  const exportDebugBundle = async () => {
    try {
      const bundle = await api.debugBundle();
      const json = JSON.stringify(bundle, null, 2);
      const blob = new Blob([json], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      a.href = url;
      a.download = `lac-debug-${stamp}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Debug bundle exported");
    } catch (e) {
      toast.error("Export failed", { description: e instanceof Error ? e.message : String(e) });
    }
  };

  return (
    <>
      <PageHeader title="Settings" subtitle="Tune how LAC connects, looks, and behaves on this machine.">
        <Button onClick={save} disabled={saving || cfg.loading || !dirty}>
          <Save /> {saving ? "Saving..." : dirty ? "Save changes" : "Saved"}
        </Button>
      </PageHeader>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid gap-5">
          <Section
            icon={Terminal}
            title="Engine"
            description="Connection defaults for Ollama and model startup."
            action={
              <StatusPill
                online={Boolean(status.data?.running)}
                label={status.data?.running ? `Ollama ${status.data.version ?? "online"}` : "Ollama offline"}
              />
            }
          >
            <div className="grid gap-4 md:grid-cols-2">
              <Field
                label="Ollama host"
                description="Local engine endpoint. Most Windows installs use the default port."
              >
                {cfg.loading ? (
                  <Skeleton className="h-[var(--control-h)] w-full" />
                ) : (
                  <Input
                    value={host}
                    onChange={(e) => setHost(e.target.value)}
                    placeholder="http://localhost:11434"
                    spellCheck={false}
                  />
                )}
              </Field>
              <Field
                label="Default model"
                description="Optional model to preselect in chat and Pro workflows."
              >
                {cfg.loading ? (
                  <Skeleton className="h-[var(--control-h)] w-full" />
                ) : (
                  <Input
                    value={defaultModel}
                    onChange={(e) => setDefaultModel(e.target.value)}
                    placeholder="e.g. llama3.2:3b"
                    spellCheck={false}
                  />
                )}
              </Field>
            </div>
            {cfg.error && <ErrorState message={cfg.error} onRetry={cfg.reload} />}
          </Section>

          <Section
            icon={Palette}
            title="Appearance"
            description="Local display preferences for this install. Changes apply immediately."
            action={
              <Button variant="ghost" size="sm" onClick={resetAppearance}>
                <RotateCcw /> Reset
              </Button>
            }
          >
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_240px]">
              <div className="space-y-5">
                <Field label="Theme mode" description="Follow Windows or pin LAC to a specific mode.">
                  <Segmented<ThemeMode>
                    value={mode}
                    onChange={setTheme}
                    options={MODE_OPTIONS.map((item) => ({
                      value: item.value,
                      label: item.label,
                      icon: item.icon,
                    }))}
                  />
                </Field>

                <Field label="Accent" description="Verdant stays the default brand accent; alternates are local.">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                    {ACCENTS.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        onClick={() => setAccent(item.value)}
                        className={cn(
                          "flex h-12 items-center gap-2 rounded border px-2.5 text-left text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-verdant-soft",
                          accent === item.value
                            ? "border-verdant bg-verdant-soft text-fg"
                            : "border-line bg-panel-2 text-fg-muted hover:border-line-strong hover:bg-panel-3 hover:text-fg"
                        )}
                        aria-pressed={accent === item.value}
                      >
                        <span
                          className="h-4 w-4 rounded-full border border-black/10"
                          style={{ background: item.swatch }}
                        />
                        <span className="truncate">{item.label}</span>
                        {accent === item.value && <Check className="ml-auto h-3.5 w-3.5 text-verdant" />}
                      </button>
                    ))}
                  </div>
                </Field>

                <Field label="Density" description="Choose the working rhythm that fits your screen.">
                  <div className="grid gap-2 sm:grid-cols-2">
                    {DENSITY_OPTIONS.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        onClick={() => setDensity(item.value)}
                        className={cn(
                          "rounded border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-verdant-soft",
                          density === item.value
                            ? "border-verdant bg-verdant-soft"
                            : "border-line bg-panel-2 hover:border-line-strong hover:bg-panel-3"
                        )}
                        aria-pressed={density === item.value}
                      >
                        <span className="flex items-center gap-2 text-[13px] font-semibold">
                          <SlidersHorizontal className="h-3.5 w-3.5 text-verdant" />
                          {item.label}
                        </span>
                        <span className="mt-1 block text-[12px] text-fg-muted">{item.hint}</span>
                      </button>
                    ))}
                  </div>
                </Field>
              </div>

              <ThemePreview />
            </div>
          </Section>

          <Section icon={Sparkles} title="Account & Pro" description="Unlock the Pro cockpit on this machine.">
            <ProActivation embedded />
          </Section>
        </div>

        <aside className="grid content-start gap-5">
          <Section icon={Activity} title="Diagnostics" description="Local state for quick sanity checks.">
            <dl className="grid gap-3 text-[13px]">
              <Meta label="Engine">
                <StatusPill
                  online={Boolean(status.data?.running)}
                  label={status.data?.running ? "Online" : "Offline"}
                />
              </Meta>
              <Meta label="Endpoint">{host || cfg.data?.ollama_host || "-"}</Meta>
              <Meta label="Resolved theme">{theme}</Meta>
              <Meta label="UI density">{density}</Meta>
            </dl>
            <Button variant="secondary" size="sm" className="mt-4 w-full" onClick={exportDebugBundle}>
              <Download /> Export debug bundle
            </Button>
          </Section>

          <Section icon={HardDrive} title="Storage" description="App payload stays separate from model weights.">
            <dl className="grid gap-3 text-[13px]">
              <Meta label="Install mode">
                <span className="inline-flex items-center gap-1.5 rounded-pill border border-success/30 bg-success-soft px-2.5 py-1 text-[12px] font-medium text-success">
                  <span className="h-1.5 w-1.5 rounded-full bg-success" />
                  On-demand Ollama pull
                </span>
              </Meta>
              <Meta label="App payload">
                {storage.loading ? <Skeleton className="h-5 w-24" /> : formatBytes(storage.data?.app_size_bytes)}
              </Meta>
              <Meta label="Ollama models">
                {storage.loading ? (
                  <Skeleton className="h-5 w-24" />
                ) : (
                  formatBytes(storage.data?.ollama_models_size_bytes)
                )}
              </Meta>
              <Meta label="Model store">
                <PathValue
                  value={storage.data?.ollama_models_dir ?? "-"}
                  onCopy={() => copyStoragePath(storage.data?.ollama_models_dir, "Model store path")}
                  disabled={!storage.data?.ollama_models_dir}
                />
              </Meta>
              <Meta label="Bundled weights">
                {storage.data?.models_are_bundled ? (
                  <span className="text-warning">{storage.data.model_weight_files_in_app.length} found in app</span>
                ) : (
                  <span className="text-success">None detected</span>
                )}
              </Meta>
            </dl>
          </Section>

          <Section icon={Info} title="About" description="Build and source details.">
            <dl className="grid gap-3 text-[13px]">
              <Meta label="App">
                <span className="font-mono">LAC v{ver.data?.version ?? "-"}</span>
              </Meta>
              <Meta label="Workspace">
                <PathValue value={cfg.data?.workspace ?? "-"} onCopy={copyWorkspace} disabled={!cfg.data?.workspace} />
              </Meta>
              <Meta label="Source">
                <a
                  href={ver.data?.github_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 text-verdant hover:underline"
                >
                  <Github className="h-3.5 w-3.5" /> GitHub
                </a>
              </Meta>
            </dl>
          </Section>
        </aside>
      </div>
    </>
  );
}

function Section({
  icon: Icon,
  title,
  description,
  action,
  children,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line bg-panel-2/60 px-5 py-4">
        <div className="flex min-w-0 gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-line bg-panel text-verdant">
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">{title}</h2>
            <p className="mt-0.5 text-[13px] text-fg-muted">{description}</p>
          </div>
        </div>
        {action}
      </div>
      <div className="p-5">{children}</div>
    </Card>
  );
}

function Field({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
        {label}
      </span>
      {description && <span className="mb-2 block text-[13px] text-fg-muted">{description}</span>}
      {children}
    </label>
  );
}

function Segmented<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string; icon: ComponentType<{ className?: string }> }[];
}) {
  return (
    <div className="grid grid-cols-3 gap-1 rounded border border-line bg-panel-2 p-1">
      {options.map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => onChange(item.value)}
            className={cn(
              "flex h-9 items-center justify-center gap-2 rounded text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-verdant-soft",
              value === item.value ? "bg-panel text-fg shadow-sm" : "text-fg-muted hover:bg-panel-3 hover:text-fg"
            )}
            aria-pressed={value === item.value}
          >
            <Icon className="h-3.5 w-3.5" />
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

function ThemePreview() {
  return (
    <div className="rounded border border-line bg-canvas p-3 shadow-sm">
      <div className="rounded border border-line bg-panel p-3">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="font-mono text-[13px] font-semibold">lac</div>
            <div className="mt-1 text-[11px] text-fg-muted">Preview</div>
          </div>
          <CircleDot className="h-4 w-4 text-verdant" />
        </div>
        <div className="space-y-2">
          <div className="h-2 rounded-pill bg-panel-3" />
          <div className="h-2 w-3/4 rounded-pill bg-panel-3" />
          <div className="mt-3 rounded border border-verdant/40 bg-verdant-soft px-2.5 py-2 text-[12px] text-fg">
            Accent, focus, and selected states
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusPill({ online, label }: { online: boolean; label: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[12px] font-medium",
        online ? "border-success/30 bg-success-soft text-success" : "border-warning/30 bg-warning-soft text-warning"
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", online ? "bg-success" : "bg-warning")} />
      {label}
    </span>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1">
      <dt className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-faint">{label}</dt>
      <dd className="min-w-0 text-fg">{children}</dd>
    </div>
  );
}

function PathValue({
  value,
  onCopy,
  disabled,
}: {
  value: string;
  onCopy: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="truncate font-mono text-[12px]">{value}</span>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 shrink-0"
        onClick={onCopy}
        aria-label="Copy path"
        disabled={disabled}
      >
        <Copy className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

function formatBytes(value: number | null | undefined) {
  if (value == null) return "-";
  if (value === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** index;
  return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}
