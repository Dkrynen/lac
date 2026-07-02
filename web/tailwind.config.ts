import type { Config } from "tailwindcss";

// Tailwind theme maps directly to the Apt design tokens (see src/index.css).
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--bg)",
        panel: "var(--surface)",
        "panel-2": "var(--surface-2)",
        "panel-3": "var(--surface-3)",
        line: "var(--border)",
        "line-strong": "var(--border-strong)",
        fg: "var(--text)",
        "fg-muted": "var(--text-muted)",
        "fg-faint": "var(--text-faint)",
        iris: "var(--accent)",
        "iris-hover": "var(--accent-hover)",
        "iris-pressed": "var(--accent-pressed)",
        "iris-soft": "var(--accent-soft)",
        "iris-fg": "var(--accent-fg)",
        success: "var(--success)",
        "success-soft": "var(--success-soft)",
        warning: "var(--warning)",
        "warning-soft": "var(--warning-soft)",
        danger: "var(--danger)",
        "danger-soft": "var(--danger-soft)",
        info: "var(--info)",
        "info-soft": "var(--info-soft)",
      },
      borderColor: {
        DEFAULT: "var(--border)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius)",
        lg: "var(--radius-lg)",
        pill: "var(--radius-pill)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        focus: "var(--shadow-focus)",
      },
      transitionTimingFunction: {
        apt: "var(--ease)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "rise": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "blink": { "50%": { opacity: "0" } },
      },
      animation: {
        "fade-in": "fade-in 160ms var(--ease)",
        "rise": "rise 200ms var(--ease)",
        "blink": "blink 1.1s steps(2,start) infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
