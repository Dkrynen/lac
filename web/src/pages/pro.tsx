import { Sparkles } from "lucide-react";
import { PageHeader } from "@/components/page";
import { Card } from "@/components/ui/card";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { ProActivation } from "@/components/pro-activation";
import { TuneHero } from "@/components/pro/tune-hero";
import { InsightsPanel } from "@/components/pro/insights-panel";
import { AutopilotPanel } from "@/components/pro/autopilot-panel";
import { BenchmarkPanel } from "@/components/pro/benchmark-panel";
import { ImportPanel } from "@/components/pro/import-panel";
import { AgentCockpitPanel } from "@/components/pro/agent-cockpit-panel";

export function Pro() {
  const status = useAsync(() => api.proStatus());
  const licensed = status.data?.licensed;

  if (status.loading) return <PageHeader title="Pro" subtitle="Loading…" />;

  if (!licensed) {
    return (
      <>
        <PageHeader title="LAC Pro" subtitle="Tune local models to this PC, benchmark what actually runs, and import compatible Hugging Face models." />
        <div className="grid gap-5 lg:grid-cols-[minmax(0,420px)_1fr]">
          <ProActivation />
          <Card className="p-5">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Sparkles className="h-4 w-4 text-verdant" />
              What unlocks
            </div>
            <div className="mt-4 grid gap-3 text-[13px] text-fg-muted sm:grid-cols-2">
              <Value title="Autopilot tuning" body="Benchmark and sweep installed models against your exact hardware." />
              <Value title="Model cockpit" body="See speed, tuning status, recommended actions, and local-agent readiness." />
              <Value title="Hugging Face import" body="Resolve compatible GGUF or convertible repos before large downloads begin." />
              <Value title="Measured calibration" body="Replace estimates with real tok/s data from this machine." />
            </div>
          </Card>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader title="LAC Pro"
        subtitle={`Active · ${status.data?.plan ?? "pro"} · ${status.data?.expires_human ?? ""}`} />
      <div className="grid gap-5">
        <AgentCockpitPanel />
        <TuneHero />
        <div className="grid gap-5 lg:grid-cols-2">
          <InsightsPanel /> <AutopilotPanel />
          <BenchmarkPanel /> <ImportPanel />
        </div>
      </div>
    </>
  );
}

function Value({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded border border-line bg-panel-2 p-3">
      <div className="text-[13px] font-semibold text-fg">{title}</div>
      <p className="mt-1 text-[12px] leading-relaxed text-fg-muted">{body}</p>
    </div>
  );
}
