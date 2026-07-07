import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type Theme = "dark" | "light";
export type ThemeMode = "system" | Theme;
export type Accent = "verdant" | "blue" | "violet" | "amber" | "rose";
export type Density = "comfortable" | "compact";

interface ThemeCtx {
  theme: Theme;
  mode: ThemeMode;
  setTheme: (t: ThemeMode) => void;
  accent: Accent;
  setAccent: (a: Accent) => void;
  density: Density;
  setDensity: (d: Density) => void;
  toggle: () => void;
}
const Ctx = createContext<ThemeCtx | null>(null);
const KEY = "lac-theme";
const MODE_KEY = "lac-theme-mode";
const ACCENT_KEY = "lac-accent";
const DENSITY_KEY = "lac-density";

function systemTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function readMode(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  const savedMode = localStorage.getItem(MODE_KEY) as ThemeMode | null;
  if (savedMode === "system" || savedMode === "dark" || savedMode === "light") return savedMode;
  const legacy = localStorage.getItem(KEY) as Theme | null;
  if (legacy === "dark" || legacy === "light") return legacy;
  return systemTheme();
}

function readAccent(): Accent {
  if (typeof window === "undefined") return "verdant";
  const saved = localStorage.getItem(ACCENT_KEY) as Accent | null;
  return saved === "blue" || saved === "violet" || saved === "amber" || saved === "rose" ? saved : "verdant";
}

function readDensity(): Density {
  if (typeof window === "undefined") return "comfortable";
  return localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable";
}

function resolve(mode: ThemeMode): Theme {
  return mode === "system" ? systemTheme() : mode;
}

function apply(theme: Theme, mode: ThemeMode, accent: Accent, density: Density) {
  const el = document.documentElement;
  el.classList.toggle("dark", theme === "dark");
  el.dataset.theme = theme;
  el.dataset.themeMode = mode;
  el.dataset.accent = accent;
  el.dataset.density = density;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(readMode);
  const [system, setSystem] = useState<Theme>(systemTheme);
  const [accent, setAccentState] = useState<Accent>(readAccent);
  const [density, setDensityState] = useState<Density>(readDensity);

  const theme = useMemo(() => (mode === "system" ? system : mode), [mode, system]);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!media) return;
    const onChange = () => setSystem(systemTheme());
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, []);

  useEffect(() => {
    apply(theme, mode, accent, density);
    localStorage.setItem(MODE_KEY, mode);
    localStorage.setItem(KEY, theme);
    localStorage.setItem(ACCENT_KEY, accent);
    localStorage.setItem(DENSITY_KEY, density);
  }, [theme, mode, accent, density]);

  const setTheme = (t: ThemeMode) => setMode(t);
  const setAccent = (a: Accent) => setAccentState(a);
  const setDensity = (d: Density) => setDensityState(d);
  const toggle = () => setMode((t) => (resolve(t) === "dark" ? "light" : "dark"));

  return (
    <Ctx.Provider value={{ theme, mode, setTheme, accent, setAccent, density, setDensity, toggle }}>
      {children}
    </Ctx.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
