# Admin Ops & Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four production-readiness gaps identified in the admin-ops audit: no daily sales/deposits pulse, no stall alert if the Binance Pay top-up rail silently breaks, no persisted context on a buyer, and no quick jump from an order to its buyer's history.

**Architecture:** Four independent, additive changes. The digest and Binance Pay alert extend the existing `job_queue`-driven background-job pattern already used for the BSC/Binance Pay pollers in `app/main.py` and the one-shot admin-DM pattern already used for the BSC stall alert in `app/services/crypto_watch.py`. User notes add one column (idempotent `ALTER TABLE`, matching the existing migration style in `app/db/schema.py`) plus one server action in the Next.js panel. The order→buyer link is a two-line JSX change in two files.

**Tech Stack:** Python 3.12+, asyncpg (Postgres), python-telegram-bot v21/22 `JobQueue` (already a dependency, `run_daily` is part of the same API `run_repeating` already uses). Next.js 16 App Router, Supabase JS client, shadcn/ui primitives — all already in `admin/package.json`. No new dependencies. Verification follows this repo's existing convention: `assert`-based self-checks appended to the modules' existing `_demo()` functions (guarded by `config.require_disposable_db_for_selfcheck()`), plus `npm run build` for the admin-panel changes.

---

### Task 1: `DAILY_DIGEST_HOUR_UTC` config + daily aggregate queries

**Files:**
- Modify: `app/config.py` (add env var)
- Modify: `.env.example` (document it)
- Modify: `app/db/orders.py` (add `today_summary`)
- Modify: `app/db/wallet.py` (add `today_deposit_summary`, `pending_deposit_summary`)
- Modify: `app/db/__init__.py` (re-export the three new functions)

- [ ] **Step 1: Add the env var to `app/config.py`**

Add this line right after `STARTUP_RETRIES` (around line 115, in the "Network / connection to Telegram" section — actually place it in a new small section since it's unrelated to networking):

```python
# --- Daily ops digest ---
# UTC hour (0-23) the once-daily admin summary DM is sent. Default 20:00 UTC.
DAILY_DIGEST_HOUR_UTC = _get_int(os.getenv("DAILY_DIGEST_HOUR_UTC", ""), 20)
```

Place it after the `_get_int` function definition (so `_get_int` exists before it's called) — i.e. right after the `CRYPTO_FEE_USDT = _get_float(...)` line (around line 101).

- [ ] **Step 2: Document it in `.env.example`**

Add after the `BINANCE_API_SECRET=` line (around line 44), before the `# --- Premium custom emoji on buttons` section:

```

# --- Daily ops digest (optional) ---
# UTC hour (0-23) the once-daily admin summary DM (sales, deposits, pending
# items) is sent. Default 20 (20:00 UTC).
DAILY_DIGEST_HOUR_UTC=20
```

- [ ] **Step 3: Add `today_summary` to `app/db/orders.py`**

Add at the end of `app/db/orders.py`, after `earnings_summary`:

```python
async def today_summary() -> dict:
    """Approved-order totals for today (UTC calendar day) — powers the admin
    daily digest DM. Separate from earnings_summary(), which is all-time."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount), 0) AS inr, COALESCE(SUM(amount_usdt), 0) AS usdt,
                      COUNT(*) AS n
               FROM orders
               WHERE status = $1 AND created_at >= $2""",
            STATUS_APPROVED, today_start,
        )
        return {"inr": row["inr"], "usdt": row["usdt"], "count": row["n"]}
```

- [ ] **Step 4: Add `today_deposit_summary` and `pending_deposit_summary` to `app/db/wallet.py`**

Add at the end of `app/db/wallet.py`, after `delete_wallet_data_for_users`:

```python
async def today_deposit_summary() -> dict:
    """Credited-deposit totals for today (UTC calendar day) — powers the admin
    daily digest DM."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(tagged_amount_micro), 0) AS micro, COUNT(*) AS n
               FROM deposits
               WHERE status = $1 AND created_at >= $2""",
            DEPOSIT_CREDITED, today_start,
        )
        return {"micro": row["micro"], "count": row["n"]}


async def pending_deposit_summary() -> dict:
    """All currently-pending deposits, regardless of age — powers the admin
    daily digest DM's 'needs attention' line."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(tagged_amount_micro), 0) AS micro, COUNT(*) AS n
               FROM deposits WHERE status = $1""",
            DEPOSIT_PENDING,
        )
        return {"micro": row["micro"], "count": row["n"]}
```

- [ ] **Step 5: Re-export from `app/db/__init__.py`**

In the `from app.db.orders import (...)` block, add `today_summary` alphabetically:

```python
from app.db.orders import (
    clear_abandoned_orders,
    clear_data,
    clear_successful_orders,
    count_user_orders,
    create_order,
    earnings_summary,
    get_order,
    list_abandoned_orders,
    list_orders,
    reject_order,
    set_order_method,
    set_order_status,
    set_order_utr,
    today_summary,
)
```

In the `from app.db.wallet import (...)` block, add `pending_deposit_summary` and `today_deposit_summary`:

```python
from app.db.wallet import (
    create_deposit,
    create_tagged_deposit,
    credit_deposit_once,
    credit_wallet,
    debit_wallet,
    delete_wallet_data_for_users,
    get_cursor,
    get_ledger,
    get_wallet_balance,
    is_tx_processed,
    list_unnotified_manual_credits,
    list_unnotified_rejections,
    mark_deposit_notified,
    pending_deposit_summary,
    set_cursor,
    today_deposit_summary,
)
```

Add all three to `__all__` (alphabetically within their existing groups):

```python
    "create_order", "earnings_summary", "get_order", "list_abandoned_orders",
    "list_orders", "reject_order", "set_order_method", "set_order_status",
    "set_order_utr", "today_summary",
    "all_recipient_ids", "all_users", "delete_users", "get_user", "record_user",
    "create_deposit", "create_tagged_deposit", "credit_deposit_once", "credit_wallet",
    "debit_wallet", "delete_wallet_data_for_users", "get_cursor", "get_ledger",
    "get_wallet_balance", "is_tx_processed", "list_unnotified_manual_credits",
    "list_unnotified_rejections", "mark_deposit_notified", "pending_deposit_summary",
    "set_cursor", "today_deposit_summary",
```

- [ ] **Step 6: Verify config loads**

Run: `python -c "from app import config; print(config.DAILY_DIGEST_HOUR_UTC)"`
Expected: `20`

- [ ] **Step 7: Commit**

```bash
git add app/config.py .env.example app/db/orders.py app/db/wallet.py app/db/__init__.py
git commit -m "feat: add daily-digest aggregate queries and DAILY_DIGEST_HOUR_UTC config"
```

---

### Task 2: Send the daily digest DM

**Files:**
- Modify: `app/services/crypto_watch.py` (add `send_daily_digest`)
- Modify: `app/main.py` (schedule the job)

**Problem:** No way to see "how did today go" without opening the admin panel — everything else is instant single-event DMs.

- [ ] **Step 1: Add `send_daily_digest` to `app/services/crypto_watch.py`**

Add after `_alert_admins_stall` (around line 78, before the `# BSC` section header):

```python
async def send_daily_digest(context) -> None:
    """PTB job: once a day, DM every admin a one-message summary of today's
    sales/deposits and what's still waiting on them. Plain f-string formatting
    (not app.formatting.money()/usdt()) because those helpers render 0 as ""
    / "Free" for buyer-facing prices — a digest needs to show "₹0" on a slow
    day, not a blank field."""
    orders_today = await db.today_summary()
    deposits_today = await db.today_deposit_summary()
    deposits_pending = await db.pending_deposit_summary()
    pending_orders = await db.list_orders(status=db.STATUS_PENDING, limit=1000)

    today = time.strftime("%Y-%m-%d", time.gmtime())
    text = (
        f"{cemoji('chart', '📊')} Daily summary — {today}\n\n"
        f"Sales: ₹{orders_today['inr']:,.0f} · ${orders_today['usdt']:.2f} USDT "
        f"({orders_today['count']} order{'s' if orders_today['count'] != 1 else ''})\n"
        f"Deposits credited: ${deposits_today['micro'] / 1_000_000:.2f} USDT "
        f"({deposits_today['count']})\n\n"
        f"Pending now: {len(pending_orders)} order{'s' if len(pending_orders) != 1 else ''} · "
        f"{deposits_pending['count']} deposit{'s' if deposits_pending['count'] != 1 else ''} "
        f"(${deposits_pending['micro'] / 1_000_000:.2f} USDT)"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not DM admin %s the daily digest: %s", admin_id, e)
```

- [ ] **Step 2: Schedule it in `app/main.py`**

In `app/main.py`, add the `datetime`/`time` import needed for `run_daily`'s `time=` argument. Check the top of the file for existing imports first — if `import datetime` (or `from datetime import time`) isn't already present, add:

```python
from datetime import time as dt_time
```

Then, in `build_application()`, right after the existing background-job block (after the `poll_manual_rejections` line, around line 403):

```python
    # Once-daily ops summary DM — independent of whether wallet top-ups are
    # enabled at all, since it also reports order sales.
    app.job_queue.run_daily(
        crypto_watch.send_daily_digest,
        time=dt_time(hour=config.DAILY_DIGEST_HOUR_UTC, minute=0),
    )
```

- [ ] **Step 3: Add a runnable self-check**

In `app/services/crypto_watch.py`'s `_demo()`, add this block right before the final `finally:` (after the `list_unnotified_manual_credits`/`_notify_credited`/`mark_deposit_notified` block added by the prior production-readiness plan, i.e. right before line ~461's `finally:`):

```python
        fake_ctx3 = _FakeContext()
        await send_daily_digest(fake_ctx3)
        assert len(fake_ctx3.bot.sent) == len(config.ADMIN_IDS), (
            "daily digest should DM every configured admin exactly once"
        )
```

- [ ] **Step 4: Run the self-check**

Run (PowerShell): `$env:ALLOW_SELFCHECK_DB=1; python -m app.services.crypto_watch`
Expected: `crypto_watch self-check: all assertions passed`

- [ ] **Step 5: Commit**

```bash
git add app/services/crypto_watch.py app/main.py
git commit -m "feat: DM admins a once-daily sales/deposits digest"
```

---

### Task 3: Binance Pay stall alert (parity with BSC)

**Files:**
- Modify: `app/services/crypto_watch.py`

**Problem:** `poll_binance_pay` (line ~237) silently logs and returns on any exception — an expired API key or Binance outage goes unnoticed until a buyer complains. `poll_bsc` already has a one-shot stall DM (`_alert_admins_stall`); Binance Pay has no block-height concept, so this uses consecutive-failure count instead.

- [ ] **Step 1: Add the failure-tracking state and alert helper**

In `app/services/crypto_watch.py`, add next to the existing `_STALL_WARNING_BLOCKS = 1000` / `_stall_alerted = False` lines (around line 132-133):

```python
_BINANCE_PAY_FAILURE_THRESHOLD = 5  # ~2 min at the existing 25s poll interval
_binance_pay_consecutive_failures = 0
_binance_pay_alerted = False
```

Add this helper right after `_alert_admins_stall` (around line 78):

```python
async def _alert_admins_binance_pay_stall(context, consecutive_failures: int) -> None:
    """DM every admin once when the Binance Pay poller has failed this many
    times in a row (e.g. an expired API key, or Binance's API down) — same
    one-shot pattern as _alert_admins_stall, but keyed on consecutive
    failures since Binance Pay polling has no block-height cursor to measure
    'behind' against. Resets in poll_binance_pay on the next successful poll."""
    global _binance_pay_alerted
    if _binance_pay_alerted:
        return
    _binance_pay_alerted = True
    text = (
        f"{cemoji('warn', '⚠️')} Binance Pay deposit poller has failed "
        f"{consecutive_failures} times in a row. Wallet top-ups on this rail may be "
        f"delayed. Check BINANCE_API_KEY/BINANCE_API_SECRET and Binance API status."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not DM admin %s about Binance Pay stall: %s", admin_id, e)
```

- [ ] **Step 2: Wire it into `poll_binance_pay`**

Replace the existing `poll_binance_pay` exception branch:

```python
    try:
        txns = _fetch_pay_transactions()
    except Exception as e:  # noqa: BLE001
        logger.warning("Binance Pay poll failed (will retry next tick): %s", e)
        return
```

with:

```python
    global _binance_pay_consecutive_failures, _binance_pay_alerted
    try:
        txns = _fetch_pay_transactions()
    except Exception as e:  # noqa: BLE001
        logger.warning("Binance Pay poll failed (will retry next tick): %s", e)
        _binance_pay_consecutive_failures += 1
        if _binance_pay_consecutive_failures >= _BINANCE_PAY_FAILURE_THRESHOLD:
            await _alert_admins_binance_pay_stall(context, _binance_pay_consecutive_failures)
        return
    _binance_pay_consecutive_failures = 0
    _binance_pay_alerted = False
```

- [ ] **Step 3: Add a runnable self-check**

Add to `_demo()`, right after the existing `_alert_admins_stall` self-check block (after the `_stall_alerted = False` reset line, around line 417):

```python
        global _binance_pay_alerted, _binance_pay_consecutive_failures
        _binance_pay_alerted = False
        fake_ctx_bp = _FakeContext()
        await _alert_admins_binance_pay_stall(fake_ctx_bp, 5)
        assert len(fake_ctx_bp.bot.sent) == len(config.ADMIN_IDS), (
            "Binance Pay stall alert should DM every configured admin once"
        )
        await _alert_admins_binance_pay_stall(fake_ctx_bp, 6)
        assert len(fake_ctx_bp.bot.sent) == len(config.ADMIN_IDS), (
            "a second stall while already alerted must not re-send (one-shot until caught up)"
        )
        _binance_pay_alerted = False
        _binance_pay_consecutive_failures = 0
```

- [ ] **Step 4: Run the self-check**

Run (PowerShell): `$env:ALLOW_SELFCHECK_DB=1; python -m app.services.crypto_watch`
Expected: `crypto_watch self-check: all assertions passed`

- [ ] **Step 5: Commit**

```bash
git add app/services/crypto_watch.py
git commit -m "feat: DM admins once when the Binance Pay deposit poller fails repeatedly"
```

---

### Task 4: Persisted notes per user — schema + Python

**Files:**
- Modify: `app/db/schema.py` (migration)
- Modify: `app/db/users.py` (add `set_user_notes`)
- Modify: `app/db/__init__.py` (re-export)

- [ ] **Step 1: Add the migration**

In `app/db/schema.py::init_pool()`, add after the existing `orders.qty` migration line (around line 175):

```python
        # Freeform admin context on a buyer (e.g. "disputed a charge once,
        # watch for repeat"), editable from the admin panel's user detail page.
        # Single field, overwritten on save — not a timestamped note history.
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT ''")
```

- [ ] **Step 2: Add `set_user_notes` to `app/db/users.py`**

Add after `get_user`:

```python
async def set_user_notes(user_id: int, notes: str) -> None:
    async with _connect() as conn:
        await conn.execute("UPDATE users SET notes = $1 WHERE user_id = $2", notes, user_id)
```

- [ ] **Step 3: Re-export from `app/db/__init__.py`**

Change:

```python
from app.db.users import all_recipient_ids, all_users, delete_users, get_user, record_user
```

to:

```python
from app.db.users import all_recipient_ids, all_users, delete_users, get_user, record_user, set_user_notes
```

And in `__all__`, change:

```python
    "all_recipient_ids", "all_users", "delete_users", "get_user", "record_user",
```

to:

```python
    "all_recipient_ids", "all_users", "delete_users", "get_user", "record_user", "set_user_notes",
```

- [ ] **Step 4: Verify the migration runs**

Run (PowerShell): `$env:ALLOW_SELFCHECK_DB=1; python -c "import asyncio; from app import db; asyncio.run(db.init_pool())"`
Expected: no error (confirms the `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` runs cleanly against the scratch DB). Point `DATABASE_URL` at your scratch DB for this, same as any other self-check.

- [ ] **Step 5: Commit**

```bash
git add app/db/schema.py app/db/users.py app/db/__init__.py
git commit -m "feat: add persisted notes column per user"
```

---

### Task 5: Persisted notes per user — admin panel UI

**Files:**
- Create: `admin/src/app/(admin)/users/notes-actions.ts`
- Modify: `admin/src/app/(admin)/users/[id]/page.tsx`

**Depends on:** Task 4 (the `users.notes` column must exist).

- [ ] **Step 1: Create the server action**

Create `admin/src/app/(admin)/users/notes-actions.ts`:

```ts
"use server";
import { revalidatePath } from "next/cache";
import { supabaseServer } from "@/lib/supabase-server";
import { getSession } from "@/lib/session";

export async function updateUserNote(userId: number, notes: string) {
  const session = await getSession();
  if (!session) throw new Error("Not authenticated");

  const db = supabaseServer();
  const { error } = await db.from("users").update({ notes }).eq("user_id", userId);
  if (error) throw new Error(error.message);

  revalidatePath(`/users/${userId}`);
}
```

- [ ] **Step 2: Add a notes card to the user detail page**

In `admin/src/app/(admin)/users/[id]/page.tsx`, this needs a client component for the editable textarea (the page itself is an async server component). Create `admin/src/components/user-notes.tsx`:

```tsx
"use client";
import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { updateUserNote } from "@/app/(admin)/users/notes-actions";

export function UserNotes({ userId, initialNotes }: { userId: number; initialNotes: string }) {
  const [notes, setNotes] = useState(initialNotes);
  const [pending, startTransition] = useTransition();

  function save() {
    startTransition(async () => {
      try {
        await updateUserNote(userId, notes);
        toast.success("Note saved");
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to save note");
      }
    });
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <p className="mb-2 text-sm font-medium text-[var(--ink)]">Notes</p>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="e.g. disputed a charge once, watch for repeat"
        rows={3}
        className="w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm text-[var(--ink)] outline-none placeholder:text-[var(--ink-muted)] focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      />
      <Button size="sm" className="mt-2" disabled={pending} onClick={save}>
        Save note
      </Button>
    </div>
  );
}
```

- [ ] **Step 3: Wire it into the user detail page**

In `admin/src/app/(admin)/users/[id]/page.tsx`, add the import:

```tsx
import { UserNotes } from "@/components/user-notes";
```

And add `<UserNotes userId={userId} initialNotes={user?.notes ?? ""} />` right after the header block (after the closing `</div>` of the `<h1>`/joined-date block, before the `{balanceMismatch && (...)}` block).

- [ ] **Step 4: Verify**

Run: `cd admin && npm run build`
Expected: succeeds (no TypeScript errors).

Manual check: `npm run dev`, log in, open a user detail page, type a note, click "Save note", reload the page, confirm the note persisted.

- [ ] **Step 5: Commit**

```bash
git add admin/src/app/(admin)/users/notes-actions.ts admin/src/components/user-notes.tsx "admin/src/app/(admin)/users/[id]/page.tsx"
git commit -m "feat: add persisted per-user notes to the admin panel"
```

---

### Task 6: Link orders to buyer history

**Files:**
- Modify: `admin/src/components/order-row.tsx`
- Modify: `admin/src/app/(admin)/dashboard/page.tsx`

**Problem:** The buyer name/username on both the orders page and the dashboard's recent-activity table is plain text — no way to jump to `/users/[id]`, which already exists and already shows that buyer's wallet/order history.

- [ ] **Step 1: Link it in `order-row.tsx`**

Add the import:

```tsx
import Link from "next/link";
```

Replace:

```tsx
      <TableCell className="text-[var(--ink-muted)]">{order.username || order.user_id}</TableCell>
```

with:

```tsx
      <TableCell>
        <Link href={`/users/${order.user_id}`} className="text-[var(--brand-violet-to)] hover:underline">
          {order.username || order.user_id}
        </Link>
      </TableCell>
```

- [ ] **Step 2: Link it in the dashboard's recent-activity table**

In `admin/src/app/(admin)/dashboard/page.tsx`, replace:

```tsx
                  <TableCell className="text-[var(--ink-muted)]">
                    {order.username || order.user_id}
                  </TableCell>
```

with:

```tsx
                  <TableCell>
                    <Link
                      href={`/users/${order.user_id}`}
                      className="text-[var(--brand-violet-to)] hover:underline"
                    >
                      {order.username || order.user_id}
                    </Link>
                  </TableCell>
```

(`Link` is already imported at the top of `dashboard/page.tsx` — no new import needed there.)

- [ ] **Step 3: Verify**

Run: `cd admin && npm run build`
Expected: succeeds.

Manual check: `npm run dev`, open `/orders` and `/dashboard`, click a buyer name, confirm it navigates to `/users/[id]` and shows that buyer's wallet ledger/deposits.

- [ ] **Step 4: Commit**

```bash
git add admin/src/components/order-row.tsx "admin/src/app/(admin)/dashboard/page.tsx"
git commit -m "feat: link order rows to their buyer's detail page"
```

---

### Task 7: Final verification pass

**Files:** none (verification only)

- [ ] **Step 1: Full Python self-checks**

Run (PowerShell), pointed at a scratch `DATABASE_URL`:
```powershell
$env:ALLOW_SELFCHECK_DB=1; python -m app.services.crypto_watch
python -m app.db.products
```
Expected: both print `... self-check: all assertions passed`.

- [ ] **Step 2: Admin panel build**

Run: `cd admin && npm run build`
Expected: succeeds with no TypeScript errors.

- [ ] **Step 3: Manual smoke test**

`npm run dev`, log in, and confirm:
- A user detail page shows the Notes card, saves and persists a note.
- An order row's buyer name links to that buyer's `/users/[id]` page.
- Dashboard's recent-activity buyer names also link correctly.

For the two Python-side features (digest DM, Binance Pay stall alert), these only fire in the real running bot process (`python bot.py`) at the scheduled hour / on repeated failure — the self-checks in Step 1 are the verification for their logic; there's no separate manual UI to click through.

- [ ] **Step 4: Commit (if any fixes were made during verification)**

```bash
git add -A
git commit -m "Fix issues found during admin ops/automation verification pass"
```
