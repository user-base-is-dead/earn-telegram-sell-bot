# Admin Panel Redesign: Midnight Luxury — Design Spec

## Goal

Redesign the Next.js admin panel (`admin/`) from its current light-default violet/teal/coral/magenta
theme into a dark-only, premium "Midnight Luxury" aesthetic: near-black surfaces, a deep-indigo
primary accent, a reserved warm-gold accent for genuinely premium moments, Fraunces display headings
+ Inter body/data typography, richer gradient-filled charts with real-data sparklines, and a faster
perceived-speed feel (route skeletons, optimistic row actions, tightened motion timing).

This supersedes the previous "Add violet glow and gradient wash" pass (already shipped, see git log
`70d1cd1`..`0bb0f16`) — it's an evolution of the same token system, not a rewrite of the architecture.

## Non-goals

- No light mode. `next-themes`/theme toggle is removed; dark is the only palette.
- No new npm dependencies. `recharts`, `motion/react`, `next/font/google` are already in
  `admin/package.json` / Next.js core.
- No new chart types requiring new derived queries (bar comparison, heatmap/activity grid). Sparklines
  only where real per-day data already exists from `getDashboardStats`.
- No schema changes, no new Supabase queries beyond grouping data already fetched.
- No changes to `app/` (the Telegram bot) or its handlers — this is `admin/` only.

## Architecture

Everything rides on the existing CSS-custom-property token layer in `admin/src/app/globals.css`
(`--bg`, `--surface`, `--ink`, `--ink-muted`, `--border`, `--brand-*`, `--glow-*`) consumed via
Tailwind arbitrary values (`text-[var(--ink)]`) across every component. The redesign is:

1. **Token rewrite** — `globals.css`'s `:root` (light) block is deleted; `.dark`'s values become the
   only palette and get retuned to the near-black/indigo/gold system. `--brand-gold-from/to` and a
   `--glow-gold` token are added alongside the existing violet/teal/coral/magenta set.
2. **Font swap** — `admin/src/app/layout.tsx` swaps `Geist`/`Geist_Mono` imports for `Fraunces` and
   `Inter` from `next/font/google`, exposed as the same `--font-sans` / new `--font-heading` CSS
   variables `globals.css`'s `@theme inline` block already wires up (today `--font-heading` aliases
   `--font-sans`; it becomes its own Fraunces variable).
3. **Component-level changes** are additive/retuning, not restructuring: existing components keep
   their props and data flow; only class names, chart internals, and a few `useState` additions for
   optimistic feedback change.

## Components

### Theme & typography

- `admin/src/app/globals.css` — remove `:root` light block, retune `.dark` to Midnight Luxury values,
  add gold brand tokens.
- `admin/src/app/layout.tsx` — swap font imports (Fraunces + Inter), remove/no-op any dark-mode class
  toggling logic if `next-themes` is wired there (verify at implementation time; if absent, no change
  needed beyond the font swap).
- Any theme-toggle UI (check `topbar.tsx`, `sidebar.tsx`, `ui/` for a toggle component) is removed if
  present.

### Speed

- `admin/src/app/(admin)/dashboard/loading.tsx`, `orders/loading.tsx`, `products/loading.tsx`,
  `users/loading.tsx` — new files, each a `Skeleton`-based placeholder matching that page's layout
  shape (KPI row + chart area for dashboard, list rows for orders/products/users).
- `admin/src/components/order-row.tsx` — add local `useState<"idle"|"approved"|"rejected">` that
  flips immediately on click (before the `await`), grays out the row and disables both buttons; on
  server-action failure, revert the state and show the existing `toast.error`.
- `admin/src/components/product-card.tsx` — same optimistic pattern on the stock-active `Switch`:
  flip local state immediately, revert on failure (the component already has `useTransition` and a
  toast-on-error path per the existing `setProductActive` call — this adds a local optimistic boolean
  in front of the `checked` prop rather than waiting on `revalidatePath`).
- `admin/src/lib/motion.ts` — `DURATION.page` 0.4 → 0.28, `STAGGER_CHILD_DELAY` 0.04 → tightened
  slightly (exact value decided during implementation by feel, capped at 0.03).

### Charts

- `admin/src/components/dashboard-chart.tsx` — `LineChart`/`Line` → `AreaChart`/`Area` with an SVG
  `<linearGradient>` fill (indigo fading to transparent), same `data={trend}` prop, same shape.
- `admin/src/components/revenue-by-product-donut.tsx` — retune `COLORS` array to the new palette,
  gold for the top (index 0) slice.
- `admin/src/lib/queries.ts` — `getDashboardStats()` gains per-day breakdowns for **revenue (INR)**,
  **revenue (USDT)**, and **order count**, grouped from the `recentOrders` array it already fetches
  (same pattern as the existing `byDay`/`trend` construction, just three series instead of one, or a
  shared helper that produces all three from one pass over `approved`). Pending-approval and Users
  KPIs get no new query and no sparkline.
- New small presentational component (e.g. `admin/src/components/kpi-sparkline.tsx`) — a minimal
  `ResponsiveContainer` + `AreaChart` with no axes/tooltip/grid, sized to sit inside a `KpiCard`.
  `KpiCard` gains an optional `sparkline?: number[]` prop; when present, renders it under the value.

## Data flow

No new Supabase queries — `getDashboardStats()`'s existing `recentOrders` fetch (7 days, `approved`
filter) is grouped an extra two ways (by day for orders-count, reusing the existing by-day revenue
split into INR/USDT) and returned as three arrays instead of one `trend` array. `OrderRow` and
`ProductCard`'s optimistic state is purely local UI state layered in front of the same server actions
(`approveOrder`, `rejectOrder`, `setProductActive`) and `revalidatePath` calls already in
`orders/actions.ts` / `products/actions.ts` — no change to those server actions.

## Error handling

- Optimistic UI failure paths: both `OrderRow` and `ProductCard` already `try/catch` their server
  action calls and `toast.error` on failure — the addition is reverting the optimistic local state in
  that catch block so the UI doesn't show a false "approved"/"toggled" state after a failed mutation.
- Skeleton (`loading.tsx`) files have no error branch of their own — Next.js's existing
  `error.tsx`-or-thrown-error behavior for these routes is unchanged (none exists today; not adding
  one is consistent with not expanding scope beyond what was asked).
- Dropping light mode: if `next-themes` persists a `theme` value in localStorage/cookies from before
  this change, no migration is needed — the app simply always renders `.dark` regardless of that
  stored value.

## Testing / verification

`admin/` has no test runner configured (only `eslint`), consistent with the prior redesign plan's
approach. Verification is:
1. `cd admin && npm run build` after each task (typecheck + lint-blocking errors).
2. Manual browser walkthrough of all five pages (`/login`, `/dashboard`, `/orders`, `/products`,
   `/users`) in `npm run dev`, confirming: Midnight Luxury palette renders correctly, Fraunces
   headings / Inter body render, skeletons show on navigation, optimistic actions feel instant,
   charts render with gradient fill + sparklines + retuned donut colors.
3. `prefers-reduced-motion: reduce` check (existing global override in `globals.css` already collapses
   all animation durations — confirm it still covers the retuned motion constants).
4. Since dark-only removes a code path (light theme), confirm no lingering light-mode-only class or
   conditional is left dead in `layout.tsx`/`topbar.tsx`/wherever the theme toggle lived.
