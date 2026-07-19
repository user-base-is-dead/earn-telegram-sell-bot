# Wallet Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every wallet balance change is recorded as an immutable ledger row, written in the same DB transaction as the balance update, so the cached balance can never drift from its history.

**Architecture:** One new table (`wallet_ledger`) in `app/db/schema.py`. `credit_wallet`/`debit_wallet` in `app/db/wallet.py` gain a required `reason` param and write the ledger row inside the same `_connect()` transaction as the balance update. All three call sites (`crypto_watch.py` x1, `topup.py` x2) pass a reason. No pytest in this repo — verification follows the existing convention: extend `crypto_watch.py`'s `_demo()` self-check (`python -m app.services.crypto_watch`) with an assertion that `SUM(ledger deltas) == balance`.

**Tech Stack:** Python 3, sqlite3 (stdlib), existing `app/db/schema.py::_connect()` transaction helper.

---

### Task 1: Add `wallet_ledger` table

**Files:**
- Modify: `app/db/schema.py`

- [ ] **Step 1: Add the table to `init_db()`**

In `app/db/schema.py`, inside the `conn.executescript(...)` call in `init_db()`, add this block right after the `deposits` table's index (after line 117, before the `product_keys` table comment):

```sql
            -- Immutable log of every wallet balance change (topup, purchase, refund,
            -- admin adjustment). Written in the same transaction as the balance update
            -- in app/db/wallet.py, so balance_after_micro can never drift from this log —
            -- SUM(delta_micro) for a user always equals their current balance.
            CREATE TABLE IF NOT EXISTS wallet_ledger (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id              INTEGER NOT NULL,
                delta_micro          INTEGER NOT NULL,
                reason               TEXT    NOT NULL,
                ref                  TEXT    NOT NULL DEFAULT '',
                balance_after_micro  INTEGER NOT NULL,
                created_at           TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user ON wallet_ledger(user_id);
```

This is a new table, so `CREATE TABLE IF NOT EXISTS` is sufficient — no `ALTER TABLE` migration needed for existing DBs (unlike the column additions further down in the same function).

- [ ] **Step 2: Verify the schema loads**

Run: `python -c "from app import config; config.DB_PATH = 'tmp_schema_check.db'; from app import db; db.init_db(); import sqlite3; conn = sqlite3.connect('tmp_schema_check.db'); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]); conn.close(); import os; os.remove('tmp_schema_check.db')"`

Expected: prints a list of table names including `wallet_ledger`, no traceback.

- [ ] **Step 3: Commit**

```bash
git add app/db/schema.py
git commit -m "Add wallet_ledger table for auditable balance history"
```

---

### Task 2: Write ledger rows atomically in `credit_wallet`/`debit_wallet`

**Files:**
- Modify: `app/db/wallet.py:19-39`

- [ ] **Step 1: Replace `credit_wallet` and `debit_wallet`**

Current code (`app/db/wallet.py:19-39`):

```python
def credit_wallet(user_id: int, amount_micro: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro + ? WHERE user_id = ?",
            (amount_micro, user_id),
        )


def debit_wallet(user_id: int, amount_micro: int) -> bool:
    """Atomically deduct if the balance covers it. Returns False (no-op) if insufficient."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT wallet_balance_micro FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or row["wallet_balance_micro"] < amount_micro:
            return False
        conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro - ? WHERE user_id = ?",
            (amount_micro, user_id),
        )
        return True
```

Replace with:

```python
def _log_ledger(conn: sqlite3.Connection, user_id: int, delta_micro: int, reason: str, ref: str) -> None:
    balance_after = conn.execute(
        "SELECT wallet_balance_micro FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()["wallet_balance_micro"]
    conn.execute(
        """INSERT INTO wallet_ledger (user_id, delta_micro, reason, ref, balance_after_micro, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, delta_micro, reason, ref, balance_after, _now()),
    )


def credit_wallet(user_id: int, amount_micro: int, reason: str, ref: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro + ? WHERE user_id = ?",
            (amount_micro, user_id),
        )
        _log_ledger(conn, user_id, amount_micro, reason, ref)


def debit_wallet(user_id: int, amount_micro: int, reason: str, ref: str = "") -> bool:
    """Atomically deduct if the balance covers it. Returns False (no-op) if insufficient."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT wallet_balance_micro FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row or row["wallet_balance_micro"] < amount_micro:
            return False
        conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro - ? WHERE user_id = ?",
            (amount_micro, user_id),
        )
        _log_ledger(conn, user_id, -amount_micro, reason, ref)
        return True
```

Both `UPDATE` and the `_log_ledger` insert happen inside the same `with _connect() as conn:` block, which commits once on exit (see `_connect()` in `app/db/schema.py:30-46`) — so a crash between them can't leave one written without the other.

- [ ] **Step 2: Add a ledger read helper for admin visibility (needed by Task 5)**

Append to `app/db/wallet.py`:

```python
def get_ledger(user_id: int) -> list[sqlite3.Row]:
    """A user's full transaction history, oldest first."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM wallet_ledger WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
```

- [ ] **Step 3: Export `get_ledger` through the facade**

`app/db/__init__.py` re-exports every public db function (see its module docstring). Add `get_ledger` to the `from app.db.wallet import (...)` block and to `__all__`:

In `app/db/__init__.py`, change:

```python
from app.db.wallet import (
    create_deposit,
    credit_wallet,
    debit_wallet,
    find_matching_deposit,
    get_cursor,
    get_wallet_balance,
    is_tx_processed,
    mark_deposit_credited,
    mark_tx_processed,
    pending_tagged_amounts,
    set_cursor,
)
```

to:

```python
from app.db.wallet import (
    create_deposit,
    credit_wallet,
    debit_wallet,
    find_matching_deposit,
    get_cursor,
    get_ledger,
    get_wallet_balance,
    is_tx_processed,
    mark_deposit_credited,
    mark_tx_processed,
    pending_tagged_amounts,
    set_cursor,
)
```

And in the same file's `__all__` list, change:

```python
    "create_deposit", "credit_wallet", "debit_wallet", "find_matching_deposit",
    "get_cursor", "get_wallet_balance", "is_tx_processed",
    "mark_deposit_credited", "mark_tx_processed", "pending_tagged_amounts",
    "set_cursor",
```

to:

```python
    "create_deposit", "credit_wallet", "debit_wallet", "find_matching_deposit",
    "get_cursor", "get_ledger", "get_wallet_balance", "is_tx_processed",
    "mark_deposit_credited", "mark_tx_processed", "pending_tagged_amounts",
    "set_cursor",
```

- [ ] **Step 4: Verify it imports cleanly**

Run: `python -c "from app import db; print(db.credit_wallet, db.debit_wallet, db.get_ledger)"`

Expected: prints three function objects, no traceback.

- [ ] **Step 5: Commit**

```bash
git add app/db/wallet.py app/db/__init__.py
git commit -m "Log every credit/debit to wallet_ledger in the same transaction"
```

---

### Task 3: Update call sites to pass `reason`/`ref`

**Files:**
- Modify: `app/services/crypto_watch.py:33-45`
- Modify: `app/handlers/topup.py:186` and `app/handlers/topup.py:198`

- [ ] **Step 1: `crypto_watch._credit_if_matched`**

Current (`app/services/crypto_watch.py:33-45`):

```python
def _credit_if_matched(rail: str, tx_ref: str, amount_micro: int) -> Optional[int]:
    """If `amount_micro` matches a pending deposit on `rail` and `tx_ref` hasn't
    already been credited, credit the wallet. Returns the credited user_id, or
    None if nothing matched / it was already processed."""
    if db.is_tx_processed(rail, tx_ref):
        return None
    dep = db.find_matching_deposit(rail, amount_micro)
    if not dep:
        return None
    db.credit_wallet(dep["user_id"], amount_micro)
    db.mark_deposit_credited(dep["id"], tx_ref)
    db.mark_tx_processed(rail, tx_ref)
    return dep["user_id"]
```

Change the `db.credit_wallet` call to:

```python
    db.credit_wallet(dep["user_id"], amount_micro, reason=f"deposit_{rail}", ref=tx_ref)
```

(`rail` is `db.RAIL_BSC` = `"bsc"` or `db.RAIL_BINANCE_PAY` = `"binance_pay"`, so this produces `"deposit_bsc"` / `"deposit_binance_pay"` exactly as specced.)

- [ ] **Step 2: `topup._pay_from_wallet` — purchase debit**

Current (`app/handlers/topup.py:185-186`):

```python
    price_micro = int(Decimal(str(p["price"])) * 1_000_000)
    if not db.debit_wallet(user.id, price_micro):
```

Change to:

```python
    price_micro = int(Decimal(str(p["price"])) * 1_000_000)
    if not db.debit_wallet(user.id, price_micro, reason="purchase", ref=str(product_id)):
```

`order_id` doesn't exist yet at this point in the function (it's created a few lines later at `app/handlers/topup.py:194`), so `ref` uses `product_id`, which is already in scope.

- [ ] **Step 3: `topup._pay_from_wallet` — stockout refund**

Current (`app/handlers/topup.py:196-199`):

```python
    code = db.pop_unused_key(product_id, order_id)
    if code is None:
        db.credit_wallet(user.id, price_micro)  # lost the race for the last key: refund
        await _reply(f"{cemoji('warn', '⚠️')} Just sold out — refunded to your wallet.")
        return
```

Change the `db.credit_wallet` call to:

```python
        db.credit_wallet(user.id, price_micro, reason="refund_stockout", ref=str(order_id))  # lost the race for the last key: refund
```

- [ ] **Step 4: Verify no other call sites were missed**

Run: `python -c "
import re, pathlib
for p in pathlib.Path('app').rglob('*.py'):
    for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
        if re.search(r'\b(credit_wallet|debit_wallet)\(', line) and 'def ' not in line:
            print(p, i, line.strip())
"`

Expected: exactly 3 lines printed (the ones just edited in `crypto_watch.py` and `topup.py`), each showing `reason=` in the call. If any call site is missing `reason=`, go back and fix it.

- [ ] **Step 5: Commit**

```bash
git add app/services/crypto_watch.py app/handlers/topup.py
git commit -m "Tag every wallet credit/debit call site with a ledger reason"
```

---

### Task 4: Assert the ledger invariant in the self-check

**Files:**
- Modify: `app/services/crypto_watch.py:220-258` (`_demo()`)

- [ ] **Step 1: Add ledger assertions to `_demo()`**

In `_demo()`, right after this existing block (`app/services/crypto_watch.py:238-241`):

```python
        credited = _credit_if_matched(db.RAIL_BSC, "0xtx1", 10_010_000)
        assert credited == 1, "should credit user 1's deposit, not user 2's"
        assert db.get_wallet_balance(1) == 10_010_000
        assert db.get_wallet_balance(2) == 0
```

add:

```python
        ledger_1 = db.get_ledger(1)
        assert len(ledger_1) == 1, "one credit should write exactly one ledger row"
        assert ledger_1[0]["delta_micro"] == 10_010_000
        assert ledger_1[0]["reason"] == "deposit_bsc"
        assert ledger_1[0]["balance_after_micro"] == 10_010_000
```

Then, near the end of `_demo()`, right before the final `print("crypto_watch self-check: all assertions passed")` (`app/services/crypto_watch.py:258`), add a debit and the reconciliation assertion that's the actual point of this feature:

```python
        db.debit_wallet(1, 3_000_000, reason="purchase", ref="999")
        db.credit_wallet(1, 500_000, reason="refund_stockout", ref="999")
        ledger_total = sum(row["delta_micro"] for row in db.get_ledger(1))
        assert ledger_total == db.get_wallet_balance(1), (
            "ledger must always sum to the cached balance — if this fails, a "
            "credit/debit path is updating the balance without logging it"
        )
```

- [ ] **Step 2: Run the self-check**

Run: `python -m app.services.crypto_watch`

Expected: `crypto_watch self-check: all assertions passed` with no traceback.

- [ ] **Step 3: Commit**

```bash
git add app/services/crypto_watch.py
git commit -m "Assert ledger sums always match cached wallet balance"
```

---

### Task 5: Admin ledger dump in `view_db.py`

**Files:**
- Modify: `view_db.py`

- [ ] **Step 1: Add a `--ledger <user_id>` flag**

`app/db.get_ledger` (added in Task 2) already returns a user's ledger rows, so this reuses it via the facade rather than duplicating the query. Add the import at the top of `view_db.py` (after line 16, `from app import config`):

```python
from app import config, db
```

Then in `main()`, right after the `status_filter` parsing block (after `view_db.py:58`, before `products, orders = fetch(status_filter)`), add:

```python
    if "--ledger" in sys.argv:
        i = sys.argv.index("--ledger")
        user_id = int(sys.argv[i + 1])
        rows = [dict(r) for r in db.get_ledger(user_id)]
        _print_block(
            f"WALLET LEDGER [user {user_id}]", rows,
            ["id", "delta_micro", "reason", "ref", "balance_after_micro", "created_at"],
        )
        return
```

This makes `--ledger` a standalone mode (prints ledger and exits), matching how `--orders` filters within the normal dump but `--ledger` is a distinct, user-scoped query — simplest to keep separate rather than threading it through the products/orders block.

- [ ] **Step 2: Update the module docstring**

Add a line to the `Usage:` block at the top of `view_db.py` (after line 9):

```python
    python view_db.py --ledger 123456789  # dump one user's wallet transaction history
```

- [ ] **Step 3: Manual verification**

Run:

```python
python -c "
from app import config, db
config.DB_PATH = 'tmp_ledger_check.db'
db.init_db()
db.record_user(42, 'Test', 'test')
db.credit_wallet(42, 5_000_000, reason='deposit_bsc', ref='0xabc')
db.debit_wallet(42, 2_000_000, reason='purchase', ref='7')
import sys, view_db
sys.argv = ['view_db.py', '--ledger', '42']
view_db.main()
import os
os.remove('tmp_ledger_check.db')
"
```

Expected: prints a `WALLET LEDGER [user 42]  (2)` block with the deposit and purchase rows.

- [ ] **Step 4: Commit**

```bash
git add view_db.py
git commit -m "Add --ledger flag to view_db.py for wallet reconciliation"
```

---

### Task 6: Final full verification

- [ ] **Step 1: Run the full self-check suite**

Run: `python -m app.services.crypto_watch`

Expected: `crypto_watch self-check: all assertions passed`

- [ ] **Step 2: Sanity-check the bot still boots**

Run: `python -c "from app.main import build_application; print('build_application imports OK')"`

Expected: prints the OK message, no traceback (catches any import-time typo across the touched files).

- [ ] **Step 3: Confirm no leftover temp files**

Run: `git status`

Expected: only the intended source files show as modified; no `tmp_*.db` files present (they're created and removed within each verification step above, in the repo root — outside git tracking since `*.db` isn't part of the intended commit).
