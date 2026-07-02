import * as React from "react";
import { Download, Play, Trash2, Check } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FitBar, VerdictBadge, verdictFromVram, type Verdict } from "@/components/verdict";
import { cn, fmtContext, fmtParams } from "@/lib/utils";

export interface ModelCardData {
  name: string;
  description?: string;
  params_b?: number;
  context?: number;
  vram_gb?: number; // required VRAM (GB), e.g. Q4
  capabilities?: string[];
  installed?: boolean;
}

export function ModelCard({
  model,
  totalVram,
  vramLabel = "VRAM",
  primaryLabel = "Install",
  onPrimary,
  secondaryLabel,
  onSecondary,
  busy,
  className,
}: {
  model: ModelCardData;
  totalVram?: number;
  vramLabel?: string;
  primaryLabel?: string;
  onPrimary?: () => void;
  secondaryLabel?: string;
  onSecondary?: () => void;
  busy?: boolean;
  className?: string;
}) {
  const verdict: Verdict = verdictFromVram(model.vram_gb, totalVram);
  const caps = (model.capabilities || []).slice(0, 4);

  return (
    <Card className={cn("group flex flex-col gap-3 p-4 transition-colors hover:border-line-strong", className)}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[14.5px] font-semibold">{model.name}</div>
          {model.description && (
            <p className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-fg-muted">{model.description}</p>
          )}
        </div>
        {model.installed ? (
          <Badge variant="success" dot>
            Installed
          </Badge>
        ) : (
          <VerdictBadge verdict={verdict} />
        )}
      </div>

      {caps.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {caps.map((c) => (
            <Badge key={c} variant="accent">
              {c}
            </Badge>
          ))}
        </div>
      )}

      <div className="flex gap-5">
        <Metric label="Params" value={fmtParams(model.params_b)} />
        <Metric label="Context" value={model.context ? `${fmtContext(model.context)}` : "—"} />
        <Metric label={vramLabel} value={model.vram_gb ? `${model.vram_gb.toFixed(1)} GB` : "—"} />
      </div>

      {model.vram_gb && totalVram ? (
        <div>
          <FitBar req={model.vram_gb} total={totalVram} verdict={verdict} />
          <div className="mt-1 text-[11px] text-fg-faint">
            {Math.round((model.vram_gb / totalVram) * 100)}% of {totalVram} GB
          </div>
        </div>
      ) : null}

      <div className="mt-auto flex items-center gap-2 pt-1">
        {onPrimary && (
          <Button size="sm" className="flex-1" onClick={onPrimary} disabled={busy}>
            {busy ? <Check className="animate-pulse" /> : model.installed ? <Play /> : <Download />}
            {busy ? "Working" : model.installed ? "Run" : primaryLabel}
          </Button>
        )}
        {secondaryLabel && onSecondary && (
          <Button size="sm" variant="secondary" onClick={onSecondary}>
            {primaryLabel === "Remove" ? <Trash2 /> : secondaryLabel}
          </Button>
        )}
      </div>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">{label}</div>
      <div className="font-mono text-[13px] font-medium">{value}</div>
    </div>
  );
}
