# Manual "Resolve Stuck Top-up" Admin Feature — Design

## Problem

Wallet top-ups (BSC/Binance Pay) are fully auto-confirmed: a buyer requests an amount, gets a
uniquely-tagged deposit, and a background poller (`app/services/crypto_watch.py`) matches an
incoming transfer's exact amount to that tag and credits the wallet. If the buyer sends a
different amount than tagged (typo, rounding, re-send after a first attempt), or a rail has a
service hiccup, the transfer never matches and the deposit sits as `status='pending'` forever —
`deposits` only has `pending`/`credited` states, nothing marks it expired/failed, and nobody is
notified.

Today there is no admin-facing way to manually credit a wallet or resolve a stuck deposit,
anywhere — not in the bot, not in the admin panel (confirmed by codebase audit: `db.credit_wallet`
has exactly two call sites, both automatic system refunds, no admin-triggered path exists). The
only fix today is a raw, unaudited DB write.

## Goal

Give admins a way, from the Next.js admin panel, to browse pending deposits across all users and
manually resolve one — crediting the buyer's wallet for the amount they actually sent (which may
differ from the tagged amount) and marking the deposit resolved, with a proper `wallet_ledger`
audit trail.

## Non-goals

- No Telegram notification to the buyer when a deposit is manually resolved (the admin panel has
  no bridge to the bot process — confirmed the existing order approve/reject actions in the panel
  have the same gap, so this isn't a new inconsistency). The admin tells the buyer directly if
  needed.
- No automatic expiry/cleanup job for stale pending deposits — the new page's "Stale" badge
  (below) makes them visible to an admin, which is sufficient for now.
- No changes to the auto-matching poller or the `deposits` schema's status enum — this reuses
  `pending`/`credited` as-is.
- No Postgres RPC/stored-procedure introduced. The write is 3 sequential guarded statements
  (matching the existing panel convention used by `orders/actions.ts`'s approve/reject), not a
  transaction. See "Concurrency & failure mode" below for the accepted risk.

## Architecture

A new route segment in the admin panel, `admin/src/app/(admin)/deposits/`, following the exact
pattern already established by `admin/src/app/(admin)/orders/`:

- `page.tsx` — server component, `supabaseServer()`, queries `deposits` joined to `users` for
  display name, split into two tabs via shadcn `Tabs`: **Pending** and **Resolved**. Uses the
  existing `LiveRefresh` component for polling, `export const dynamic = "force-dynamic"`, and
  shadcn `Table`/`Badge` components (not the plain-`<table>` style used in `users/[id]/page.tsx`).
- `actions.ts` — `"use server"`, one exported function: `resolveDeposit(depositId, creditedAmountMicro, note)`.
- A new client component, `deposit-row.tsx` (mirroring `order-row.tsx`), rendering one table row
  with a "Resolve" button that opens an inline form (amount input pre-filled with
  `tagged_amount_micro`, editable; optional note text field) and calls `resolveDeposit` on submit.

**Pending tab contents:** every `deposits` row with `status='pending'`, sorted oldest-first (so
genuinely stuck ones surface at the top). Any row where `expires_at` is in the past gets a
"Stale" badge — those are the ones that will never auto-match and are exactly what this page
exists to surface.

**Resolved tab contents:** deposits where `credited_tx_ref` starts with `manual:` — i.e. only
manually-resolved ones (not every auto-credited deposit ever, which would be noise and is already
visible per-user on `users/[id]/page.tsx`). Read-only, for audit purposes.

## Write sequence (`resolveDeposit`)

Mirrors the existing `approveOrder`/`rejectOrder` convention in `orders/actions.ts`: a guarded
single-row `UPDATE` as the concurrency lock, then the dependent writes, no database transaction.

```
1. UPDATE deposits SET status = 'credited', credited_tx_ref = 'manual:' || $adminTelegramId || (':' || $note if $note else '')
   WHERE id = $depositId AND status = 'pending'
   -- if 0 rows affected: throw "This deposit was already resolved" (handles a double-click
   -- or two admins racing on the same deposit — same guard-clause pattern as orders/actions.ts)

2. UPDATE users SET wallet_balance_micro = wallet_balance_micro + $creditedAmountMicro
   WHERE user_id = $userId

3. INSERT INTO wallet_ledger (user_id, delta_micro, reason, ref, balance_after_micro, created_at)
   VALUES ($userId, $creditedAmountMicro, 'manual_admin_credit', 'deposit:' || $depositId, ..., now())

4. revalidatePath("/deposits")
```

`$creditedAmountMicro` is admin-supplied (defaults to the deposit's `tagged_amount_micro` in the
form, but editable) — this is the whole point: the buyer may have sent a different amount than
what they were tagged for, and the admin is confirming what actually arrived.

`$adminTelegramId` comes from the resolving admin's own session (`getSession()` in
`admin/src/lib/session.ts`, already available server-side to every action) — not user-supplied,
not optional, so `credited_tx_ref` always records *which admin* resolved it, with `$note` as an
optional free-text addition. This gives the audit trail an accountable actor even if the admin
leaves the note blank.

## Concurrency & failure mode (accepted risk)

Step 1's guarded UPDATE is the safety net against two admins resolving the same deposit twice, or
a double-click — only the first caller sees `rows.length > 0` and proceeds. If step 2 or 3 fails
after step 1 succeeds (e.g. a network blip between two sequential Supabase calls), the deposit is
left marked `credited` without the wallet actually being credited — an inconsistency an admin
would need to notice and fix manually (there's no automatic reconciliation). This is the same
risk class the admin panel already accepts elsewhere (no mutation in `admin/` uses a DB
transaction today) — introducing a Postgres RPC/stored function for true atomicity was considered
and explicitly rejected for this iteration to avoid introducing a new pattern into the codebase
for a low-frequency admin action. If manual resolves turn out to be frequent enough that this
partial-failure risk becomes a real problem, an RPC-based rewrite is the natural next step.

## Nav & auth

- Add `{ href: "/deposits", label: "Deposits", icon: Wallet }` (import `Wallet` from
  `lucide-react`) to the `NAV` array in `admin/src/components/sidebar.tsx`.
- Add `/deposits` to the `matcher` array in `admin/src/proxy.ts` so the route is session-gated
  the same way every other admin route is — without this, `/deposits` would be reachable without
  login.

## Testing

This repo's admin panel has no test suite (confirmed: no test files anywhere under `admin/`,
`package.json` has no `test` script). Verification for this feature is manual: build the panel,
log in as admin, create a real pending deposit (or use an existing stuck one), resolve it, and
confirm (a) the wallet balance updates, (b) a `wallet_ledger` row appears with the right reason,
(c) the deposit moves from Pending to Resolved tab, (d) a second resolve attempt on the same
deposit is rejected with the "already resolved" error.
