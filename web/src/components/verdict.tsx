import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";

export type Verdict = "fits" | "offload" | "too_large" | "unknown";

export interface VerdictInfo {
  label: string;
  badge: BadgeProps["variant"];
  bar: "success" | "warning" | "danger";
}

const MAP: Record<Verdict, VerdictInfo> = {
  fits: { label: "Fits GPU", badge: "success", bar: "success" },
  offload: { label: "Offload", badge: "warning", bar: "warning" },
  too_large: { label: "Too large", badge: "danger", bar: "danger" },
  unknown: { label: "Unknown fit", badge: "neutral", bar: "iris" },
};

/** Compute a verdict from a VRAM requirement (GB) and total VRAM (GB). */
export function verdictFromVram(req: number | undefined, total: number | undefined): Verdict {
  if (!req || !total) return "unknown";
  if (req <= total * 0.9) return "fits";
  if (req <= total * 2.0) return "offload";
  return "too_large";
}

/** Normalize the library API's `fit` string into our verdict. */
export function verdictFromFit(fit: string | undefined): Verdict {
  switch (fit) {
    case "gpu":
    case "maybe":
      return "fits";
    case "offload":
      return "offload";
    case "too_big":
      return "too_large";
    default:
      return "unknown";
  }
}

export function verdictInfo(v: Verdict): VerdictInfo {
  return MAP[v];
}

export function VerdictBadge({ verdict, className }: { verdict: Verdict; className?: string }) {
  const info = MAP[verdict];
  return (
    <Badge variant={info.badge} dot className={className}>
      {info.label}
    </Badge>
  );
}

export function FitBar({
  req,
  total,
  verdict,
  className,
}: {
  req: number;
  total: number;
  verdict: Verdict;
  className?: string;
}) {
  const pct = total ? Math.min(100, Math.round((req / total) * 100)) : verdict === "too_large" ? 100 : 0;
  return <Progress value={pct} variant={MAP[verdict].bar} className={className} />;
}
