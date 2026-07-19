# Admin Ops & Automation — Design

**Goal:** Close the gaps found in a production-readiness audit of the admin surface (Telegram bot admin flows + Next.js admin panel) that matter for a solo operator running this publicly: catching a silently-broken deposit rail, getting a daily pulse without babysitting logs, and having enough context on a buyer without leaving the panel.

**Scope decided with the user:** solo operator (no multi-admin audit log needed), no auto-approval changes (every order/deposit still needs a manual click), no refund/reverse-credit UI, no rate-limiting on the admin login route, no broadcast UI in the panel, panel is already deployed (no deployment setup needed here).

---

## 1. Nightly digest DM

**Problem:** All admin notifications today are instant, single-event Telegram DMs (`app/services/crypto_watch.py`, `app/handlers/payments.py`). There's no daily rollup — the only way to see "how did today go" is opening the admin panel.

**Design:** A new `job_queue.run_daily()` job in `app/main.py`, registered alongside the existing `run_repeating` poller jobs, firing once per day at an hour set by a new `DAILY_DIGEST_HOUR_UTC` env var (default `20`, i.e. 20:00 UTC — documented in `.env.example` as adjustable). The job callback (new function `send_daily_digest` in `app/services/crypto_watch.py`, next to the other admin-facing notification helpers) queries:

- Approved orders today: count, revenue INR sum, revenue USDT sum
- Deposits credited today: count, USDT sum
- Currently pending: order count, deposit count + USDT sum

via new small aggregate query functions added to `app/db/orders.py` and `app/db/wallet.py` (following the existing facade pattern — re-exported through `app/db/__init__.py`), each a single `SELECT ... WHERE created_at >= $1` (today, UTC midnight) using the connection-pool pattern already used throughout `app/db/`.

Formats one HTML-parse-mode message (reusing `app/formatting.py`'s `money()`/`usdt()` helpers) and sends it to every `config.ADMIN_IDS` via `context.bot.send_message`, matching the existing DM style (see `_alert_admins_stall`). No new dependency — `job_queue.run_daily` is already part of `python-telegram-bot`'s `JobQueue`, same import already in use.

**Message shape:**
```
📊 Daily summary — 2026-07-18

Sales: ₹1,240 · $18.50 USDT (6 orders)
Deposits credited: $42.00 USDT (3)

Pending now: 2 orders · 1 deposit ($5.00 USDT)
```

**Error handling:** wrapped in the same `try/except log-and-continue` pattern every other job in this codebase uses — a digest failure must never crash the polling loop.

## 2. Binance Pay stall alert (parity with BSC)

**Problem:** `poll_bsc` already DMs admins once if it falls stall-level behind (`_alert_admins_stall`, `app/services/crypto_watch.py:59-77`). `poll_binance_pay` (`crypto_watch.py:237+`) has no equivalent — on exception it just logs a warning and returns. A revoked/expired API key or Binance-side outage goes unnoticed until a buyer complains their top-up never arrived.

**Design:** Binance Pay has no block-height concept to measure "behind," so the trigger is **consecutive poll failures** instead. Two new module-level vars next to the existing `_stall_alerted`:

```python
_binance_pay_consecutive_failures = 0
_binance_pay_alerted = False
_BINANCE_PAY_FAILURE_THRESHOLD = 5  # ~2 min at the existing 25s poll interval
```

In `poll_binance_pay`'s `except` branch: increment the counter; once it crosses the threshold, call a new one-shot `_alert_admins_binance_pay_stall(context, consecutive_failures)` (same shape/reuse pattern as `_alert_admins_stall` — DM every admin once, reset only on the next successful poll). On the success path (after `_fetch_pay_transactions()` succeeds), reset both the counter and the alerted flag to `False`, mirroring how `poll_bsc` resets `_stall_alerted` once caught up.

## 3. Persisted notes per user

**Problem:** No way to leave context on a user ("disputed a charge once", "asked about bulk pricing") that survives between admin sessions — `deposits`' resolve-dialog `note` field is folded into the `credited_tx_ref` string and isn't a real per-user note.

**Design:**
- Schema: `ALTER TABLE users ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT ''` added to `app/db/schema.py::init_pool()`'s existing migration block (same idempotent pattern as the `products.icon_char`/`orders.qty` migrations already there).
- `app/db/users.py`: new `get_user(user_id)` (if not already returning `notes` via `SELECT *`) and `set_user_notes(user_id, notes: str)` — a plain `UPDATE users SET notes = $1 WHERE user_id = $2`. Re-exported via `app/db/__init__.py`.
- Admin panel: `admin/src/app/(admin)/users/[id]/page.tsx` gains a small card — a `Textarea` (shadcn, already available via other form usage in the panel) pre-filled with the current note, a "Save note" button wired to a new server action `updateUserNote(userId, notes)` in a new `admin/src/app/(admin)/users/notes-actions.ts` (mirrors `deposit-actions.ts`'s auth-check-then-`supabaseServer()`-write shape), `revalidatePath("/users/[id]", "page")` on success.
- Not in scope: a notes *history/log* (multiple timestamped notes) — this is a single freeform field, overwritten on save. Upgrade path if that's ever needed: a separate `user_notes` table.

## 4. Quick win: link orders → buyer history

**Problem:** The orders page shows a buyer's name/username as plain text (`admin/src/components/order-row.tsx` and the recent-activity table on `admin/src/app/(admin)/dashboard/page.tsx`) with no way to jump to that buyer's wallet/order history (`/users/[id]`), which already exists and already shows exactly that.

**Design:** Wrap the buyer name/username cell in both places with `<Link href={`/users/${order.user_id}`}>`, styled as a subtle link (matching the existing `text-[var(--brand-violet-to)] hover:underline` convention used elsewhere in the panel, e.g. "View all orders").

---

## Out of scope (explicit)

Refund/reverse-wallet-credit action, rate-limiting on `/api/auth/telegram`, an admin-action audit log, a broadcast/announcement page in the Next.js panel, and any changes to auto-approval behavior — all deferred per the user's answers during scoping. Revisit if the operator count grows past one, or if abuse/disputes become frequent enough to need them.

## Testing

Per this repo's existing convention (no pytest suite): the new `app/db` aggregate functions and `send_daily_digest`/the Binance Pay stall-alert helper get a runnable `assert`-based self-check appended to the relevant module's existing `_demo()` (guarded by `config.require_disposable_db_for_selfcheck()`, same as the existing BSC stall-alert self-check). The admin-panel changes (notes textarea, order→buyer links) are verified with `npm run build` (typecheck) plus a manual click-through, matching how prior admin-panel work in this project was verified — no test runner is configured there.
