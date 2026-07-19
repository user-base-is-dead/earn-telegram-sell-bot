# Admin Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Next.js admin panel described in the design spec: a live dashboard, product/order/wallet management, and Telegram-authenticated single-admin login, reading the Postgres DB from the `2026-07-16-postgres-migration` plan.

**Architecture:** A new `admin/` Next.js (App Router) app, deployed separately from the Python bot. All data access happens server-side with the Supabase service-role key (no RLS, no client-exposed keys). Auth is the Telegram Login Widget verified by one Route Handler, backed by a signed httpOnly session cookie. Live updates flow through a server-side Supabase Realtime subscription relayed to the browser over Server-Sent Events.

**Tech Stack:** Next.js 15 (App Router) + TypeScript, Tailwind CSS v4, shadcn/ui, Motion (the current name for Framer Motion), Recharts, `@supabase/supabase-js`, `jose` (session cookie signing).

**Prerequisite:** The `2026-07-16-postgres-migration` plan must be complete — this panel reads the same Postgres schema created there (`docs/superpowers/plans/2026-07-16-postgres-migration.md`, Task 3's `_SCHEMA_SQL`).

---

### Design system baseline (applies to every UI task below)

Motion research (Motion/shadcn best practices, mid-2026) and the `impeccable`/`ui-ux-pro-max`
guidelines converge on the same rules — every task that adds a component follows these instead
of restating them:

- **Timing tokens:** micro-interactions (button press, toggle, hover) 150–300ms; page/section
  transitions 400ms max; exits run at ~60–70% of their matching enter duration.
- **Only animate `transform`/`opacity`** (GPU-accelerated) — never `width`/`height`/`top`/`left`.
- **Springs for interactive UI** (buttons, modals, tabs), **eased curves for decorative/sequenced
  motion** (page transitions, list reveals) — `ease-out-quart`/`quint`, no bounce/elastic.
- **Hover scale 1.02–1.06, press/tap scale 0.94–0.98.**
- **Stagger list/grid entrances 30–50ms per item.** Don't animate every section with the same
  identical reflex — vary by what's actually being revealed.
- **`prefers-reduced-motion: reduce` must be respected everywhere** — swap to instant/crossfade,
  never just "less motion."
- **Use Tailwind transitions for anything CSS can express** (zero runtime cost); reach for Motion
  only for interaction-level sequencing CSS can't do (shared-element/layout transitions,
  orchestrated stagger, gesture-driven drag).
- Colors in OKLCH, semantic tokens (`--bg`, `--surface`, `--ink`, `--accent`, `--muted`), dark
  mode designed alongside light (not inverted). No gradient text, no side-stripe card borders, no
  border+wide-shadow combos, no 24px+ card corner radii, no tiny uppercase-tracked eyebrows on
  every section — these read as generic AI-generated UI, and the whole point of this panel is to
  feel premium and deliberate, not templated.

---

### Task 1: Scaffold the Next.js app

**Files:**
- Create: `admin/` (new Next.js project root)

- [ ] **Step 1: Create the project**

Run from the repo root:

```bash
npx create-next-app@latest admin --typescript --tailwind --app --src-dir --import-alias "@/*" --eslint
cd admin
```

Accept defaults for anything else prompted.

- [ ] **Step 2: Install dependencies**

```bash
npm install @supabase/supabase-js motion recharts jose @radix-ui/react-icons lucide-react clsx tailwind-merge class-variance-authority
npx shadcn@latest init
```

For `shadcn@latest init`, choose: New York style, neutral base color (we override with our own
OKLCH tokens in Task 2), CSS variables yes.

- [ ] **Step 3: Add the shadcn components this panel needs**

```bash
npx shadcn@latest add button card table badge dialog sheet tabs input label select toast skeleton dropdown-menu separator
```

- [ ] **Step 4: `.env.local` and `.gitignore`**

Create `admin/.env.local.example`:

```
NEXT_PUBLIC_SITE_URL=http://localhost:3000
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_BOT_USERNAME=your_bot_username
ADMIN_TELEGRAM_IDS=123456789
SESSION_SECRET=generate-a-long-random-string
```

Confirm `admin/.gitignore` (created by `create-next-app`) already excludes `.env*.local` — it
does by default, verify and move on.

- [ ] **Step 5: Verify the scaffold runs**

Run: `npm run dev` (from `admin/`)
Expected: dev server starts on `http://localhost:3000`, default Next.js page loads.

- [ ] **Step 6: Commit**

```bash
git add admin/
git commit -m "Scaffold Next.js admin panel with shadcn/ui, Motion, Recharts"
```

---

### Task 2: Design tokens (OKLCH, dark/light, motion tokens)

**Files:**
- Modify: `admin/src/app/globals.css`
- Create: `admin/src/lib/motion.ts`

- [ ] **Step 1: Replace the default color tokens with an OKLCH admin-tool palette**

Product register (per the design-system baseline above: this is a tool, not a marketing page —
restrained color strategy, dark-first since it's an always-on ops dashboard checked at odd
hours). In `admin/src/app/globals.css`, replace the `:root` and `.dark` variable blocks shadcn
generated with:

```css
:root {
  --bg: oklch(98% 0.003 250);
  --surface: oklch(100% 0 0);
  --ink: oklch(22% 0.02 250);
  --ink-muted: oklch(45% 0.02 250);
  --accent: oklch(62% 0.19 260);
  --accent-ink: oklch(98% 0.01 260);
  --border: oklch(90% 0.005 250);
  --danger: oklch(58% 0.22 25);
  --success: oklch(62% 0.16 150);
  --warning: oklch(75% 0.16 80);
}

.dark {
  --bg: oklch(19% 0.012 260);
  --surface: oklch(24% 0.014 260);
  --ink: oklch(94% 0.01 260);
  --ink-muted: oklch(68% 0.02 260);
  --accent: oklch(72% 0.17 260);
  --accent-ink: oklch(15% 0.01 260);
  --border: oklch(32% 0.015 260);
  --danger: oklch(68% 0.2 25);
  --success: oklch(72% 0.15 150);
  --warning: oklch(80% 0.15 80);
}
```

Map these onto the Tailwind theme by keeping shadcn's `--background`/`--foreground`/etc
variable names as aliases pointing at the tokens above (in the same `:root`/`.dark` blocks):

```css
:root {
  --background: var(--bg);
  --foreground: var(--ink);
  --card: var(--surface);
  --card-foreground: var(--ink);
  --primary: var(--accent);
  --primary-foreground: var(--accent-ink);
  --muted-foreground: var(--ink-muted);
  --border: var(--border);
  --destructive: var(--danger);
}
```

- [ ] **Step 2: Motion tokens**

Create `admin/src/lib/motion.ts`:

```typescript
// Shared motion tokens so every animated component in the panel moves with the
// same rhythm. See docs/superpowers/plans/2026-07-16-admin-panel.md for the
// research this is based on.
export const DURATION = {
  micro: 0.2,   // button/toggle/hover — 150-300ms band
  panel: 0.35,  // card/sheet/modal enter
  page: 0.4,    // page-level transitions, upper bound
} as const;

export const EXIT_RATIO = 0.65; // exits run at ~65% of their enter duration

export const EASE_OUT_QUART: [number, number, number, number] = [0.25, 1, 0.5, 1];

export const SPRING_INTERACTIVE = {
  type: "spring" as const,
  stiffness: 420,
  damping: 32,
};

export const STAGGER_CHILD_DELAY = 0.04; // 40ms, within the 30-50ms band

export const PRESS_SCALE = 0.96;   // within 0.94-0.98
export const HOVER_SCALE = 1.03;   // within 1.02-1.06

// Framer/Motion variants for a staggered list container + item, reused by any
// list/grid that reveals on mount (dashboard KPI row, order queue, product grid).
export const listContainer = {
  hidden: {},
  show: {
    transition: { staggerChildren: STAGGER_CHILD_DELAY },
  },
};

export const listItem = {
  hidden: { opacity: 0, y: 8 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.panel, ease: EASE_OUT_QUART },
  },
};
```

- [ ] **Step 3: Global `prefers-reduced-motion` guard**

Add to `admin/src/app/globals.css`:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add admin/src/app/globals.css admin/src/lib/motion.ts
git commit -m "Add OKLCH design tokens and shared motion tokens"
```

---

### Task 3: Supabase server client (service-role, server-only)

**Files:**
- Create: `admin/src/lib/supabase-server.ts`

- [ ] **Step 1: Write the client helper**

```typescript
import "server-only";
import { createClient } from "@supabase/supabase-js";

// Service-role key: full read/write, bypasses RLS. Never imported into a Client
// Component — the `server-only` import above makes that a build error if it happens.
export function supabaseServer() {
  return createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { persistSession: false } },
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add admin/src/lib/supabase-server.ts
git commit -m "Add server-only Supabase service-role client"
```

---

### Task 4: Telegram Login Widget auth

**Files:**
- Create: `admin/src/lib/session.ts`
- Create: `admin/src/app/api/auth/telegram/route.ts`
- Create: `admin/src/app/login/page.tsx`
- Create: `admin/src/middleware.ts`

**Manual prerequisite:** message @BotFather with `/setdomain`, select the bot, set it to the
panel's deployed domain (or `localhost` won't work for the widget — use a tunnel like `ngrok`
for local dev, or test auth against the deployed preview URL).

- [ ] **Step 1: Session cookie helpers (`jose`-signed JWT, httpOnly)**

```typescript
// admin/src/lib/session.ts
import "server-only";
import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";

const COOKIE_NAME = "admin_session";
const secret = () => new TextEncoder().encode(process.env.SESSION_SECRET!);

export async function createSession(telegramId: number): Promise<void> {
  const token = await new SignJWT({ telegramId })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("30d")
    .sign(secret());

  (await cookies()).set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
}

export async function getSession(): Promise<{ telegramId: number } | null> {
  const token = (await cookies()).get(COOKIE_NAME)?.value;
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, secret());
    return { telegramId: payload.telegramId as number };
  } catch {
    return null;
  }
}

export async function clearSession(): Promise<void> {
  (await cookies()).delete(COOKIE_NAME);
}
```

- [ ] **Step 2: Telegram widget verification Route Handler**

Telegram's widget verification algorithm (documented at core.telegram.org/widgets/login):
concatenate all received fields except `hash` as `key=value` lines sorted alphabetically,
HMAC-SHA256 that string with `SHA256(bot_token)` as the key, and compare to the received `hash`.

```typescript
// admin/src/app/api/auth/telegram/route.ts
import { createHash, createHmac, timingSafeEqual } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { createSession } from "@/lib/session";

function verifyTelegramAuth(data: Record<string, string>): boolean {
  const { hash, ...rest } = data;
  if (!hash) return false;

  const checkString = Object.keys(rest)
    .sort()
    .map((key) => `${key}=${rest[key]}`)
    .join("\n");

  const secretKey = createHash("sha256").update(process.env.TELEGRAM_BOT_TOKEN!).digest();
  const computedHash = createHmac("sha256", secretKey).update(checkString).digest("hex");

  const a = Buffer.from(computedHash, "hex");
  const b = Buffer.from(hash, "hex");
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function GET(req: NextRequest) {
  const params = Object.fromEntries(req.nextUrl.searchParams.entries());

  if (!verifyTelegramAuth(params)) {
    return NextResponse.redirect(new URL("/login?error=invalid_signature", req.url));
  }

  // auth_date freshness: reject widget payloads older than 5 minutes.
  const authDate = Number(params.auth_date) * 1000;
  if (Date.now() - authDate > 5 * 60 * 1000) {
    return NextResponse.redirect(new URL("/login?error=stale", req.url));
  }

  const telegramId = Number(params.id);
  const adminIds = (process.env.ADMIN_TELEGRAM_IDS ?? "")
    .split(",")
    .map((s) => Number(s.trim()))
    .filter(Boolean);

  if (!adminIds.includes(telegramId)) {
    return NextResponse.redirect(new URL("/login?error=not_admin", req.url));
  }

  await createSession(telegramId);
  return NextResponse.redirect(new URL("/dashboard", req.url));
}
```

- [ ] **Step 3: Login page embedding the widget**

```tsx
// admin/src/app/login/page.tsx
"use client";
import { useEffect, useRef } from "react";

export default function LoginPage() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", process.env.NEXT_PUBLIC_BOT_USERNAME!);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-auth-url", `${process.env.NEXT_PUBLIC_SITE_URL}/api/auth/telegram`);
    script.setAttribute("data-request-access", "write");
    containerRef.current?.appendChild(script);
  }, []);

  return (
    <main className="flex min-h-dvh items-center justify-center bg-[var(--bg)]">
      <div className="flex flex-col items-center gap-6 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-10">
        <h1 className="text-2xl font-semibold text-[var(--ink)]">Admin sign-in</h1>
        <p className="max-w-xs text-center text-sm text-[var(--ink-muted)]">
          Sign in with the Telegram account that owns this store.
        </p>
        <div ref={containerRef} />
      </div>
    </main>
  );
}
```

Add `NEXT_PUBLIC_BOT_USERNAME` and `NEXT_PUBLIC_SITE_URL` to `.env.local.example` alongside the
existing entries from Task 1 Step 4.

- [ ] **Step 4: Middleware guarding `/dashboard`, `/orders`, `/products`, `/users`**

```typescript
// admin/src/middleware.ts
import { NextRequest, NextResponse } from "next/server";
import { getSession } from "@/lib/session";

export async function middleware(req: NextRequest) {
  const session = await getSession();
  if (!session) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/orders/:path*", "/products/:path*", "/users/:path*"],
};
```

- [ ] **Step 5: Manual verification**

Run: `npm run dev`, visit `/login` (via the tunneled/deployed domain set in BotFather), click the
widget, confirm it redirects to `/dashboard` and a `admin_session` httpOnly cookie is set (check
DevTools → Application → Cookies). Visiting `/dashboard` directly without the cookie must redirect
to `/login`.

- [ ] **Step 6: Commit**

```bash
git add admin/src/lib/session.ts admin/src/app/api/auth/telegram admin/src/app/login admin/src/middleware.ts admin/.env.local.example
git commit -m "Add Telegram Login Widget auth with signed session cookie"
```

---

### Task 5: App shell + sidebar navigation

**Files:**
- Create: `admin/src/app/(admin)/layout.tsx`
- Create: `admin/src/components/sidebar.tsx`

- [ ] **Step 1: Sidebar with animated active-state indicator**

```tsx
// admin/src/components/sidebar.tsx
"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { LayoutDashboard, Package, Receipt, Users } from "lucide-react";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/orders", label: "Orders", icon: Receipt },
  { href: "/products", label: "Products", icon: Package },
  { href: "/users", label: "Users & Wallet", icon: Users },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <nav className="flex h-dvh w-60 flex-col gap-1 border-r border-[var(--border)] bg-[var(--surface)] p-4">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className="relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-[var(--ink-muted)] transition-colors hover:text-[var(--ink)]"
          >
            {active && (
              <motion.div
                layoutId="active-nav"
                className="absolute inset-0 rounded-lg bg-[var(--accent)]/10"
                transition={{ type: "spring", stiffness: 420, damping: 32 }}
              />
            )}
            <Icon className="relative z-10 size-4" />
            <span className={`relative z-10 ${active ? "text-[var(--ink)]" : ""}`}>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
```

`layoutId="active-nav"` is Motion's shared-element transition — the highlight pill slides between
nav items instead of popping, using the same spring token from `lib/motion.ts`.

- [ ] **Step 2: Route group layout**

```tsx
// admin/src/app/(admin)/layout.tsx
import { Sidebar } from "@/components/sidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 p-8">{children}</main>
    </div>
  );
}
```

Move `dashboard/`, `orders/`, `products/`, `users/` route folders (created in later tasks) under
this `(admin)` route group so they share the sidebar.

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/(admin)/layout.tsx admin/src/components/sidebar.tsx
git commit -m "Add admin app shell with animated sidebar navigation"
```

---

### Task 6: Dashboard — KPIs, chart, low-stock banner

**Files:**
- Create: `admin/src/app/(admin)/dashboard/page.tsx`
- Create: `admin/src/components/kpi-card.tsx`
- Create: `admin/src/lib/queries.ts`

- [ ] **Step 1: Data queries**

```typescript
// admin/src/lib/queries.ts
import { supabaseServer } from "./supabase-server";

export async function getDashboardStats() {
  const db = supabaseServer();

  const since7d = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();

  const [{ data: recentOrders }, { data: lowStock }, { count: pendingCount }] = await Promise.all([
    db.from("orders").select("created_at, amount, amount_usdt, status").gte("created_at", since7d),
    db.from("products").select("id, name, stock").eq("active", 1).gte("stock", 0).lte("stock", 5),
    db.from("orders").select("id", { count: "exact", head: true }).eq("status", "pending_review"),
  ]);

  const approved = (recentOrders ?? []).filter((o) => o.status === "approved");
  const revenueInr = approved.reduce((sum, o) => sum + Number(o.amount ?? 0), 0);
  const revenueUsdt = approved.reduce((sum, o) => sum + Number(o.amount_usdt ?? 0), 0);

  // Group approved orders by day for the trend chart.
  const byDay = new Map<string, number>();
  for (const o of approved) {
    const day = o.created_at.slice(0, 10);
    byDay.set(day, (byDay.get(day) ?? 0) + Number(o.amount_usdt ?? 0));
  }
  const trend = [...byDay.entries()].sort().map(([date, usdt]) => ({ date, usdt }));

  return {
    revenueInr,
    revenueUsdt,
    orderCount7d: approved.length,
    pendingCount: pendingCount ?? 0,
    lowStock: lowStock ?? [],
    trend,
  };
}
```

- [ ] **Step 2: KPI card component**

```tsx
// admin/src/components/kpi-card.tsx
"use client";
import { motion } from "motion/react";
import { listItem } from "@/lib/motion";

export function KpiCard({ label, value, sublabel }: { label: string; value: string; sublabel?: string }) {
  return (
    <motion.div
      variants={listItem}
      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5"
    >
      <p className="text-sm text-[var(--ink-muted)]">{label}</p>
      <p className="mt-2 text-3xl font-semibold tabular-nums text-[var(--ink)]">{value}</p>
      {sublabel && <p className="mt-1 text-xs text-[var(--ink-muted)]">{sublabel}</p>}
    </motion.div>
  );
}
```

- [ ] **Step 3: Dashboard page**

```tsx
// admin/src/app/(admin)/dashboard/page.tsx
import { getDashboardStats } from "@/lib/queries";
import { KpiCard } from "@/components/kpi-card";
import { DashboardChart } from "@/components/dashboard-chart";
import { KpiRow } from "@/components/kpi-row";

export default async function DashboardPage() {
  const stats = await getDashboardStats();

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-[var(--ink)]">Dashboard</h1>

      {stats.lowStock.length > 0 && (
        <div className="rounded-lg border border-[var(--warning)]/40 bg-[var(--warning)]/10 px-4 py-3 text-sm text-[var(--ink)]">
          Low stock: {stats.lowStock.map((p) => `${p.name} (${p.stock})`).join(", ")}
        </div>
      )}

      <KpiRow>
        <KpiCard label="Revenue (7d, INR)" value={`₹${stats.revenueInr.toFixed(0)}`} />
        <KpiCard label="Revenue (7d, USDT)" value={`$${stats.revenueUsdt.toFixed(2)}`} />
        <KpiCard label="Orders (7d)" value={String(stats.orderCount7d)} />
        <KpiCard label="Pending approval" value={String(stats.pendingCount)} sublabel="Needs review" />
      </KpiRow>

      <DashboardChart data={stats.trend} />
    </div>
  );
}
```

```tsx
// admin/src/components/kpi-row.tsx
"use client";
import { motion } from "motion/react";
import { listContainer } from "@/lib/motion";

export function KpiRow({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      variants={listContainer}
      initial="hidden"
      animate="show"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
    >
      {children}
    </motion.div>
  );
}
```

```tsx
// admin/src/components/dashboard-chart.tsx
"use client";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export function DashboardChart({ data }: { data: { date: string; usdt: number }[] }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
      <p className="mb-4 text-sm font-medium text-[var(--ink)]">Revenue trend (USDT)</p>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="date" stroke="var(--ink-muted)" fontSize={12} />
          <YAxis stroke="var(--ink-muted)" fontSize={12} />
          <Tooltip
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }}
          />
          <Line type="monotone" dataKey="usdt" stroke="var(--accent)" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 4: Manual verification**

Run: `npm run dev`, sign in, visit `/dashboard`. Expected: KPI cards stagger in on load, chart
renders (empty is fine with no orders yet), low-stock banner only appears when a product has
stock between 0 and 5.

- [ ] **Step 5: Commit**

```bash
git add admin/src/app/\(admin\)/dashboard admin/src/components/kpi-card.tsx admin/src/components/kpi-row.tsx admin/src/components/dashboard-chart.tsx admin/src/lib/queries.ts
git commit -m "Add admin dashboard with KPIs, revenue trend chart, low-stock banner"
```

---

### Task 7: Orders queue + approve/reject

**Files:**
- Create: `admin/src/app/(admin)/orders/page.tsx`
- Create: `admin/src/app/(admin)/orders/actions.ts`
- Create: `admin/src/components/order-row.tsx`

- [ ] **Step 1: Server Actions for approve/reject**

Writing to `orders.status` here uses the exact same values the bot's `app/db/schema.py` defines
(`pending_review` → `approved`/`rejected`), so a Telegram-side and panel-side approval race
safely: whichever UPDATE commits first wins, the other reads a status that's no longer
`pending_review` and can no-op. This mirrors `app/handlers/approvals.py`'s existing logic; add a
`WHERE status = 'pending_review'` guard to make that race explicit here too.

```typescript
// admin/src/app/(admin)/orders/actions.ts
"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";

export async function approveOrder(orderId: number) {
  const db = supabaseServer();
  const { error } = await db
    .from("orders")
    .update({ status: "approved", updated_at: new Date().toISOString() })
    .eq("id", orderId)
    .eq("status", "pending_review");
  if (error) throw new Error(error.message);
  revalidatePath("/orders");
}

export async function rejectOrder(orderId: number, reason: string) {
  const db = supabaseServer();
  const { error } = await db
    .from("orders")
    .update({ status: "rejected", reason, updated_at: new Date().toISOString() })
    .eq("id", orderId)
    .eq("status", "pending_review");
  if (error) throw new Error(error.message);
  revalidatePath("/orders");
}
```

**Note:** approving here does not run the bot's delivery logic (sending the buyer their product
code via Telegram) — that logic lives in `app/handlers/approvals.py` and only runs inside the bot
process. Flag this as a follow-up decision for the user before shipping this task: either (a)
keep manual-payment approvals Telegram-only and use this panel view as read-only + a link back to
the Telegram chat, or (b) give the panel a way to trigger delivery too (e.g. a `pending_panel_action`
row the bot polls, or calling the Telegram Bot API directly from the Route Handler to send the
code). Don't build (b) speculatively — confirm which the user wants before extending this task.

- [ ] **Step 2: Orders page (Server Component, fetches + renders)**

```tsx
// admin/src/app/(admin)/orders/page.tsx
import { supabaseServer } from "@/lib/supabase-server";
import { OrderRow } from "@/components/order-row";

export default async function OrdersPage() {
  const db = supabaseServer();
  const { data: orders } = await db
    .from("orders")
    .select("*")
    .order("id", { ascending: false })
    .limit(100);

  const pending = (orders ?? []).filter((o) => o.status === "pending_review");
  const rest = (orders ?? []).filter((o) => o.status !== "pending_review");

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-semibold text-[var(--ink)]">Pending approval</h1>
        <div className="mt-4 flex flex-col gap-3">
          {pending.length === 0 && <p className="text-sm text-[var(--ink-muted)]">Nothing waiting.</p>}
          {pending.map((order) => (
            <OrderRow key={order.id} order={order} actionable />
          ))}
        </div>
      </div>
      <div>
        <h2 className="text-lg font-medium text-[var(--ink)]">Order history</h2>
        <div className="mt-4 flex flex-col gap-2">
          {rest.map((order) => (
            <OrderRow key={order.id} order={order} actionable={false} />
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Order row with approve/reject buttons**

```tsx
// admin/src/components/order-row.tsx
"use client";
import { useTransition } from "react";
import { motion } from "motion/react";
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

  return (
    <div className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 py-3">
      <div>
        <p className="text-sm font-medium text-[var(--ink)]">
          #{order.ref} — {order.product_name}
        </p>
        <p className="text-xs text-[var(--ink-muted)]">
          {order.username || order.user_id} · {order.method} · {order.utr}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <Badge className={STATUS_COLOR[order.status]}>{order.status}</Badge>
        {actionable && (
          <>
            <motion.div whileTap={{ scale: PRESS_SCALE }} transition={{ duration: DURATION.micro }}>
              <Button
                size="sm"
                disabled={pending}
                onClick={() => startTransition(() => approveOrder(order.id))}
              >
                Approve
              </Button>
            </motion.div>
            <motion.div whileTap={{ scale: PRESS_SCALE }} transition={{ duration: DURATION.micro }}>
              <Button
                size="sm"
                variant="destructive"
                disabled={pending}
                onClick={() => startTransition(() => rejectOrder(order.id, "Rejected from admin panel"))}
              >
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

- [ ] **Step 4: Manual verification**

Create a test order via the bot (submit a UTR so it reaches `pending_review`), confirm it shows
in the panel's pending list, click Approve, confirm the row moves to history with an `approved`
badge and the bot's own `python view_db.py --orders pending_review` no longer lists it.

- [ ] **Step 5: Commit**

```bash
git add admin/src/app/\(admin\)/orders admin/src/components/order-row.tsx
git commit -m "Add orders queue with approve/reject Server Actions"
```

---

### Task 8: Products management (CRUD + delivery-key pool)

**Files:**
- Create: `admin/src/app/(admin)/products/page.tsx`
- Create: `admin/src/app/(admin)/products/actions.ts`
- Create: `admin/src/components/product-card.tsx`
- Create: `admin/src/components/product-form-dialog.tsx`

- [ ] **Step 1: Server Actions**

```typescript
// admin/src/app/(admin)/products/actions.ts
"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";

export type ProductInput = {
  name: string;
  description: string;
  price: number;
  price_inr: number;
  content: string;
  stock: number;
};

export async function createProduct(input: ProductInput) {
  const db = supabaseServer();
  const { error } = await db.from("products").insert({
    ...input,
    active: 1,
    created_at: new Date().toISOString(),
  });
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}

export async function updateProduct(id: number, input: Partial<ProductInput>) {
  const db = supabaseServer();
  const { error } = await db.from("products").update(input).eq("id", id);
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}

export async function setProductActive(id: number, active: boolean) {
  const db = supabaseServer();
  const { error } = await db.from("products").update({ active: active ? 1 : 0 }).eq("id", id);
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}

export async function addProductKeys(id: number, codes: string[]) {
  const db = supabaseServer();
  const { error } = await db.from("product_keys").insert(codes.map((code) => ({ product_id: id, code })));
  if (error) throw new Error(error.message);
  revalidatePath("/products");
}
```

- [ ] **Step 2: Products page**

```tsx
// admin/src/app/(admin)/products/page.tsx
import { supabaseServer } from "@/lib/supabase-server";
import { ProductCard } from "@/components/product-card";

export default async function ProductsPage() {
  const db = supabaseServer();
  const { data: products } = await db.from("products").select("*").order("id");
  const { data: keyCounts } = await db
    .from("product_keys")
    .select("product_id, used");

  const unusedByProduct = new Map<number, number>();
  for (const row of keyCounts ?? []) {
    if (row.used === 0) {
      unusedByProduct.set(row.product_id, (unusedByProduct.get(row.product_id) ?? 0) + 1);
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-[var(--ink)]">Products</h1>
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(products ?? []).map((p) => (
          <ProductCard key={p.id} product={p} unusedKeys={unusedByProduct.get(p.id) ?? 0} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Product card** (edit/deactivate/key-pool entry points; the dialog form itself
  follows the same `react-hook-form` + shadcn `Dialog` pattern used across the panel — write it
  in this step, not deferred, since it's this task's core CRUD surface)

```tsx
// admin/src/components/product-card.tsx
"use client";
import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { setProductActive } from "@/app/(admin)/products/actions";
import { ProductFormDialog } from "@/components/product-form-dialog";

export function ProductCard({ product, unusedKeys }: { product: any; unusedKeys: number }) {
  const [editing, setEditing] = useState(false);
  const [pending, startTransition] = useTransition();

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium text-[var(--ink)]">{product.name}</p>
          <p className="text-sm text-[var(--ink-muted)]">
            ₹{product.price_inr} · ${product.price} · stock {product.stock === -1 ? "∞" : product.stock}
          </p>
        </div>
        <Switch
          checked={product.active === 1}
          disabled={pending}
          onCheckedChange={(checked) => startTransition(() => setProductActive(product.id, checked))}
        />
      </div>
      <p className="mt-2 text-xs text-[var(--ink-muted)]">{unusedKeys} unused delivery keys</p>
      <Button size="sm" variant="outline" className="mt-3" onClick={() => setEditing(true)}>
        Edit
      </Button>
      {editing && <ProductFormDialog product={product} onClose={() => setEditing(false)} />}
    </div>
  );
}
```

```tsx
// admin/src/components/product-form-dialog.tsx
"use client";
import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { updateProduct, addProductKeys } from "@/app/(admin)/products/actions";

export function ProductFormDialog({ product, onClose }: { product: any; onClose: () => void }) {
  const [form, setForm] = useState({
    name: product.name,
    description: product.description,
    price: product.price,
    price_inr: product.price_inr,
    stock: product.stock,
  });
  const [keysText, setKeysText] = useState("");

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {product.name}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div>
            <Label htmlFor="name">Name</Label>
            <Input id="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="price_inr">Price (INR)</Label>
            <Input
              id="price_inr"
              type="number"
              value={form.price_inr}
              onChange={(e) => setForm({ ...form, price_inr: Number(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="price">Price (USDT)</Label>
            <Input
              id="price"
              type="number"
              value={form.price}
              onChange={(e) => setForm({ ...form, price: Number(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="stock">Stock (-1 = unlimited)</Label>
            <Input
              id="stock"
              type="number"
              value={form.stock}
              onChange={(e) => setForm({ ...form, stock: Number(e.target.value) })}
            />
          </div>
          <Button
            onClick={async () => {
              await updateProduct(product.id, form);
              onClose();
            }}
          >
            Save
          </Button>
          <div className="mt-2 border-t border-[var(--border)] pt-3">
            <Label htmlFor="keys">Add delivery keys (one per line)</Label>
            <textarea
              id="keys"
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--bg)] p-2 text-sm"
              rows={4}
              value={keysText}
              onChange={(e) => setKeysText(e.target.value)}
            />
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={async () => {
                const codes = keysText.split("\n").map((s) => s.trim()).filter(Boolean);
                if (codes.length) await addProductKeys(product.id, codes);
                setKeysText("");
              }}
            >
              Add keys
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
```

Run `npx shadcn@latest add switch` before this step if not already added.

- [ ] **Step 4: Manual verification**

Edit a product's price in the panel, confirm `python view_db.py` (bot-side) reflects the change.
Add two delivery keys via the panel, confirm `count_unused_keys` (via `view_db.py`) increases by 2.

- [ ] **Step 5: Commit**

```bash
git add admin/src/app/\(admin\)/products admin/src/components/product-card.tsx admin/src/components/product-form-dialog.tsx
git commit -m "Add product management: CRUD, active toggle, delivery-key pool"
```

---

### Task 9: Users & wallet ledger view

**Files:**
- Create: `admin/src/app/(admin)/users/page.tsx`
- Create: `admin/src/app/(admin)/users/[id]/page.tsx`

- [ ] **Step 1: Users list**

```tsx
// admin/src/app/(admin)/users/page.tsx
import Link from "next/link";
import { supabaseServer } from "@/lib/supabase-server";

export default async function UsersPage() {
  const db = supabaseServer();
  const { data: users } = await db
    .from("users")
    .select("user_id, first_name, username, wallet_balance_micro, started_at")
    .order("started_at", { ascending: false })
    .limit(200);

  return (
    <div>
      <h1 className="text-2xl font-semibold text-[var(--ink)]">Users</h1>
      <div className="mt-6 flex flex-col divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--surface)]">
        {(users ?? []).map((u) => (
          <Link
            key={u.user_id}
            href={`/users/${u.user_id}`}
            className="flex items-center justify-between px-4 py-3 text-sm hover:bg-[var(--accent)]/5"
          >
            <span className="text-[var(--ink)]">{u.first_name} @{u.username || "—"}</span>
            <span className="tabular-nums text-[var(--ink-muted)]">
              ${(u.wallet_balance_micro / 1_000_000).toFixed(2)}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Per-user ledger detail (reuses the ledger-sums-match-balance invariant as a
  visible audit check, same one `view_db.py --ledger` already verifies bot-side)**

```tsx
// admin/src/app/(admin)/users/[id]/page.tsx
import { supabaseServer } from "@/lib/supabase-server";

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const userId = Number(id);
  const db = supabaseServer();

  const [{ data: user }, { data: ledger }, { data: deposits }] = await Promise.all([
    db.from("users").select("*").eq("user_id", userId).single(),
    db.from("wallet_ledger").select("*").eq("user_id", userId).order("id"),
    db.from("deposits").select("*").eq("user_id", userId).order("id", { ascending: false }),
  ]);

  const ledgerSum = (ledger ?? []).reduce((sum, r) => sum + Number(r.delta_micro), 0);
  const balanceMismatch = user && ledgerSum !== user.wallet_balance_micro;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-[var(--ink)]">
        {user?.first_name} @{user?.username}
      </h1>

      {balanceMismatch && (
        <div className="rounded-lg border border-[var(--danger)]/40 bg-[var(--danger)]/10 px-4 py-3 text-sm text-[var(--danger)]">
          Ledger sum (${(ledgerSum / 1_000_000).toFixed(2)}) does not match cached balance
          (${((user?.wallet_balance_micro ?? 0) / 1_000_000).toFixed(2)}) — investigate before
          trusting this balance.
        </div>
      )}

      <div>
        <h2 className="text-lg font-medium text-[var(--ink)]">Ledger</h2>
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-[var(--ink-muted)]">
            <tr><th className="py-2">Date</th><th>Reason</th><th className="text-right">Delta</th><th className="text-right">Balance after</th></tr>
          </thead>
          <tbody>
            {(ledger ?? []).map((r) => (
              <tr key={r.id} className="border-t border-[var(--border)]">
                <td className="py-2">{r.created_at}</td>
                <td>{r.reason}</td>
                <td className="text-right tabular-nums">{(r.delta_micro / 1_000_000).toFixed(2)}</td>
                <td className="text-right tabular-nums">{(r.balance_after_micro / 1_000_000).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <h2 className="text-lg font-medium text-[var(--ink)]">Deposits</h2>
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-[var(--ink-muted)]">
            <tr><th className="py-2">Rail</th><th>Status</th><th className="text-right">Tagged amount</th></tr>
          </thead>
          <tbody>
            {(deposits ?? []).map((d) => (
              <tr key={d.id} className="border-t border-[var(--border)]">
                <td className="py-2">{d.rail}</td>
                <td>{d.status}</td>
                <td className="text-right tabular-nums">{(d.tagged_amount_micro / 1_000_000).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/\(admin\)/users
git commit -m "Add users list and per-user wallet ledger audit view"
```

---

### Task 10: Realtime — server-side subscribe, SSE relay to the browser

**Files:**
- Create: `admin/src/app/api/realtime/route.ts`
- Create: `admin/src/hooks/use-live-updates.ts`
- Modify: `admin/src/app/(admin)/orders/page.tsx` (wrap in a client refresh listener)

Per the design spec: browser-side Supabase Realtime with the anon key would need RLS + Supabase
Auth to be safe, which this panel deliberately doesn't have (Task 4 uses a custom session cookie
instead). So the subscription happens server-side with the service-role key, and only a `"changed"`
signal (not the row data itself) is relayed to the browser — the browser then re-fetches through
the already-authenticated Server Component route, never touching Supabase directly.

- [ ] **Step 1: SSE relay route**

```typescript
// admin/src/app/api/realtime/route.ts
import { getSession } from "@/lib/session";
import { supabaseServer } from "@/lib/supabase-server";

export async function GET() {
  const session = await getSession();
  if (!session) {
    return new Response("Unauthorized", { status: 401 });
  }

  const encoder = new TextEncoder();
  let close: () => void = () => {};

  const stream = new ReadableStream({
    start(controller) {
      const send = (event: string) => controller.enqueue(encoder.encode(`data: ${event}\n\n`));

      const db = supabaseServer();
      const channel = db
        .channel("admin-panel-changes")
        .on("postgres_changes", { event: "*", schema: "public", table: "orders" }, () => send("orders"))
        .on("postgres_changes", { event: "*", schema: "public", table: "products" }, () => send("products"))
        .on("postgres_changes", { event: "*", schema: "public", table: "deposits" }, () => send("deposits"))
        .subscribe();

      close = () => {
        db.removeChannel(channel);
        controller.close();
      };

      // Keep-alive ping every 20s so intermediate proxies don't time out the connection.
      const ping = setInterval(() => controller.enqueue(encoder.encode(": ping\n\n")), 20_000);
      close = () => {
        clearInterval(ping);
        db.removeChannel(channel);
      };
    },
    cancel() {
      close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
```

- [ ] **Step 2: Client hook consuming the stream**

```typescript
// admin/src/hooks/use-live-updates.ts
"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Re-fetches the current Server Component route whenever a watched table changes. */
export function useLiveUpdates(watchTables: string[]) {
  const router = useRouter();

  useEffect(() => {
    const source = new EventSource("/api/realtime");
    source.onmessage = (e) => {
      if (watchTables.includes(e.data)) {
        router.refresh();
      }
    };
    return () => source.close();
  }, [router, watchTables]);
}
```

- [ ] **Step 3: Wire it into Orders and Dashboard**

Add a small client component that mounts the hook (Server Components can't call hooks directly):

```tsx
// admin/src/components/live-refresh.tsx
"use client";
import { useLiveUpdates } from "@/hooks/use-live-updates";

export function LiveRefresh({ watch }: { watch: string[] }) {
  useLiveUpdates(watch);
  return null;
}
```

In `admin/src/app/(admin)/orders/page.tsx`, add `<LiveRefresh watch={["orders"]} />` as the first
child of the returned JSX. In `admin/src/app/(admin)/dashboard/page.tsx`, add
`<LiveRefresh watch={["orders", "products", "deposits"]} />` likewise.

- [ ] **Step 4: Enable Realtime on the Supabase tables**

In the Supabase dashboard → Database → Replication, enable Realtime for `orders`, `products`,
`deposits` (off by default per table).

- [ ] **Step 5: Manual verification**

Open `/orders` in the browser, submit + approve a UTR from the Telegram bot in another window,
confirm the panel's order list updates within ~1s without a manual page reload.

- [ ] **Step 6: Commit**

```bash
git add admin/src/app/api/realtime admin/src/hooks/use-live-updates.ts admin/src/components/live-refresh.tsx
git commit -m "Add server-side Supabase Realtime relayed to the browser over SSE"
```

---

### Task 11: Deploy

**Files:** none (operational steps)

- [ ] **Step 1:** Push `admin/` to its own Vercel project (or any Next.js host), root directory set to `admin/`.
- [ ] **Step 2:** Set all `admin/.env.local.example` variables as production env vars on the host.
- [ ] **Step 3:** Point BotFather's `/setdomain` at the deployed production domain.
- [ ] **Step 4:** Sign in on the production URL, walk through Dashboard → Orders → Products → Users once end-to-end.
- [ ] **Step 5:** Confirm `prefers-reduced-motion` works: enable it in OS accessibility settings, reload the dashboard, confirm KPI cards appear instantly with no stagger/slide.
