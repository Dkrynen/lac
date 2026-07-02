import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-9 w-full rounded border border-line bg-panel-2 px-3 text-sm text-fg placeholder:text-fg-faint transition-colors focus-visible:outline-none focus-visible:border-iris focus-visible:ring-2 focus-visible:ring-iris-soft disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";
