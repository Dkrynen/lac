import { cn } from "@/lib/utils";

type Variant = "verdant" | "success" | "warning" | "danger" | "info";

const fills: Record<Variant, string> = {
  verdant: "bg-verdant",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
};

export function Progress({
  value,
  variant = "verdant",
  className,
}: {
  value: number; // 0..100
  variant?: Variant;
  className?: string;
}) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-pill bg-panel-3", className)}>
      <div
        className={cn("h-full rounded-pill transition-[width] duration-300 ease-lac", fills[variant])}
        style={{ width: `${v}%` }}
      />
    </div>
  );
}
