# Manual Stuck-Deposit Resolve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin browse pending wallet-top-up deposits across all users in the Next.js admin panel and manually credit one that never auto-matched (buyer sent the wrong amount, service hiccup, etc.), with a proper `wallet_ledger` audit trail.

**Architecture:** A new route segment `admin/src/app/(admin)/deposits/` (page + server action) mirroring the existing `admin/src/app/(admin)/orders/` pattern exactly — same tabbed-table page shape, same guarded-`UPDATE` server action style, same row-component-with-inline-dialog shape already used by `product-card.tsx`/`product-form-dialog.tsx`. No schema changes, no new dependency, no database transaction (see design doc for why — matches this panel's existing convention).

**Tech Stack:** Next.js 16 App Router, TypeScript, Supabase JS client (service-role, server-only), shadcn/ui components (`@base-ui/react` under the hood), Tailwind CSS vars for theming, `sonner` for toasts.

**Design doc:** `docs/superpowers/specs/2026-07-17-manual-deposit-resolve-design.md` — read this first if anything below is ambiguous, it has the full rationale.

---

### Task 1: `resolveDeposit` server action

**Files:**
- Create: `admin/src/app/(admin)/deposits/actions.ts`

- [ ] **Step 1: Write the file**

```typescript
"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";
import { getSession } from "@/lib/session";

export async function resolveDeposit(
  depositId: number,
  userId: number,
  creditedAmountMicro: number,
  note: string,
) {
  const session = await getSession();
  if (!session) throw new Error("Not authenticated");

  const db = supabaseServer();
  const trimmedNote = note.trim();
  const ref = trimmedNote
    ? `manual:${session.telegramId}:${trimmedNote}`
    : `manual:${session.telegramId}`;

  // Guarded UPDATE is the concurrency lock — same pattern as orders/actions.ts's
  // approveOrder/rejectOrder. Only the caller that actually flips pending -> credited
  // proceeds to credit the wallet, so a double-click or two admins racing on the same
  // deposit can't both credit it.
  const { data, error } = await db
    .from("deposits")
    .update({ status: "credited", credited_tx_ref: ref })
    .eq("id", depositId)
    .eq("status", "pending")
    .select("id");
  if (error) throw new Error(error.message);
  if (!data || data.length === 0) throw new Error("This deposit was already resolved");

  const { data: userRow, error: userErr } = await db
    .from("users")
    .select("wallet_balance_micro")
    .eq("user_id", userId)
    .single();
  if (userErr) throw new Error(userErr.message);

  // ponytail: read-then-write balance update, not an atomic increment (Supabase's
  // query builder has no `col = col + x` expression support without an RPC, which the
  // design doc explicitly deferred for this low-frequency admin action). Tiny race
  // window if two admins resolve different deposits for the same user at the same
  // instant. Upgrade path: a Postgres RPC function if manual resolves become frequent.
  const newBalance = (userRow?.wallet_balance_micro ?? 0) + creditedAmountMicro;

  const { error: balErr } = await db
    .from("users")
    .update({ wallet_balance_micro: newBalance })
    .eq("user_id", userId);
  if (balErr) throw new Error(balErr.message);

  const { error: ledgerErr } = await db.from("wallet_ledger").insert({
    user_id: userId,
    delta_micro: creditedAmountMicro,
    reason: "manual_admin_credit",
    ref: `deposit:${depositId}`,
    balance_after_micro: newBalance,
    created_at: new Date().toISOString(),
  });
  if (ledgerErr) throw new Error(ledgerErr.message);

  revalidatePath("/deposits");
}
```

- [ ] **Step 2: Type-check it**

Run (from the `admin/` directory): `npx tsc --noEmit`
Expected: no errors referencing `deposits/actions.ts`. (This repo has no separate test suite for the admin panel — `tsc` plus the manual QA in Task 6 is the verification convention here, matching every other action file in `admin/src/app/(admin)/*/actions.ts`, none of which have unit tests.)

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/\(admin\)/deposits/actions.ts
git commit -m "feat(admin): add resolveDeposit server action for manual top-up credits"
```

---

### Task 2: `DepositRow` component (row + resolve dialog)

**Files:**
- Create: `admin/src/components/deposit-row.tsx`

This depends on Task 1 (imports `resolveDeposit`). Follows the `product-card.tsx` + `product-form-dialog.tsx` pattern (a row that opens an inline dialog to collect input), folded into one file since — unlike the product dialog, which is reused for both create and edit — this dialog only has one caller, so a second file would be an unrequested split.

- [ ] **Step 1: Write the file**

```typescript
"use client";
import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TableCell, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { resolveDeposit } from "@/app/(admin)/deposits/actions";

export function DepositRow({ deposit, actionable }: { deposit: any; actionable: boolean }) {
  const [resolving, setResolving] = useState(false);
  const [amount, setAmount] = useState((deposit.tagged_amount_micro / 1_000_000).toString());
  const [note, setNote] = useState("");
  const [pending, startTransition] = useTransition();
  const [done, setDone] = useState(false);

  const stale = deposit.status === "pending" && new Date(deposit.expires_at) < new Date();

  function submit() {
    const micro = Math.round(Number(amount) * 1_000_000);
    if (!micro || micro <= 0) {
      toast.error("Enter a valid amount");
      return;
    }
    startTransition(async () => {
      try {
        await resolveDeposit(deposit.id, deposit.user_id, micro, note);
        setDone(true);
        setResolving(false);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to resolve deposit");
      }
    });
  }

  return (
    <TableRow className={done ? "opacity-60" : ""}>
      <TableCell className="text-[var(--ink-muted)]">{deposit.buyer_name}</TableCell>
      <TableCell className="uppercase text-[var(--ink-muted)]">{deposit.rail}</TableCell>
      <TableCell className="text-right tabular-nums">
        {(deposit.tagged_amount_micro / 1_000_000).toFixed(2)}
      </TableCell>
      <TableCell className="text-[var(--ink-muted)]">{deposit.created_at}</TableCell>
      <TableCell>
        <Badge
          className={
            done
              ? "bg-[var(--success)]/15 text-[var(--success)]"
              : stale
                ? "bg-[var(--danger)]/15 text-[var(--danger)]"
                : "bg-[var(--warning)]/15 text-[var(--warning)]"
          }
        >
          {done ? "resolved" : stale ? "stale" : deposit.status}
        </Badge>
      </TableCell>
      <TableCell>
        {actionable && !done && (
          <Button size="sm" disabled={pending} onClick={() => setResolving(true)}>
            Resolve
          </Button>
        )}
      </TableCell>
      {resolving && (
        <Dialog open onOpenChange={(open) => !open && setResolving(false)}>
          <DialogContent className="w-full max-w-md">
            <DialogHeader>
              <DialogTitle>Resolve deposit #{deposit.id}</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-3">
              <p className="text-sm text-[var(--ink-muted)]">
                Buyer was tagged for {(deposit.tagged_amount_micro / 1_000_000).toFixed(2)} USDT.
                Enter what they actually sent.
              </p>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="amount">Amount to credit (USDT)</Label>
                <Input
                  id="amount"
                  type="number"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="note">Note (optional)</Label>
                <Input
                  id="note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. sent 9.98 instead of 10.03"
                />
              </div>
              <Button disabled={pending} onClick={submit}>
                Credit wallet
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </TableRow>
  );
}
```

- [ ] **Step 2: Type-check it**

Run: `npx tsc --noEmit`
Expected: no errors referencing `deposit-row.tsx` (there will still be errors about `deposits/page.tsx` not existing yet if you check imports transitively — that's expected until Task 3).

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/deposit-row.tsx
git commit -m "feat(admin): add DepositRow with inline resolve dialog"
```

---

### Task 3: `/deposits` page

**Files:**
- Create: `admin/src/app/(admin)/deposits/page.tsx`

Depends on Task 2 (imports `DepositRow`).

- [ ] **Step 1: Write the file**

```typescript
import { supabaseServer } from "@/lib/supabase-server";
import { DepositRow } from "@/components/deposit-row";
import { LiveRefresh } from "@/components/live-refresh";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const dynamic = "force-dynamic";

function DepositTable({
  deposits,
  actionable,
  emptyMessage,
}: {
  deposits: any[];
  actionable: boolean;
  emptyMessage: string;
}) {
  if (deposits.length === 0) {
    return <p className="mt-4 text-sm text-[var(--ink-muted)]">{emptyMessage}</p>;
  }
  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)]">
      <div
        className="h-[3px] w-full"
        style={{ background: "linear-gradient(90deg, var(--brand-violet-to), var(--brand-gold-to))" }}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Buyer</TableHead>
            <TableHead>Rail</TableHead>
            <TableHead className="text-right">Tagged amount</TableHead>
            <TableHead>Created</TableHead>
            <TableHead>Status</TableHead>
            {actionable && <TableHead>Actions</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {deposits.map((d) => (
            <DepositRow key={d.id} deposit={d} actionable={actionable} />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export default async function DepositsPage() {
  const db = supabaseServer();
  const { data: deposits } = await db
    .from("deposits")
    .select("*")
    .order("id", { ascending: true }) // oldest first, so genuinely stuck ones surface at the top
    .limit(200);

  const userIds = [...new Set((deposits ?? []).map((d) => d.user_id))];
  const { data: users } = userIds.length
    ? await db.from("users").select("user_id, first_name, username").in("user_id", userIds)
    : { data: [] as { user_id: number; first_name: string; username: string }[] };
  const userMap = new Map((users ?? []).map((u) => [u.user_id, u]));

  const enriched = (deposits ?? []).map((d) => ({
    ...d,
    buyer_name: userMap.get(d.user_id)?.username || userMap.get(d.user_id)?.first_name || d.user_id,
  }));

  const pending = enriched.filter((d) => d.status === "pending");
  const resolved = enriched.filter(
    (d) => d.status === "credited" && d.credited_tx_ref?.startsWith("manual:"),
  );

  return (
    <div className="flex flex-col gap-4">
      <LiveRefresh watch={["deposits"]} />

      <Tabs defaultValue="pending">
        <TabsList className="h-10 border border-[var(--border)] bg-[var(--surface)] p-1">
          <TabsTrigger
            value="pending"
            className="gap-1.5 data-active:bg-[var(--brand-violet-to)] data-active:text-white data-active:shadow-[var(--glow-violet)]"
          >
            Pending
            {pending.length > 0 && (
              <Badge className="bg-[var(--warning)]/15 text-[var(--warning)]">{pending.length}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="resolved"
            className="data-active:bg-[var(--brand-violet-to)] data-active:text-white data-active:shadow-[var(--glow-violet)]"
          >
            ✓ Manually resolved
          </TabsTrigger>
        </TabsList>

        <TabsContent value="pending">
          <p className="text-sm text-[var(--ink-muted)]">
            Deposits waiting to be auto-matched by the BSC/Binance Pay watcher. Stale ones (past
            their expiry) will never auto-match — resolve them manually once you&apos;ve confirmed
            what the buyer actually sent.
          </p>
          <DepositTable deposits={pending} actionable emptyMessage="Nothing pending." />
        </TabsContent>

        <TabsContent value="resolved">
          <p className="text-sm text-[var(--ink-muted)]">Deposits you resolved manually.</p>
          <DepositTable deposits={resolved} actionable={false} emptyMessage="None yet." />
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 2: Type-check it**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/\(admin\)/deposits/page.tsx
git commit -m "feat(admin): add /deposits page listing pending and manually-resolved deposits"
```

---

### Task 4: Nav entry + route protection

**Files:**
- Modify: `admin/src/components/sidebar.tsx`
- Modify: `admin/src/proxy.ts`

Independent of Tasks 1-3 — can be done any time, but doing it last means `/deposits` doesn't appear in the nav (or get session-gated) until the page it points to actually exists.

- [ ] **Step 1: Add the nav entry**

In `admin/src/components/sidebar.tsx`, change the import line:
```typescript
import { LayoutDashboard, Package, Receipt, Store, Users } from "lucide-react";
```
to:
```typescript
import { LayoutDashboard, Package, Receipt, Store, Users, Wallet } from "lucide-react";
```

And change the `NAV` array:
```typescript
const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/orders", label: "Orders", icon: Receipt },
  { href: "/products", label: "Products", icon: Package },
  { href: "/users", label: "Users & Wallet", icon: Users },
];
```
to:
```typescript
const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/orders", label: "Orders", icon: Receipt },
  { href: "/products", label: "Products", icon: Package },
  { href: "/users", label: "Users & Wallet", icon: Users },
  { href: "/deposits", label: "Deposits", icon: Wallet },
];
```

- [ ] **Step 2: Add route protection**

In `admin/src/proxy.ts`, change:
```typescript
export const config = {
  matcher: ["/dashboard/:path*", "/orders/:path*", "/products/:path*", "/users/:path*"],
};
```
to:
```typescript
export const config = {
  matcher: ["/dashboard/:path*", "/orders/:path*", "/products/:path*", "/users/:path*", "/deposits/:path*"],
};
```

- [ ] **Step 3: Type-check it**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add admin/src/components/sidebar.tsx admin/src/proxy.ts
git commit -m "feat(admin): wire /deposits into nav and session-gated routes"
```

---

### Task 5: End-to-end manual verification

**Files:** none (verification only)

This repo's admin panel has no automated test suite (confirmed during design: no test files under `admin/`, no `test` script in `package.json`) — every other feature in this codebase is verified this way, so this matches convention rather than skipping testing.

- [ ] **Step 1: Build the panel**

Run (from `admin/`): `npm run build`
Expected: build succeeds with no type or lint errors.

- [ ] **Step 2: Start it and log in**

Run: `npm run dev`
Open the panel in a browser, log in as an admin (Telegram Login Widget), navigate to the new "Deposits" nav entry. Confirm the page loads without a redirect to `/login` (proves Task 4's matcher change worked) and without a server error (proves Task 3's query/join logic works against the real DB).

- [ ] **Step 3: Exercise the resolve flow against a real pending deposit**

If a genuinely stuck pending deposit already exists in the DB (e.g. the one that prompted this feature), use it. Otherwise create one via the bot's normal top-up flow (`/topup` in Telegram, pick a rail, don't actually send the transfer) so it shows up in the Pending tab.

- Confirm the deposit appears in the Pending tab with the buyer's name/username, tagged amount, and (if its `expires_at` has passed) a "stale" badge.
- Click "Resolve", confirm the amount field is pre-filled with the tagged amount, edit it to a different value (simulating "buyer sent a different amount"), add a note, submit.
- Confirm: the row disappears from Pending (or shows "resolved" if you don't navigate away), a new row appears in the "Manually resolved" tab, and — checking the DB directly or via `users/[id]/page.tsx` for that buyer — the wallet balance increased by exactly the amount you entered (not the original tagged amount) and a `wallet_ledger` row exists with `reason='manual_admin_credit'`.

- [ ] **Step 4: Exercise the double-resolve guard**

With a second pending deposit (or by re-opening dev tools and calling `resolveDeposit` again with the same `depositId` you just resolved), confirm the action throws "This deposit was already resolved" rather than double-crediting.

- [ ] **Step 5: Report results**

No commit for this task — report the verification outcome (pass/fail per step above) back to the user.

---

## Post-plan note

Deferred per the design doc: no Telegram notification to the buyer on manual resolve (panel has no bridge to the bot process — same gap as existing order approve/reject), no automatic expiry/cleanup job for stale deposits, no Postgres RPC for true write atomicity (accepted risk, documented in Task 1 Step 1's `ponytail:` comment and the design doc).
