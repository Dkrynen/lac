# Apt — Design System

The single source of truth for Apt's brand and UI. Sleek, dark, dev-tool grade
(Linear / Raycast / Vercel territory). Dark-first. Information-dense but calm.

> **Why "Apt"?** *apt* = fitting, appropriate. The product's core job is finding
> models that **fit** your hardware. The name is the concept.

---

## 1. Brand

| | |
|---|---|
| **Name** | Apt (lowercase wordmark: `apt`) |
| **Tagline (primary)** | Local AI, sorted. |
| **Tagline (alt)** | The control room for your local models. |
| **Voice** | Sharp, technical, calm. No hype, no emoji. Say it once, precisely. |
| **Audience** | Developers and power users running local LLMs. |

**Logo direction (pick one in Phase 2):**
1. **Wordmark + cursor** — lowercase mono `apt` with a blinking terminal cursor block. Pure dev-tool.
2. **The fit mark** — an abstract `A` formed by two chevrons nesting (a model "fitting" into hardware).
3. **Glyph** — a rounded-square app icon, Iris gradient, white `a` or cursor glyph.

Colors used in every logo lockup: `--text` on `--bg`, with the Iris accent (`--accent`) reserved for the cursor/glyph only.

---

## 2. Color

Dark is the default. Tokens are CSS variables (see `tokens.css`).

### Neutral (dark — default)
| Token | Value | Use |
|---|---|---|
| `--bg` | `#09090B` | App canvas |
| `--surface` | `#0F0F13` | Cards, sidebar |
| `--surface-2` | `#15151A` | Inputs, raised rows |
| `--surface-3` | `#1B1B22` | Hover, active well |
| `--border` | `rgba(255,255,255,.08)` | Default 1px borders |
| `--border-strong` | `rgba(255,255,255,.14)` | Emphasis borders |
| `--text` | `#ECECEE` | Primary text |
| `--text-muted` | `#A1A1AA` | Secondary text |
| `--text-faint` | `#71717A` | Tertiary / placeholders |

### Accent — "Iris" (the one accent)
| Token | Value | Use |
|---|---|---|
| `--accent` | `#6E7BF2` | Primary actions, focus, links |
| `--accent-hover` | `#8B96F5` | Hover state |
| `--accent-pressed` | `#5A67E0` | Active/pressed |
| `--accent-soft` | `rgba(110,123,242,.14)` | Tints, selected row bg |
| `--accent-fg` | `#FFFFFF` | Text on accent |

### Semantic
| Token | Value | Use |
|---|---|---|
| `--success` | `#3DD68C` | "Fits GPU", installed, OK |
| `--warning` | `#F5A524` | "Offload", partial |
| `--danger` | `#F6465D` | "Too large", errors, destructive |
| `--info` | `#38BDF8` | Info toasts |

### Light theme
Provided under `[data-theme="light"]` in `tokens.css`. Same semantic structure;
canvas `#FAFAFA`, surfaces white/near-white, borders `rgba(0,0,0,.08)`, text
`#18181B`. Accent stays Iris. Light is secondary — dark ships first.

---

## 3. Typography

- **Sans:** Geist Sans → fallback Inter, system-ui. (Dev-tool standard.)
- **Mono:** Geist Mono → fallback JetBrains Mono, ui-monospace. Used for **model names, metrics, VRAM, params, code, tokens** — anything machine-y.

Type scale (compact, dev density):

| Role | Size / Line | Weight | Tracking |
|---|---|---|---|
| Display | 30px / 1.15 | 600 | -0.02em |
| H1 | 22px / 1.2 | 600 | -0.02em |
| H2 | 18px / 1.3 | 600 | -0.01em |
| H3 | 15px / 1.4 | 600 | -0.01em |
| Body | 14px / 1.5 | 400 | 0 |
| Small | 13px / 1.45 | 400 | 0 |
| Caption | 12px / 1.4 | 500 | 0.01em |
| Mono metric | 13px / 1.4 | 500 | 0 |

---

## 4. Spacing, radius, elevation, motion

- **Spacing:** 4px base grid (Tailwind defaults: 1=4, 2=8, 3=12, 4=16, 6=24, 8=32).
- **Radius:** `--radius-sm` 6px · `--radius` 8px (default) · `--radius-lg` 12px · pill 9999px. Small radii = serious tool.
- **Elevation:**
  - `--shadow-sm` `0 1px 2px rgba(0,0,0,.30)` — cards
  - `--shadow-md` `0 4px 12px rgba(0,0,0,.35)` — popovers
  - `--shadow-lg` `0 12px 32px rgba(0,0,0,.45)` — modals
  - `--shadow-focus` `0 0 0 2px var(--bg), 0 0 0 4px var(--accent-soft)` — focus ring
- **Motion:** 150ms ease-out default; 200ms for transforms. Honor `prefers-reduced-motion`.
- **Focus:** always a visible Iris ring. Never outline:none without replacement.

---

## 5. Components (primitives)

shadcn/ui base, themed to these tokens:

- **Button** — variants: `primary` (Iris), `secondary` (surface-2 + border), `outline` (transparent + border), `ghost` (transparent, hover surface-3), `danger` (danger bg). Sizes: `sm` / `md` / `lg` / `icon`.
- **Badge** — semantic (`success`/`warning`/`danger`/`info`) + `neutral` + `accent`. Dot + label. Used for compatibility, status, capabilities.
- **Card** — `--surface`, 1px `--border`, `--radius`, `--shadow-sm`, 16–20px padding.
- **Input** — `--surface-2`, 1px border, 8px radius, Iris focus ring, mono optional.
- **Progress** — track `--surface-3`, fill `--accent` (or semantic). For download % and VRAM-fit bars.
- **Tabs · Tooltip · Dialog · Sheet · Select · Switch · ScrollArea · Skeleton · Toast(sonner) · Table** — standard.

---

## 6. Signature patterns

- **Model card** — name (mono, semibold), one-line description, a row of capability badges (vision/tools/thinking/embed), a compatibility verdict badge (Fits / Offload / Too large), params + context (mono, faint), and a thin VRAM-fit progress bar. Primary action: Install / Run.
- **Compatibility verdict** maps directly to semantic colors (success/warning/danger) — it's the most-glanced element, so make it scannable.
- **Density:** tables/lists lead; big hero art is avoided. Mono numerics align. Hover rows tint `--surface-3`.
- **Empty/loading states** use Skeletons, never spinners-with-no-context.

---

## 7. Implementation

- **Stack:** Vite + React + TypeScript + Tailwind CSS + shadcn/ui, source in `web/`.
- **Tokens:** `web/tokens.css` defines `:root` (dark) + `[data-theme="light"]`. Tailwind config maps `colors.*` to `var(--*)`. shadcn components consume the same vars.
- **Engine:** unchanged — Flask (`backend/api.py`) stays the REST API + Ollama layer. In **dev**, Vite proxies `/api` → Flask (`:5050`). In **prod**, `vite build` → `web/dist`, Flask serves `web/dist` as static and falls through to `index.html` for client routing.
- **shadcn note:** shadcn expects HSL *channels* (e.g. `--background: 0 0% 5%`). Tokens here are hex for readability in the preview; the Phase 2 scaffold will emit the HSL-channel equivalents from the same values.

---

## 8. Accessibility

- Target **AA**: text-on-bg pairs verified ≥ 4.5:1, Iris-on-white and white-on-Iris both pass.
- Visible focus everywhere; keyboard nav for every interactive element.
- `prefers-reduced-motion` disables transitions.
- `prefers-color-scheme: light` can auto-switch, but dark is default.
