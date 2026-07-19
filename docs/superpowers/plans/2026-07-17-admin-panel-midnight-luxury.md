# Admin Panel Midnight Luxury Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Next.js admin panel (`admin/`) into a dark-only "Midnight Luxury" theme — near-black surfaces, deep-indigo primary accent, a reserved warm-gold accent, Fraunces display headings + Inter body/data typography, gradient-filled charts with real-data sparklines, and faster perceived speed (route skeletons + optimistic row actions).

**Architecture:** Extends the existing CSS-custom-property token system in `admin/src/app/globals.css` (no new token *mechanism*, just new values) and reuses every existing component's data flow. `admin/src/app/layout.tsx` hardcodes a `dark` class on `<html>` (the app currently has no theme toggle and no `ThemeProvider` — `.dark`'s CSS never activates in production today; this makes it the only palette). Charts, sparklines, and optimistic UI are additive changes to existing components, not new architecture.

**Tech Stack:** Next.js 16 (App Router), `motion/react`, `recharts`, `next/font/google` (Fraunces + Inter), shadcn/ui primitives, Supabase JS client — all already in `admin/package.json`. No test runner is configured in `admin/` (only `eslint`); per this project's established convention (see the prior `2026-07-16-admin-panel-visual-redesign.md` plan), this plan uses `npm run build` (typechecks) as the automated check plus manual browser verification for visual/motion changes.

Design spec this plan implements: `docs/superpowers/specs/2026-07-17-admin-panel-midnight-luxury-design.md`.

**Deviations from the spec, decided during planning:**
- The spec says "`:root` light block deleted; `.dark` becomes the only palette." Several shadcn primitives (`switch.tsx`, `input.tsx`, `badge.tsx`, `button.tsx`, `tabs.tsx`, `select.tsx`, `dropdown-menu.tsx`) use Tailwind `dark:` utility variants that only activate under an ancestor `.dark` class (via the existing `@custom-variant dark (&:is(.dark *));`). Deleting the `.dark` selector and merging its values into `:root` would silently turn those `dark:` overrides off. Instead: keep the `.dark` selector and the custom-variant mechanism intact, and make it permanent by hardcoding `dark` in `layout.tsx`'s `<html>` className (no toggle, no `next-themes`). `:root` keeps only `--radius` (not a color, needed regardless of theme); every color token moves into `.dark`.
- `next-themes` is a listed dependency but its only consumer today is `components/ui/sonner.tsx`'s `useTheme()` call — there is no `ThemeProvider` anywhere, so it always resolves to the `"system"` fallback. Since this redesign goes dark-only, that call is replaced with a hardcoded `theme="dark"`, which makes `next-themes` fully unused — removed from `package.json` per "remove imports/deps your changes made unused."
- Fraunces/Inter are configured **without** an explicit `weight` array. Both are variable Google Fonts; per Next.js's `next/font/google` docs, omitting `weight` on a variable font loads the full weight range, which is what "weighted, elegant, and matches whatever `font-weight` a component sets" requires.

---

### Task 1: Design tokens — Midnight Luxury palette

**Files:**
- Modify: `admin/src/app/globals.css`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/app/globals.css` with:

```css
@import "tailwindcss";
@import "tw-animate-css";
@import "shadcn/tailwind.css";

@custom-variant dark (&:is(.dark *));

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --font-sans: var(--font-inter);
  --font-mono: var(--font-geist-mono);
  --font-heading: var(--font-fraunces);
  --color-sidebar-ring: var(--sidebar-ring);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar: var(--sidebar);
  --color-chart-5: var(--chart-5);
  --color-chart-4: var(--chart-4);
  --color-chart-3: var(--chart-3);
  --color-chart-2: var(--chart-2);
  --color-chart-1: var(--chart-1);
  --color-ring: var(--ring);
  --color-input: var(--input);
  --color-border: var(--border);
  --color-destructive: var(--destructive);
  --color-accent-foreground: var(--accent-foreground);
  --color-accent: var(--accent);
  --color-muted-foreground: var(--muted-foreground);
  --color-muted: var(--muted);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-secondary: var(--secondary);
  --color-primary-foreground: var(--primary-foreground);
  --color-primary: var(--primary);
  --color-popover-foreground: var(--popover-foreground);
  --color-popover: var(--popover);
  --color-card-foreground: var(--card-foreground);
  --color-card: var(--card);
  --radius-sm: calc(var(--radius) * 0.6);
  --radius-md: calc(var(--radius) * 0.8);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) * 1.4);
  --radius-2xl: calc(var(--radius) * 1.8);
  --radius-3xl: calc(var(--radius) * 2.2);
  --radius-4xl: calc(var(--radius) * 2.6);
}

:root {
  --radius: 0.625rem;
}

/* Midnight Luxury — the app's only palette. Applied via a hardcoded "dark"
   class on <html> in layout.tsx (no toggle, no next-themes) rather than
   collapsing into :root, so shadcn primitives' existing dark: utility
   variants (the @custom-variant above) keep working unchanged. */
.dark {
  --background: #0b0a12;
  --foreground: #f3ead8;
  --card: #15121f;
  --card-foreground: #f3ead8;
  --popover: #15121f;
  --popover-foreground: #f3ead8;
  --primary: #7c6cf0;
  --primary-foreground: #0b0a12;
  --secondary: #1c1830;
  --secondary-foreground: #f3ead8;
  --muted: #1c1830;
  --muted-foreground: #a99bd1;
  --accent: #7c6cf0;
  --accent-foreground: #0b0a12;
  --destructive: oklch(68% 0.2 25);
  --border: #2a2438;
  --input: #2a2438;
  --ring: #7c6cf0;
  --chart-1: #7c6cf0;
  --chart-2: #2dd4c5;
  --chart-3: #e8c874;
  --chart-4: #ff8a65;
  --chart-5: #a99bd1;
  --sidebar: #15121f;
  --sidebar-foreground: #f3ead8;
  --sidebar-primary: #7c6cf0;
  --sidebar-primary-foreground: #0b0a12;
  --sidebar-accent: #1c1830;
  --sidebar-accent-foreground: #f3ead8;
  --sidebar-border: #2a2438;
  --sidebar-ring: #7c6cf0;

  /* Semantic aliases used directly by admin panel component code (var(--x) arbitrary
     Tailwind values, e.g. text-[var(--ink-muted)]) — these ride on top of the shadcn
     tokens above rather than duplicating them. */
  --bg: var(--background);
  --surface: var(--card);
  --ink: var(--foreground);
  --ink-muted: var(--muted-foreground);
  --success: #2dd4c5;
  --warning: #ff8a65;

  /* Brand gradient system — violet/teal/coral/magenta/gold, each a from/to stop
     for linear-gradient() fills plus a matching glow box-shadow token. Gold is
     reserved for the one "premium hero" moment (see kpi-card.tsx usage), not
     spread across the UI. */
  --brand-violet-from: #7c6cf0;
  --brand-violet-to: #5b3ff0;
  --brand-teal-from: #2dd4c5;
  --brand-teal-to: #0b8578;
  --brand-coral-from: #ff8a65;
  --brand-coral-to: #e2492a;
  --brand-magenta-from: #e0499e;
  --brand-magenta-to: #b52e79;
  --brand-gold-from: #e8c874;
  --brand-gold-to: #b8912e;

  --glow-violet: 0 0 16px rgba(124, 108, 240, 0.6);
  --glow-teal: 0 0 16px rgba(45, 212, 197, 0.6);
  --glow-coral: 0 0 16px rgba(255, 138, 101, 0.6);
  --glow-magenta: 0 0 16px rgba(224, 73, 158, 0.6);
  --glow-gold: 0 0 16px rgba(184, 145, 46, 0.55);
  --danger: var(--destructive);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

@layer base {
  * {
    @apply border-border outline-ring/50;
  }
  body {
    @apply bg-background text-foreground;
  }
  html {
    @apply font-sans;
  }
}
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds (CSS custom properties aren't typechecked, so referencing `--font-inter`/`--font-fraunces` before they exist doesn't break the build). There is nothing to see visually yet — without Task 2's hardcoded `dark` class on `<html>`, none of these new `.dark` values are active in the browser. Just confirm the build passes, then continue to Task 2.

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/globals.css
git commit -m "Rewrite admin panel tokens to Midnight Luxury dark palette"
```

---

### Task 2: Typography — Fraunces + Inter, hardcode dark

**Files:**
- Modify: `admin/src/app/layout.tsx`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/app/layout.tsx` with:

```tsx
import type { Metadata } from "next";
import { Fraunces, Inter, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Create Next App",
  description: "Generated by create next app",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`dark ${fraunces.variable} ${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {children}
        <Toaster />
      </body>
    </html>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Run `npm run dev`, open http://localhost:3000/dashboard (log in first if needed). Confirm the background is near-black, and any Card/Dialog/Sheet title (e.g. open "New product" on `/products`) renders in the Fraunces serif while body text is Inter.

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/layout.tsx
git commit -m "Swap Geist Sans for Fraunces + Inter, hardcode dark theme"
```

---

### Task 3: Apply Fraunces to page headings

**Files:**
- Modify: `admin/src/components/topbar.tsx`
- Modify: `admin/src/app/login/page.tsx`
- Modify: `admin/src/app/(admin)/orders/page.tsx`
- Modify: `admin/src/app/(admin)/products/page.tsx`
- Modify: `admin/src/app/(admin)/users/page.tsx`
- Modify: `admin/src/app/(admin)/users/[id]/page.tsx`

(`dashboard/page.tsx`'s heading is handled in Task 9, which rewrites that file anyway.)

- [ ] **Step 1: Topbar page title**

In `admin/src/components/topbar.tsx`, replace:

```tsx
      <h2 className="text-lg font-semibold text-[var(--ink)]">{title}</h2>
```

with:

```tsx
      <h2 className="font-heading text-lg font-semibold text-[var(--ink)]">{title}</h2>
```

- [ ] **Step 2: Login heading**

In `admin/src/app/login/page.tsx`, replace:

```tsx
        <h1 className="text-2xl font-semibold text-[var(--ink)]">Admin sign-in</h1>
```

with:

```tsx
        <h1 className="font-heading text-2xl font-semibold text-[var(--ink)]">Admin sign-in</h1>
```

- [ ] **Step 3: Orders headings**

In `admin/src/app/(admin)/orders/page.tsx`, replace:

```tsx
        <h1 className="text-2xl font-semibold text-[var(--ink)]">Needs review</h1>
```

with:

```tsx
        <h1 className="font-heading text-2xl font-semibold text-[var(--ink)]">Needs review</h1>
```

and replace both occurrences of:

```tsx
        <h2 className="text-lg font-medium text-[var(--ink)]">⚡ Auto-approved</h2>
```

```tsx
        <h2 className="text-lg font-medium text-[var(--ink)]">✓ Reviewed by you</h2>
```

with:

```tsx
        <h2 className="font-heading text-lg font-medium text-[var(--ink)]">⚡ Auto-approved</h2>
```

```tsx
        <h2 className="font-heading text-lg font-medium text-[var(--ink)]">✓ Reviewed by you</h2>
```

- [ ] **Step 4: Products heading**

In `admin/src/app/(admin)/products/page.tsx`, replace:

```tsx
        <h1 className="text-2xl font-semibold text-[var(--ink)]">Products</h1>
```

with:

```tsx
        <h1 className="font-heading text-2xl font-semibold text-[var(--ink)]">Products</h1>
```

- [ ] **Step 5: Users heading**

In `admin/src/app/(admin)/users/page.tsx`, replace:

```tsx
      <h1 className="text-2xl font-semibold text-[var(--ink)]">Users</h1>
```

with:

```tsx
      <h1 className="font-heading text-2xl font-semibold text-[var(--ink)]">Users</h1>
```

- [ ] **Step 6: User detail headings**

In `admin/src/app/(admin)/users/[id]/page.tsx`, replace:

```tsx
      <h1 className="text-2xl font-semibold text-[var(--ink)]">
        {user?.first_name} @{user?.username}
      </h1>
```

with:

```tsx
      <h1 className="font-heading text-2xl font-semibold text-[var(--ink)]">
        {user?.first_name} @{user?.username}
      </h1>
```

and replace both occurrences of `<h2 className="text-lg font-medium text-[var(--ink)]">Ledger</h2>` and
`<h2 className="text-lg font-medium text-[var(--ink)]">Deposits</h2>` with
`<h2 className="font-heading text-lg font-medium text-[var(--ink)]">Ledger</h2>` and
`<h2 className="font-heading text-lg font-medium text-[var(--ink)]">Deposits</h2>` respectively.

- [ ] **Step 7: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Browse each page (`/login`, `/orders`, `/products`, `/users`, `/users/<id>`) and confirm every h1/h2 and the topbar title render in Fraunces.

- [ ] **Step 8: Commit**

```bash
git add admin/src/components/topbar.tsx admin/src/app/login/page.tsx "admin/src/app/(admin)/orders/page.tsx" "admin/src/app/(admin)/products/page.tsx" "admin/src/app/(admin)/users/page.tsx" "admin/src/app/(admin)/users/[id]/page.tsx"
git commit -m "Apply Fraunces display font to all page headings"
```

---

### Task 4: Remove next-themes (now fully unused)

**Files:**
- Modify: `admin/src/components/ui/sonner.tsx`
- Modify: `admin/package.json`

- [ ] **Step 1: Hardcode dark theme in the toaster**

Replace the full contents of `admin/src/components/ui/sonner.tsx` with:

```tsx
"use client"

import { Toaster as Sonner, type ToasterProps } from "sonner"
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="dark"
      className="toaster group"
      icons={{
        success: (
          <CircleCheckIcon className="size-4" />
        ),
        info: (
          <InfoIcon className="size-4" />
        ),
        warning: (
          <TriangleAlertIcon className="size-4" />
        ),
        error: (
          <OctagonXIcon className="size-4" />
        ),
        loading: (
          <Loader2Icon className="size-4 animate-spin" />
        ),
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          toast: "cn-toast",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
```

- [ ] **Step 2: Remove the dependency**

In `admin/package.json`, remove this line from `dependencies`:

```json
    "next-themes": "^0.4.6",
```

- [ ] **Step 3: Reinstall and verify**

Run: `cd admin && npm install && npm run build`
Expected: `npm install` removes `next-themes` from `node_modules` and `package-lock.json`; `npm run build` succeeds.

- [ ] **Step 4: Commit**

```bash
git add admin/src/components/ui/sonner.tsx admin/package.json admin/package-lock.json
git commit -m "Remove now-unused next-themes dependency, hardcode dark toaster"
```

---

### Task 5: Tighten motion timing for a snappier feel

**Files:**
- Modify: `admin/src/lib/motion.ts`

- [ ] **Step 1: Trim page-transition duration and stagger delay**

In `admin/src/lib/motion.ts`, replace:

```ts
export const DURATION = {
  micro: 0.2,   // button/toggle/hover — 150-300ms band
  panel: 0.35,  // card/sheet/modal enter
  page: 0.4,    // page-level transitions, upper bound
} as const;
```

with:

```ts
export const DURATION = {
  micro: 0.2,   // button/toggle/hover — 150-300ms band
  panel: 0.3,   // card/sheet/modal enter
  page: 0.28,   // page-level transitions — snappier admin feel
} as const;
```

and replace:

```ts
export const STAGGER_CHILD_DELAY = 0.04; // 40ms, within the 30-50ms band
```

with:

```ts
export const STAGGER_CHILD_DELAY = 0.03; // 30ms — lower bound of the 30-50ms band
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Run `npm run dev`, click between sidebar links. Confirm the page transition and the dashboard KPI-row stagger both feel noticeably snappier than before, without feeling instant/jarring.

- [ ] **Step 3: Commit**

```bash
git add admin/src/lib/motion.ts
git commit -m "Tighten page-transition and stagger timing"
```

---

### Task 6: queries.ts — per-day sparkline series

**Files:**
- Modify: `admin/src/lib/queries.ts`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/lib/queries.ts` with:

```ts
import { supabaseServer } from "./supabase-server";

/** Returns the 7 calendar-day strings (YYYY-MM-DD) ending today, oldest first. */
function last7Days(): string[] {
  const days: string[] = [];
  for (let i = 6; i >= 0; i--) {
    days.push(new Date(Date.now() - i * 24 * 60 * 60 * 1000).toISOString().slice(0, 10));
  }
  return days;
}

/**
 * Buckets rows by their created_at day and sums valueOf(row) per day, returning
 * one number per day of last7Days() (0 for days with no matching rows) so every
 * sparkline series has the same length regardless of data gaps.
 */
function dailySeries(
  rows: { created_at: string }[],
  valueOf: (row: { created_at: string; amount?: number | null; amount_usdt?: number | null }) => number
): number[] {
  const byDay = new Map<string, number>();
  for (const row of rows) {
    const day = row.created_at.slice(0, 10);
    byDay.set(day, (byDay.get(day) ?? 0) + valueOf(row));
  }
  return last7Days().map((day) => byDay.get(day) ?? 0);
}

export async function getDashboardStats() {
  const db = supabaseServer();

  const since7d = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();

  const [{ data: recentOrders }, { data: lowStock }, { count: pendingCount }] = await Promise.all([
    db
      .from("orders")
      .select("created_at, amount, amount_usdt, status, product_name")
      .gte("created_at", since7d),
    db.from("products").select("id, name, stock").eq("active", 1).gte("stock", 0).lte("stock", 5),
    db.from("orders").select("id", { count: "exact", head: true }).eq("status", "pending_review"),
  ]);

  const approved = (recentOrders ?? []).filter((o) => o.status === "approved");
  const revenueInr = approved.reduce((sum, o) => sum + Number(o.amount ?? 0), 0);
  const revenueUsdt = approved.reduce((sum, o) => sum + Number(o.amount_usdt ?? 0), 0);

  const revenueInrTrend = dailySeries(approved, (o) => Number(o.amount ?? 0));
  const revenueUsdtTrend = dailySeries(approved, (o) => Number(o.amount_usdt ?? 0));
  const orderCountTrend = dailySeries(approved, () => 1);

  const trend = last7Days().map((date, i) => ({ date, usdt: revenueUsdtTrend[i] }));

  return {
    revenueInr,
    revenueUsdt,
    orderCount7d: approved.length,
    pendingCount: pendingCount ?? 0,
    lowStock: lowStock ?? [],
    trend,
    revenueInrTrend,
    revenueUsdtTrend,
    orderCountTrend,
    topProducts: topProductsByRevenue(approved),
  };
}

/**
 * Groups approved orders by product name and returns the top 4 by USDT revenue,
 * folding everything past the top 4 into an "Other" bucket (omitted if zero).
 */
export function topProductsByRevenue(
  approved: { product_name: string; amount_usdt: number }[]
): { name: string; value: number }[] {
  const byProduct = new Map<string, number>();
  for (const o of approved) {
    byProduct.set(o.product_name, (byProduct.get(o.product_name) ?? 0) + Number(o.amount_usdt ?? 0));
  }
  const sorted = [...byProduct.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, 4).map(([name, value]) => ({ name, value }));
  const otherValue = sorted.slice(4).reduce((sum, [, v]) => sum + v, 0);
  return otherValue > 0 ? [...top, { name: "Other", value: otherValue }] : top;
}

export async function getPendingOrderCount(): Promise<number> {
  const db = supabaseServer();
  const { count } = await db
    .from("orders")
    .select("id", { count: "exact", head: true })
    .eq("status", "pending_review");
  return count ?? 0;
}
```

- [ ] **Step 2: Verify the day-bucketing logic manually**

This is the one piece of non-trivial logic in this task, so check it by hand before wiring it into the UI. In `cd admin`, run:

```bash
node -e "
function last7Days() {
  const days = [];
  for (let i = 6; i >= 0; i--) days.push(new Date(Date.now() - i * 86400000).toISOString().slice(0, 10));
  return days;
}
function dailySeries(rows, valueOf) {
  const byDay = new Map();
  for (const row of rows) byDay.set(row.created_at.slice(0, 10), (byDay.get(row.created_at.slice(0, 10)) ?? 0) + valueOf(row));
  return last7Days().map((day) => byDay.get(day) ?? 0);
}
const today = new Date().toISOString().slice(0, 10);
const rows = [{ created_at: today + 'T10:00:00Z', amount_usdt: 5 }, { created_at: today + 'T14:00:00Z', amount_usdt: 3 }];
const series = dailySeries(rows, (r) => r.amount_usdt);
console.log(series);
console.log('length is 7:', series.length === 7);
console.log('today (last) is 8:', series[6] === 8);
"
```

Expected output: a 7-element array ending in `8` (5+3 summed for today), confirming same-day rows aggregate correctly and the series always has 7 entries.

- [ ] **Step 3: Typecheck**

Run: `cd admin && npm run build`
Expected: succeeds (the new return fields aren't consumed by any page yet, so nothing breaks; Task 9 wires them in).

- [ ] **Step 4: Commit**

```bash
git add admin/src/lib/queries.ts
git commit -m "Add per-day revenue/order-count series for KPI sparklines"
```

---

### Task 7: KpiSparkline component

**Files:**
- Create: `admin/src/components/kpi-sparkline.tsx`

- [ ] **Step 1: Create the component**

```tsx
"use client";
import { AreaChart, Area, ResponsiveContainer } from "recharts";

export function KpiSparkline({ data, color }: { data: number[]; color: string }) {
  const points = data.map((value, i) => ({ i, value }));
  return (
    <ResponsiveContainer width="100%" height={32}>
      <AreaChart data={points} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          fill={color}
          fillOpacity={0.15}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
```

`isAnimationActive={false}` — this is a small decorative trend indicator inside a KPI card, not a focal chart; per the frequency gate (viewed on every dashboard load) it should render its data immediately rather than animate in.

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds (not imported anywhere yet).

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/kpi-sparkline.tsx
git commit -m "Add KpiSparkline component"
```

---

### Task 8: KpiCard — gold accent + optional sparkline

**Files:**
- Modify: `admin/src/components/kpi-card.tsx`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/components/kpi-card.tsx` with:

```tsx
"use client";
import { motion } from "motion/react";
import { listItem, GLOW_DELAY, DURATION } from "@/lib/motion";
import { KpiSparkline } from "@/components/kpi-sparkline";

type Accent = "violet" | "teal" | "coral" | "magenta" | "gold";

const ACCENT_GRADIENT: Record<Accent, string> = {
  violet: "linear-gradient(135deg, var(--brand-violet-from), var(--brand-violet-to))",
  teal: "linear-gradient(135deg, var(--brand-teal-from), var(--brand-teal-to))",
  coral: "linear-gradient(135deg, var(--brand-coral-from), var(--brand-coral-to))",
  magenta: "linear-gradient(135deg, var(--brand-magenta-from), var(--brand-magenta-to))",
  gold: "linear-gradient(135deg, var(--brand-gold-from), var(--brand-gold-to))",
};

const ACCENT_GLOW: Record<Accent, string> = {
  violet: "var(--glow-violet)",
  teal: "var(--glow-teal)",
  coral: "var(--glow-coral)",
  magenta: "var(--glow-magenta)",
  gold: "var(--glow-gold)",
};

const ACCENT_SOLID: Record<Accent, string> = {
  violet: "var(--brand-violet-to)",
  teal: "var(--brand-teal-to)",
  coral: "var(--brand-coral-to)",
  magenta: "var(--brand-magenta-to)",
  gold: "var(--brand-gold-to)",
};

export function KpiCard({
  label,
  value,
  sublabel,
  icon,
  accent,
  sparkline,
}: {
  label: string;
  value: string;
  sublabel?: string;
  icon: React.ReactNode;
  accent: Accent;
  sparkline?: number[];
}) {
  return (
    <motion.div
      variants={listItem}
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5"
    >
      <div className="relative mb-3 size-9">
        <div
          className="absolute inset-0 rounded-lg"
          style={{ background: ACCENT_GRADIENT[accent] }}
        />
        <motion.div
          className="absolute inset-0 rounded-lg"
          style={{ boxShadow: ACCENT_GLOW[accent] }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: GLOW_DELAY, duration: DURATION.micro }}
        />
        <div className="relative flex size-9 items-center justify-center">
          {icon}
        </div>
      </div>
      <p className="text-sm text-[var(--ink-muted)]">{label}</p>
      <p className="mt-2 text-3xl font-semibold tabular-nums text-[var(--ink)]">{value}</p>
      {sublabel && <p className="mt-1 text-xs text-[var(--ink-muted)]">{sublabel}</p>}
      {sparkline && sparkline.some((v) => v > 0) && (
        <div className="mt-2">
          <KpiSparkline data={sparkline} color={ACCENT_SOLID[accent]} />
        </div>
      )}
    </motion.div>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds — `"gold"` only widens the existing `Accent` union so `dashboard/page.tsx` and `users/page.tsx`'s existing `accent="violet"`/`"teal"`/`"coral"`/`"magenta"` calls still typecheck, and `sparkline` is optional so call sites without it are unaffected. If it fails, stop and investigate before continuing.

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/kpi-card.tsx
git commit -m "Add gold accent option and optional sparkline to KpiCard"
```

---

### Task 9: Dashboard page — wire gold accent + sparklines

**Files:**
- Modify: `admin/src/app/(admin)/dashboard/page.tsx`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/app/(admin)/dashboard/page.tsx` with:

```tsx
import { DollarSign, Wallet, Receipt, Clock } from "lucide-react";
import { getDashboardStats } from "@/lib/queries";
import { KpiCard } from "@/components/kpi-card";
import { DashboardChart } from "@/components/dashboard-chart";
import { RevenueByProductDonut } from "@/components/revenue-by-product-donut";
import { KpiRow } from "@/components/kpi-row";
import { LiveRefresh } from "@/components/live-refresh";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const stats = await getDashboardStats();

  return (
    <div className="flex flex-col gap-6">
      <LiveRefresh watch={["orders", "products", "deposits"]} />
      <h1 className="font-heading text-2xl font-semibold text-[var(--ink)]">Dashboard</h1>

      {stats.lowStock.length > 0 && (
        <div className="rounded-lg border border-[var(--brand-coral-to)]/30 bg-[var(--brand-coral-to)]/10 px-4 py-3 text-sm text-[var(--ink)]">
          Low stock: {stats.lowStock.map((p) => `${p.name} (${p.stock})`).join(", ")}
        </div>
      )}

      <KpiRow>
        <KpiCard
          label="Revenue (7d, INR)"
          value={`₹${stats.revenueInr.toFixed(0)}`}
          icon={<DollarSign className="size-5 text-white" />}
          accent="gold"
          sparkline={stats.revenueInrTrend}
        />
        <KpiCard
          label="Revenue (7d, USDT)"
          value={`$${stats.revenueUsdt.toFixed(2)}`}
          icon={<Wallet className="size-5 text-white" />}
          accent="violet"
          sparkline={stats.revenueUsdtTrend}
        />
        <KpiCard
          label="Orders (7d)"
          value={String(stats.orderCount7d)}
          icon={<Receipt className="size-5 text-white" />}
          accent="teal"
          sparkline={stats.orderCountTrend}
        />
        <KpiCard
          label="Pending approval"
          value={String(stats.pendingCount)}
          sublabel="Needs review"
          icon={<Clock className="size-5 text-white" />}
          accent="magenta"
        />
      </KpiRow>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.6fr_1fr]">
        <DashboardChart data={stats.trend} />
        <RevenueByProductDonut data={stats.topProducts} />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Run `npm run dev`, open `/dashboard`. Confirm the Revenue (INR) card has a gold gradient icon chip (not violet), and the three revenue/order KPI cards each show a small sparkline beneath the value (if there's been any approved-order activity in the last 7 days — otherwise no sparkline renders, which is correct per the empty-data guard in `KpiCard`).

- [ ] **Step 3: Commit**

```bash
git add "admin/src/app/(admin)/dashboard/page.tsx"
git commit -m "Wire gold accent and sparklines into dashboard KPIs"
```

---

### Task 10: DashboardChart — gradient-filled area chart

**Files:**
- Modify: `admin/src/components/dashboard-chart.tsx`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/components/dashboard-chart.tsx` with:

```tsx
"use client";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export function DashboardChart({ data }: { data: { date: string; usdt: number }[] }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
      <p className="mb-4 text-sm font-medium text-[var(--ink)]">Revenue trend (USDT)</p>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="revenueTrendFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--brand-violet-to)" stopOpacity={0.5} />
              <stop offset="100%" stopColor="var(--brand-violet-to)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="date" stroke="var(--ink-muted)" fontSize={12} />
          <YAxis stroke="var(--ink-muted)" fontSize={12} />
          <Tooltip
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }}
          />
          <Area
            type="monotone"
            dataKey="usdt"
            stroke="var(--brand-violet-to)"
            strokeWidth={2}
            fill="url(#revenueTrendFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Run `npm run dev`, open `/dashboard`. Confirm the revenue trend chart now shows a violet gradient fill under the line instead of a bare line.

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/dashboard-chart.tsx
git commit -m "Upgrade dashboard revenue chart to gradient-filled area chart"
```

---

### Task 11: RevenueByProductDonut — retuned colors

**Files:**
- Modify: `admin/src/components/revenue-by-product-donut.tsx`

- [ ] **Step 1: Retune the color order**

In `admin/src/components/revenue-by-product-donut.tsx`, replace:

```tsx
const COLORS = [
  "var(--brand-violet-to)",
  "var(--brand-teal-to)",
  "var(--brand-coral-to)",
  "var(--brand-magenta-to)",
  "var(--ink-muted)",
];
```

with:

```tsx
const COLORS = [
  "var(--brand-gold-to)",
  "var(--brand-violet-to)",
  "var(--brand-teal-to)",
  "var(--brand-coral-to)",
  "var(--brand-magenta-to)",
];
```

(`topProductsByRevenue` in `queries.ts` already sorts by revenue descending, so index 0 — gold — always lands on the single top-revenue product, matching "gold reserved for the one premium moment.")

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Open `/dashboard` with at least one approved order in the last 7 days; confirm the donut's largest-revenue slice is gold.

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/revenue-by-product-donut.tsx
git commit -m "Retune revenue-by-product donut colors, gold for top product"
```

---

### Task 12: Route loading skeletons

**Files:**
- Create: `admin/src/app/(admin)/dashboard/loading.tsx`
- Create: `admin/src/app/(admin)/orders/loading.tsx`
- Create: `admin/src/app/(admin)/products/loading.tsx`
- Create: `admin/src/app/(admin)/users/loading.tsx`

- [ ] **Step 1: Dashboard skeleton**

```tsx
import { Skeleton } from "@/components/ui/skeleton";

export default function DashboardLoading() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-8 w-40" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-32 rounded-xl" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.6fr_1fr]">
        <Skeleton className="h-64 rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Orders skeleton**

```tsx
import { Skeleton } from "@/components/ui/skeleton";

export default function OrdersLoading() {
  return (
    <div className="flex flex-col gap-8">
      <div>
        <Skeleton className="h-8 w-40" />
        <div className="mt-4 flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Products skeleton**

```tsx
import { Skeleton } from "@/components/ui/skeleton";

export default function ProductsLoading() {
  return (
    <div>
      <Skeleton className="h-8 w-32" />
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-36 rounded-xl" />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Users skeleton**

```tsx
import { Skeleton } from "@/components/ui/skeleton";

export default function UsersLoading() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-8 w-24" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-24 rounded-xl" />
      </div>
      <Skeleton className="h-96 rounded-xl" />
    </div>
  );
}
```

- [ ] **Step 5: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Run `npm run dev`. With network throttled (Chrome devtools → Network → Slow 3G) or by adding a temporary `await new Promise(r => setTimeout(r, 1000))` at the top of one page's data fetch, navigate to each of `/dashboard`, `/orders`, `/products`, `/users` and confirm the matching skeleton shows briefly instead of a blank page. Remove any temporary delay you added before committing.

- [ ] **Step 6: Commit**

```bash
git add "admin/src/app/(admin)/dashboard/loading.tsx" "admin/src/app/(admin)/orders/loading.tsx" "admin/src/app/(admin)/products/loading.tsx" "admin/src/app/(admin)/users/loading.tsx"
git commit -m "Add route-level loading skeletons for instant navigation feedback"
```

---

### Task 13: Optimistic OrderRow actions

**Files:**
- Modify: `admin/src/components/order-row.tsx`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/components/order-row.tsx` with:

```tsx
"use client";
import { useState, useTransition } from "react";
import { motion } from "motion/react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { approveOrder, rejectOrder } from "@/app/(admin)/orders/actions";
import { PRESS_SCALE, DURATION } from "@/lib/motion";

const STATUS_COLOR: Record<string, string> = {
  approved: "bg-[var(--success)]/15 text-[var(--success)]",
  rejected: "bg-[var(--danger)]/15 text-[var(--danger)]",
  pending_review: "bg-[var(--warning)]/15 text-[var(--warning)]",
  created: "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]",
};

export function OrderRow({ order, actionable }: { order: any; actionable: boolean }) {
  const [pending, startTransition] = useTransition();
  const [optimisticStatus, setOptimisticStatus] = useState<string | null>(null);
  const status = optimisticStatus ?? order.status;
  const resolved = optimisticStatus !== null;

  function handle(action: "approve" | "reject") {
    setOptimisticStatus(action === "approve" ? "approved" : "rejected");
    startTransition(async () => {
      try {
        if (action === "approve") await approveOrder(order.id);
        else await rejectOrder(order.id, "Rejected from admin panel");
      } catch (e) {
        setOptimisticStatus(null);
        toast.error(e instanceof Error ? e.message : `Failed to ${action} order`);
      }
    });
  }

  return (
    <div
      className={`flex items-center justify-between rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 py-3 transition-all hover:-translate-y-0.5 hover:shadow-md ${resolved ? "opacity-60" : ""}`}
    >
      <div>
        <p className="text-sm font-medium text-[var(--ink)]">
          #{order.ref} — {order.product_name}
        </p>
        <p className="text-xs text-[var(--ink-muted)]">
          {order.username || order.user_id} · {order.method} · {order.utr}
        </p>
      </div>
      <div className="flex items-center gap-3">
        {!actionable && order.status !== "created" && (
          order.method === "Wallet" ? (
            <Badge className="bg-[var(--brand-violet-to)]/15 text-[var(--brand-violet-to)]">⚡ Auto</Badge>
          ) : (
            <Badge className="bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]">✓ Reviewed</Badge>
          )
        )}
        <Badge className={STATUS_COLOR[status]}>{status}</Badge>
        {actionable && !resolved && (
          <>
            <motion.div whileTap={{ scale: PRESS_SCALE }} transition={{ duration: DURATION.micro }}>
              <Button size="sm" disabled={pending} onClick={() => handle("approve")}>
                Approve
              </Button>
            </motion.div>
            <motion.div whileTap={{ scale: PRESS_SCALE }} transition={{ duration: DURATION.micro }}>
              <Button size="sm" variant="destructive" disabled={pending} onClick={() => handle("reject")}>
                Reject
              </Button>
            </motion.div>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Run `npm run dev`, open `/orders` with at least one pending order. Click Approve (or Reject) and confirm: the buttons disappear and the row dims **immediately** (before the network round-trip completes), the badge shows the new status right away, and the row settles into its final state once `revalidatePath` catches up — no flash of the old state. To check the failure path, temporarily change `approveOrder`'s `.eq("status", "pending_review")` filter value to something that never matches, click Approve, confirm the row reverts to its actionable state with an error toast, then revert that temporary change.

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/order-row.tsx
git commit -m "Add optimistic UI to order approve/reject actions"
```

---

### Task 14: Optimistic ProductCard stock toggle

**Files:**
- Modify: `admin/src/components/product-card.tsx`

- [ ] **Step 1: Replace the full file**

Replace the full contents of `admin/src/components/product-card.tsx` with:

```tsx
"use client";
import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { setProductActive } from "@/app/(admin)/products/actions";
import { ProductFormDialog } from "@/components/product-form-dialog";

export function ProductCard({ product, unusedKeys }: { product: any; unusedKeys: number }) {
  const [editing, setEditing] = useState(false);
  const [pending, startTransition] = useTransition();
  const [optimisticActive, setOptimisticActive] = useState<boolean | null>(null);
  const active = optimisticActive ?? product.active === 1;

  const lowStock = product.stock !== -1 && product.stock <= 5;
  const statusLabel = !active ? "Inactive" : lowStock ? "Low stock" : "In stock";
  const statusClass = !active
    ? "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]"
    : lowStock
      ? "bg-[var(--brand-coral-to)]/15 text-[var(--brand-coral-to)]"
      : "bg-[var(--brand-teal-to)]/15 text-[var(--brand-teal-to)]";

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5 transition-transform hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium text-[var(--ink)]">{product.name}</p>
          <p className="text-sm text-[var(--ink-muted)]">
            ₹{product.price_inr} · ${product.price} · stock {product.stock === -1 ? "∞" : product.stock}
          </p>
        </div>
        <Switch
          checked={active}
          disabled={pending}
          onCheckedChange={(checked) => {
            setOptimisticActive(checked);
            startTransition(async () => {
              try {
                await setProductActive(product.id, checked);
              } catch (e) {
                setOptimisticActive(null);
                toast.error(e instanceof Error ? e.message : "Failed to update product");
              }
            });
          }}
        />
      </div>
      <div className="mt-3 flex items-center justify-between">
        <Badge className={statusClass}>{statusLabel}</Badge>
        <p className="text-xs text-[var(--ink-muted)]">{unusedKeys} unused keys</p>
      </div>
      <Button size="sm" variant="outline" className="mt-3" onClick={() => setEditing(true)}>
        Edit
      </Button>
      {editing && <ProductFormDialog product={product} onClose={() => setEditing(false)} />}
    </div>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd admin && npm run build`
Expected: succeeds. Run `npm run dev`, open `/products`. Toggle a product's active switch and confirm it flips **immediately** rather than waiting on the round-trip, and the status badge ("In stock"/"Low stock"/"Inactive") updates in lockstep with the switch.

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/product-card.tsx
git commit -m "Add optimistic UI to product active-status toggle"
```

---

### Task 15: Final verification pass

**Files:** none (verification only)

- [ ] **Step 1: Full build**

Run: `cd admin && npm run build`
Expected: succeeds with no TypeScript or lint-blocking errors.

- [ ] **Step 2: Manual smoke test, every page**

Run: `npm run dev`, then in a browser visit, in order: `/login`, `/dashboard`, `/orders`, `/products`, `/users`, `/users/<some id>`. For each, confirm:
- No console errors.
- Near-black Midnight Luxury background, Fraunces headings, Inter body/data text throughout.
- Sidebar active nav pill and topbar render correctly against the new palette (no leftover light-mode-calibrated low-contrast text — spot-check `--ink-muted` labels).
- Dashboard: gold-accented Revenue (INR) KPI card with a sparkline, gradient-filled area chart, donut with a gold top slice.
- Orders: approve/reject feels instant (Task 13's optimistic behavior).
- Products: stock toggle feels instant (Task 14's optimistic behavior).
- Navigating between pages briefly shows the matching skeleton (Task 12) before content, and the page-transition fade feels snappier than before this redesign.

- [ ] **Step 3: Reduced-motion check**

In devtools, enable "Emulate CSS prefers-reduced-motion: reduce" (Chrome DevTools → Rendering tab), reload `/dashboard`. Confirm the existing global override in `globals.css` still collapses all animations (glow fade-in, page transitions, KPI stagger) to near-instant — no new code needed, just confirming the pre-existing safeguard still covers the retuned motion constants.

- [ ] **Step 4: Confirm no dead light-mode code remains**

Grep the admin panel for now-orphaned light-mode references:

```bash
cd admin && grep -rn "next-themes\|useTheme" src/ || echo "clean"
```

Expected: `clean` (no matches) — Task 4 removed the only consumer.

- [ ] **Step 5: Deploy**

If everything above checks out, redeploy to Vercel production the same way the initial deploy was done:

```bash
cd admin && vercel --prod --yes
```

- [ ] **Step 6: Final commit (if any fixes were made during verification)**

```bash
git add -A
git commit -m "Fix issues found during Midnight Luxury redesign verification pass"
```
