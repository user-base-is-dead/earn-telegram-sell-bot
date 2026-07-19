# Wallet transaction ledger

## Problem

`app/db/wallet.py` mutates `users.wallet_balance_micro` directly (`credit_wallet`/
`debit_wallet`). There is no record of individual balance changes — only the
running total. If the total is ever wrong (a bug, a race, a manual DB edit),
there's no history to diff against or reconcile from, and no way to answer
"why is this user's balance X" beyond re-deriving it from scattered `orders`/
`deposits` rows.

## Goal

Every balance change is recorded as an immutable ledger row, written in the
same DB transaction as the balance update, so the cached balance can never
drift from its history — and that invariant is asserted by a self-check, not
just assumed.

## Schema

New table in `app/db/schema.py::init_db()`:

```sql
CREATE TABLE IF NOT EXISTS wallet_ledger (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    delta_micro       INTEGER NOT NULL,   -- positive = credit, negative = debit
    reason            TEXT    NOT NULL,   -- 'deposit_bsc' | 'deposit_binance_pay' | 'purchase' | 'refund_stockout' | 'admin_adjust'
    ref               TEXT    NOT NULL DEFAULT '',  -- tx hash / txn id / order id, context-dependent
    balance_after_micro INTEGER NOT NULL, -- running balance snapshot, for cheap point-in-time reads
    created_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user ON wallet_ledger(user_id);
```

Migration guard (existing DBs): `CREATE TABLE IF NOT EXISTS` already handles
this — no `ALTER TABLE` needed since it's a new table, not a new column.

## Behavior change

`app/db/wallet.py`:

- `credit_wallet(user_id, amount_micro, reason, ref="")` — now takes `reason`
  (and optional `ref`). In one `_connect()` transaction: update
  `wallet_balance_micro`, then insert the ledger row with the resulting
  balance as `balance_after_micro`.
- `debit_wallet(user_id, amount_micro, reason, ref="")` — same, on the debit
  path, only if the balance covers it (unchanged early-return behavior).

Every existing call site adds a `reason` (and `ref` where a natural one
exists — tx hash, order id):

- `app/services/crypto_watch.py::_credit_if_matched` → `reason="deposit_bsc"`
  or `"deposit_binance_pay"`, `ref=tx_ref`
- `app/handlers/topup.py::_pay_from_wallet` debit → `reason="purchase"`,
  `ref=str(order_id)`
- `app/handlers/topup.py::_pay_from_wallet` stockout refund → `reason="refund_stockout"`,
  `ref=str(order_id)`

No other call sites credit/debit the wallet today.

## Verification

Extend `app/services/crypto_watch.py::_demo()`: after the existing
credit/replay/unmatched assertions, add
`assert sum(ledger deltas for user) == get_wallet_balance(user)` — the actual
invariant this feature exists to guarantee.

## Admin visibility

`view_db.py` gets a `--ledger <user_id>` flag: prints that user's
`wallet_ledger` rows (time, reason, delta, balance_after, ref) oldest-first,
for manual reconciliation. No bot-facing UI change.

## Out of scope

- New payment rails — BSC/Binance Pay unchanged.
- User-facing transaction history screen.
- Double-entry accounting — single-row-per-event ledger is sufficient at this
  scale; revisit if multi-currency or inter-user transfers are ever added.
