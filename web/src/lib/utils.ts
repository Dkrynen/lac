import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a byte/GB number to a compact human string. */
export function fmtBytes(gb: number | undefined | null): string {
  if (gb == null || isNaN(gb)) return "—";
  if (gb <= 0) return "0 GB";
  if (gb < 1) return `${Math.round(gb * 1024)} MB`;
  if (gb < 1024) return `${gb.toFixed(gb < 10 ? 1 : 0)} GB`;
  return `${(gb / 1024).toFixed(2)} TB`;
}

/** Format parameter count (in billions) compactly. */
export function fmtParams(b: number | undefined | null): string {
  if (!b) return "—";
  if (b >= 1000) return `${(b / 1000).toFixed(1)}T`;
  if (b >= 1) return b >= 100 ? `${Math.round(b)}B` : `${b.toFixed(1)}B`;
  return `${Math.round(b * 1000)}M`;
}

export function fmtContext(ctx: number | undefined | null): string {
  if (!ctx) return "—";
  if (ctx >= 1000) return `${(ctx / 1000).toFixed(0)}k`;
  return `${ctx}`;
}

/** Clamp + format a percent for VRAM fit bars. */
export function fitPct(vram: number, total: number): number {
  if (!total) return 0;
  return Math.min(100, Math.round((vram / total) * 100));
}
