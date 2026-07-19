# Admin panel visual redesign

Date: 2026-07-16

## Goal

The admin panel (built per `2026-07-15-admin-panel-design.md`) is functionally complete but visually
flat: no color beyond a single indigo accent, no gradients, minimal motion. This redesign gives it a
premium, vibrant-but-elegant look across all four pages (Dashboard, Orders, Products, Users/Wallet)
plus Login, inspired by the Prodex and SadaxCart Dribbble references, without introducing new
dependencies — `motion/react`, `recharts`, and the shadcn primitives already in `admin/package.json`
cover everything needed.

## Color system

Light-first (dark mode stays available via the existing toggle). Four brand hues, each a
gradient pair plus a matching glow shadow token, layered on top of the existing neutral scale
(`--bg`, `--surface`, `--ink`, `--ink-muted`, `--border`) so shadcn primitives keep working
unmodified:

| Token | Light | Role |
|---|---|---|
| `--brand-violet-from` / `-to` | `#7c6cf0` → `#5b3ff0` | Primary — sidebar active state, primary CTAs, revenue KPI |
| `--brand-teal-from` / `-to` | `#2dd4c5` → `#0ea89a` | Orders KPI, "approved" status |
| `--brand-coral-from` / `-to` | `#ff8a65` → `#ff6b4a` | Pending/attention KPI, "pending" status |
| `--brand-magenta-from` / `-to` | `#e0499e` → `#b52e79` | Users KPI, quaternary chart series |

Each gets a `--glow-{name}` token: `0 0 14px oklch(from var(--brand-{name}-to) l c h / 0.45)` — used
on KPI icon chips and the sidebar's active nav pill, not on body text or large surfaces (glow is an
accent, not a background).

Dark mode: same four hues, glow opacity raised to ~0.6 (reads better against a dark surface); neutral
scale keeps the existing `.dark` block in `globals.css` unchanged.

Semantic reuse: `--success`/`--warning`/`--danger` (already defined) keep mapping to teal/coral's
darker end and the existing `--destructive` respectively, so Orders status badges get color for free
without a second color system.

## Layout & navigation

- **Sidebar** (`components/sidebar.tsx`): keep the existing `layoutId="active-nav"` spring animation
  (already tuned per `lib/motion.ts`), restyle the active pill to a violet gradient fill with a soft
  glow instead of the current flat `bg-accent/10`.
- **Topbar** (new — `components/topbar.tsx`, wired into `(admin)/layout.tsx`): page title (derived
  from route), a search input (client-side filter over the current page's list — no new backend
  query), a notification bell (reuses the existing `live-refresh`/realtime signal for a badge count),
  and an avatar/menu (sign-out). Matches the reference screenshots' top row.

## Motion

Extends `lib/motion.ts` — no new tokens needed beyond what's already there (`DURATION`, `EASE_OUT_QUART`,
`SPRING_INTERACTIVE`, `listContainer`/`listItem`, `PRESS_SCALE`/`HOVER_SCALE`):

- KPI row: existing stagger container/item variants, unchanged timing.
- KPI icon chips: glow fades in ~80ms after the chip's own entrance (nested `motion.div` with a
  delayed opacity transition) — a chip "arrives" then "lights up."
- Charts (`dashboard-chart.tsx`, new categories-donut): recharts' built-in `isAnimationActive` +
  `animationDuration` matching `DURATION.panel` (350ms), no custom animation code.
- Sidebar active pill: unchanged spring, gradient fill swapped in via CSS variable.
- Route transitions: fade+y (8px) on the page content wrapper, `DURATION.page` (400ms).
- All of the above already inherit the global `prefers-reduced-motion` override in `globals.css`
  (animation/transition durations forced to near-zero) — no per-component reduced-motion handling
  needed.

## Per-page changes

- **Dashboard** (`(admin)/dashboard/page.tsx`): KPI cards get gradient icon chips (revenue=violet,
  orders=teal, pending=coral, users=magenta — reusing the KPI icons already implied by `kpi-card.tsx`'s
  props, just adding an `icon`/`accent` prop). Sales trend chart and a new top-categories donut sit
  side-by-side (matches both references). Low-stock banner restyled from a plain bordered strip to a
  coral-tinted card with an icon, same content.
- **Orders** (`(admin)/orders/page.tsx`, `order-row.tsx`): status badges recolored via the semantic
  tokens (pending=coral, approved=teal, rejected=destructive/red) instead of the current
  monochrome badges. Row hover gets a subtle lift (`translateY(-1px)` + shadow), consistent with
  `HOVER_SCALE`-style existing interaction tokens.
- **Products** (`(admin)/products/page.tsx`, `product-card.tsx`): card grid restyled with the same
  elevation/glow language as KPI chips (active/in-stock products get a teal accent bar, low-stock a
  coral one) — reuses existing active/stock data, no new fields.
- **Users/Wallet** (`(admin)/users/page.tsx`): summary KPIs styled like Dashboard's row; ledger table
  gets the same row-hover treatment as Orders.
- **Login** (`login/page.tsx`): card gets a violet glow border and a subtle gradient wash behind the
  Telegram widget container; no functional change to the widget itself.

## What's reused vs. new

**Reused as-is**: all shadcn primitives (`button`, `card`, `dialog`, `table`, `badge`, `switch`,
`select`, `tabs`, `sheet`, `dropdown-menu`), `lib/queries.ts`, `lib/motion.ts` (extended, not
replaced), `lib/session.ts`, `hooks/use-live-updates.ts`, `components/live-refresh.tsx`.

**New**: `components/topbar.tsx`, a categories-donut chart component, color tokens in `globals.css`,
an `icon`/`accent` prop on `kpi-card.tsx`, per-page className/markup restyling. **No new npm
dependencies** — `motion/react`, `recharts`, `lucide-react` already cover every requirement above.

## Out of scope

- No new backend queries or schema changes — this is presentation-layer only, reading the same data
  `lib/queries.ts` already returns.
- No changes to auth/session logic, order approval flow, or product CRUD behavior.
- Mobile-responsive polish is a nice-to-have but not blocking — the panel is single-admin, desktop-used
  today; note as a fast-follow if it comes up during implementation, not a requirement to design now.
