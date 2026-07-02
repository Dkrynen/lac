import * as React from "react";
import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  subtitle,
  children,
  className,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-6 flex flex-wrap items-end justify-between gap-4", className)}>
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-[13px] text-fg-muted">{subtitle}</p>}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-line bg-panel p-10 text-center">
      <p className="text-sm text-fg-muted">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 rounded border border-line px-3 py-1.5 text-[13px] text-fg hover:bg-panel-3"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
  children,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line bg-panel/40 p-12 text-center">
      {icon && <div className="mb-3 text-fg-faint">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-[13px] text-fg-muted">{hint}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
