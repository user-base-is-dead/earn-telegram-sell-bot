# Postgres Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the bot's datastore from a local SQLite file to a managed Supabase Postgres instance, with zero behavior change for bot users, so the DB survives the server dying and the future admin panel can read it independently.

**Architecture:** `app/db/` keeps its existing thin-hand-written-SQL shape (no ORM) — only the driver changes, from `sqlite3` to `asyncpg` (async connection pool). Every `app/db/*` function becomes `async def`; every call site across handlers/services gets `await`. A one-off script copies existing rows from SQLite into Postgres during cutover.

**Tech Stack:** `asyncpg` (async Postgres driver), Supabase-hosted Postgres, existing `python-telegram-bot` v21 asyncio runtime.

---

Reference spec: `docs/superpowers/specs/2026-07-15-admin-panel-design.md` (Phase 1 section).

### Task 1: Provision Supabase and configure `DATABASE_URL`

**Files:**
- Modify: `app/config.py:105-110`
- Modify: `.env.example`

- [ ] **Step 1: Provision the Supabase project (manual, one-time)**

Go to supabase.com, create a project, then from Project Settings → Database copy the
**connection string** (use the "Session pooler" URI, port 6543, so it works with asyncpg
under serverless-style connection churn — the direct port 5432 URI also works for this
always-on bot process, either is fine here since the bot holds a long-lived pool).
It looks like:

```
postgresql://postgres.xxxxxxxx:[PASSWORD]@aws-0-xxxx.pooler.supabase.com:6543/postgres
```

- [ ] **Step 2: Replace `DB_PATH` with `DATABASE_URL` in config**

In `app/config.py`, replace lines 105-110:

```python
# The database lives in a sibling folder NEXT TO the repo (outside it), so git never
# touches it. The path is computed from THIS file's location, so it resolves correctly
# no matter which directory the bot is launched from, on any OS. Override with DB_PATH
# in .env only if you want a custom location.
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "telegram-seller-bot-db-main" / "store.db"
DB_PATH = os.getenv("DB_PATH", str(_DEFAULT_DB)).strip()
```

with:

```python
# Managed Postgres (Supabase) connection string. Required — see .env.example.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
```

- [ ] **Step 3: Update `validate()` to require `DATABASE_URL`**

In `app/config.py`, find the `validate()` function (around line 148) and replace:

```python
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nCopy .env.example to .env and fill in the values."
        )
```

with (add `DATABASE_URL` to the `missing` check above it):

```python
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nCopy .env.example to .env and fill in the values."
        )
```

and delete the now-obsolete line below it:

```python
    # Ensure the database directory exists.
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Add `DATABASE_URL` to `.env.example`**

Add near the top of `.env.example` (replacing any `DB_PATH` entry there):

```
# Supabase/Postgres connection string (Project Settings -> Database -> Connection string).
DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@aws-0-xxxx.pooler.supabase.com:6543/postgres
```

- [ ] **Step 5: Commit**

```bash
git add app/config.py .env.example
git commit -m "Switch config from local SQLite path to Supabase DATABASE_URL"
```

---

### Task 2: Add `asyncpg` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

`requirements.txt` currently is:

```
python-telegram-bot[job-queue]==22.8
qrcode[pil]==7.4.2
python-dotenv==1.0.1
```

Add one line:

```
python-telegram-bot[job-queue]==22.8
qrcode[pil]==7.4.2
python-dotenv==1.0.1
asyncpg==0.30.0
```

- [ ] **Step 2: Install and verify**

Run: `pip install -r requirements.txt`
Expected: `asyncpg` installs cleanly (it ships prebuilt wheels for Windows/py3.12+, no C compiler needed).

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "Add asyncpg dependency for Postgres migration"
```

---

### Task 3: Rewrite `app/db/schema.py` for asyncpg

**Files:**
- Modify: `app/db/schema.py` (full rewrite)

- [ ] **Step 1: Replace the entire file**

```python
"""Connection pool helper, schema, and shared status constants (Postgres via asyncpg)."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import asyncpg

from app import config

# Order lifecycle
STATUS_CREATED = "created"            # order made, QR shown, not yet paid
STATUS_PENDING = "pending_review"     # user submitted payment, awaiting admin
STATUS_APPROVED = "approved"          # admin approved, product delivered
STATUS_REJECTED = "rejected"          # admin rejected the payment

UNLIMITED_STOCK = -1

# Deposit lifecycle (wallet top-ups)
DEPOSIT_PENDING = "pending"
DEPOSIT_CREDITED = "credited"

RAIL_BSC = "bsc"
RAIL_BINANCE_PAY = "binance_pay"

# Public order reference: date + random code (e.g. 260618-K7P2). Unambiguous
# alphabet (no 0/O/1/I/L) so it never collides with or resembles an old ID.
_REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_pool: Optional[asyncpg.Pool] = None

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    price           DOUBLE PRECISION NOT NULL,
    price_inr       DOUBLE PRECISION NOT NULL DEFAULT 0,
    content         TEXT NOT NULL DEFAULT '',
    stock           INTEGER NOT NULL DEFAULT -1,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    offer_price     DOUBLE PRECISION NOT NULL DEFAULT 0,
    offer_price_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    offer_until     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS orders (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    username      TEXT NOT NULL DEFAULT '',
    product_id    BIGINT NOT NULL REFERENCES products(id),
    product_name  TEXT NOT NULL,
    amount        DOUBLE PRECISION NOT NULL,
    amount_usdt   DOUBLE PRECISION NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    utr           TEXT NOT NULL DEFAULT '',
    method        TEXT NOT NULL DEFAULT '',
    ref           TEXT NOT NULL DEFAULT '',
    reason        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status   ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_user     ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_products_active ON products(active);

CREATE TABLE IF NOT EXISTS users (
    user_id              BIGINT PRIMARY KEY,
    first_name           TEXT NOT NULL DEFAULT '',
    username             TEXT NOT NULL DEFAULT '',
    started_at           TEXT NOT NULL,
    clicks               INTEGER NOT NULL DEFAULT 0,
    wallet_balance_micro BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS deposits (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL,
    rail                TEXT NOT NULL,
    tagged_amount_micro BIGINT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    credited_tx_ref     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_deposits_rail_status ON deposits(rail, status);

CREATE TABLE IF NOT EXISTS wallet_ledger (
    id                   BIGSERIAL PRIMARY KEY,
    user_id              BIGINT NOT NULL,
    delta_micro          BIGINT NOT NULL,
    reason               TEXT NOT NULL,
    ref                  TEXT NOT NULL DEFAULT '',
    balance_after_micro  BIGINT NOT NULL,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user ON wallet_ledger(user_id);

CREATE TABLE IF NOT EXISTS product_keys (
    id               BIGSERIAL PRIMARY KEY,
    product_id       BIGINT NOT NULL REFERENCES products(id),
    code             TEXT NOT NULL,
    used             INTEGER NOT NULL DEFAULT 0,
    used_by_order_id BIGINT
);
CREATE INDEX IF NOT EXISTS idx_product_keys_unused ON product_keys(product_id, used);

CREATE TABLE IF NOT EXISTS processed_tx (
    id          BIGSERIAL PRIMARY KEY,
    rail        TEXT NOT NULL,
    tx_ref      TEXT NOT NULL,
    credited_at TEXT NOT NULL,
    UNIQUE(rail, tx_ref)
);

CREATE TABLE IF NOT EXISTS chain_cursor (
    rail       TEXT PRIMARY KEY,
    last_block BIGINT NOT NULL
);
"""


async def init_pool() -> None:
    """Create the connection pool and the schema if it doesn't exist yet. Call once
    at startup, from inside a running event loop (e.g. python-telegram-bot's post_init)."""
    global _pool
    _pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


@asynccontextmanager
async def _connect() -> AsyncIterator[asyncpg.Connection]:
    async with _pool.acquire() as conn:
        async with conn.transaction():
            yield conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

- [ ] **Step 2: Commit**

```bash
git add app/db/schema.py
git commit -m "Rewrite app/db/schema.py for asyncpg/Postgres"
```

---

### Task 4: Rewrite `app/db/products.py` for asyncpg

**Files:**
- Modify: `app/db/products.py` (full rewrite)

- [ ] **Step 1: Replace the entire file**

```python
"""Product CRUD and the auto-delivery key pool."""
from typing import Optional

import asyncpg

from app.db.schema import UNLIMITED_STOCK, _connect, _now


async def add_product(
    name: str,
    description: str,
    price: float,
    content: str,
    stock: int = UNLIMITED_STOCK,
    price_inr: float = 0.0,
) -> int:
    async with _connect() as conn:
        return await conn.fetchval(
            """INSERT INTO products (name, description, price, price_inr, content, stock, active, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, 1, $7) RETURNING id""",
            name, description, price, price_inr, content, stock, _now(),
        )


async def get_product(product_id: int) -> Optional[asyncpg.Record]:
    async with _connect() as conn:
        return await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)


async def list_products(only_active: bool = True, include_out_of_stock: bool = False) -> list[asyncpg.Record]:
    """List products in display order.

    only_active=True  -> only active (non-deactivated) products.
    include_out_of_stock=True -> also include active products with 0 stock, so the
        catalog can still show them (marked sold-out) instead of hiding them.
    """
    async with _connect() as conn:
        if only_active:
            if include_out_of_stock:
                return await conn.fetch(
                    "SELECT * FROM products WHERE active = 1 ORDER BY id"
                )
            return await conn.fetch(
                """SELECT * FROM products
                   WHERE active = 1 AND (stock = $1 OR stock > 0)
                   ORDER BY id""",
                UNLIMITED_STOCK,
            )
        return await conn.fetch("SELECT * FROM products ORDER BY id")


async def set_product_active(product_id: int, active: bool) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE products SET active = $1 WHERE id = $2",
            1 if active else 0, product_id,
        )


# Only these product fields may be edited from chat (whitelist guards the SQL).
EDITABLE_FIELDS = {
    "name", "description", "price", "price_inr", "stock",
}


async def update_product(product_id: int, field: str, value) -> None:
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"Field not editable: {field!r}")
    async with _connect() as conn:
        await conn.execute(
            f"UPDATE products SET {field} = $1 WHERE id = $2", value, product_id
        )


async def delete_product(product_id: int) -> None:
    async with _connect() as conn:
        await conn.execute("DELETE FROM products WHERE id = $1", product_id)


async def decrement_stock(product_id: int) -> None:
    """Reduce stock by one if the product is not unlimited."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            "SELECT stock FROM products WHERE id = $1", product_id
        )
        if row and row["stock"] != UNLIMITED_STOCK and row["stock"] > 0:
            await conn.execute(
                "UPDATE products SET stock = stock - 1 WHERE id = $1", product_id
            )


# --------------------------------------------------------------------------- #
# Product key pool (auto-delivered digital codes)
# --------------------------------------------------------------------------- #
async def add_product_keys(product_id: int, codes: list[str]) -> int:
    """Add a batch of unused codes to a product's delivery pool. Returns count added."""
    async with _connect() as conn:
        await conn.executemany(
            "INSERT INTO product_keys (product_id, code) VALUES ($1, $2)",
            [(product_id, c) for c in codes],
        )
        return len(codes)


async def count_unused_keys(product_id: int) -> int:
    async with _connect() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM product_keys WHERE product_id = $1 AND used = 0",
            product_id,
        )


async def pop_unused_key(product_id: int, order_id: int) -> Optional[str]:
    """Atomically claim and return one unused code for this product, or None if empty.

    FOR UPDATE SKIP LOCKED is required here (and wasn't under SQLite): Postgres allows
    real concurrent transactions, so without a row lock two simultaneous buyers could
    both read the same unused key before either UPDATE commits.
    """
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT id, code FROM product_keys
               WHERE product_id = $1 AND used = 0
               ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""",
            product_id,
        )
        if not row:
            return None
        await conn.execute(
            "UPDATE product_keys SET used = 1, used_by_order_id = $1 WHERE id = $2",
            order_id, row["id"],
        )
        return row["code"]
```

- [ ] **Step 2: Commit**

```bash
git add app/db/products.py
git commit -m "Rewrite app/db/products.py for asyncpg/Postgres"
```

---

### Task 5: Rewrite `app/db/orders.py` for asyncpg

**Files:**
- Modify: `app/db/orders.py` (full rewrite)

- [ ] **Step 1: Replace the entire file**

```python
"""Order lifecycle, listing/cleanup, and earnings."""
import secrets
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from app.db.schema import STATUS_APPROVED, STATUS_CREATED, STATUS_PENDING, STATUS_REJECTED, _REF_ALPHABET, _connect, _now


async def _gen_ref(conn: asyncpg.Connection) -> str:
    """A unique, non-repeating public order reference like '260618-K7P2'."""
    date = datetime.now(timezone.utc).strftime("%y%m%d")
    while True:
        suffix = "".join(secrets.choice(_REF_ALPHABET) for _ in range(4))
        ref = f"{date}-{suffix}"
        exists = await conn.fetchval("SELECT 1 FROM orders WHERE ref = $1", ref)
        if not exists:
            return ref


async def create_order(
    user_id: int, username: str, product_id: int, product_name: str, amount: float,
    amount_usdt: float = 0.0,
) -> int:
    now = _now()
    async with _connect() as conn:
        ref = await _gen_ref(conn)
        return await conn.fetchval(
            """INSERT INTO orders
               (user_id, username, product_id, product_name, amount, amount_usdt, status, ref, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id""",
            user_id, username, product_id, product_name, amount, amount_usdt,
            STATUS_CREATED, ref, now, now,
        )


async def get_order(order_id: int) -> Optional[asyncpg.Record]:
    async with _connect() as conn:
        return await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)


async def set_order_status(order_id: int, status: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE orders SET status = $1, updated_at = $2 WHERE id = $3",
            status, _now(), order_id,
        )


async def set_order_method(order_id: int, method: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE orders SET method = $1, updated_at = $2 WHERE id = $3",
            method, _now(), order_id,
        )


async def reject_order(order_id: int, reason: str) -> None:
    """Mark an order rejected and record the reason shown to the buyer."""
    async with _connect() as conn:
        await conn.execute(
            "UPDATE orders SET status = $1, reason = $2, updated_at = $3 WHERE id = $4",
            STATUS_REJECTED, reason, _now(), order_id,
        )


async def set_order_utr(order_id: int, utr: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE orders SET utr = $1, status = $2, updated_at = $3 WHERE id = $4",
            utr, STATUS_PENDING, _now(), order_id,
        )


async def list_orders(
    status: Optional[str] = None,
    limit: int = 20,
    include_created: bool = True,
    user_id: Optional[int] = None,
) -> list[asyncpg.Record]:
    """List orders, newest first. Filters combine:
    - status: exact status, OR
    - include_created=False: everything except abandoned ('created') orders
    - user_id: only this buyer's orders
    """
    clauses: list[str] = []
    params: list = []
    if status:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    elif not include_created:
        params.append(STATUS_CREATED)
        clauses.append(f"status != ${len(params)}")
    if user_id is not None:
        params.append(user_id)
        clauses.append(f"user_id = ${len(params)}")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    async with _connect() as conn:
        return await conn.fetch(
            f"SELECT * FROM orders{where} ORDER BY id DESC LIMIT ${len(params)}", *params
        )


async def count_user_orders(user_id: int, status: Optional[str] = None) -> int:
    """How many orders this buyer placed (optionally filtered to one status) —
    used on their profile screen."""
    async with _connect() as conn:
        if status:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM orders WHERE user_id = $1 AND status = $2",
                user_id, status,
            )
        return await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE user_id = $1 AND status != $2",
            user_id, STATUS_CREATED,
        )


async def list_abandoned_orders(limit: int = 50) -> list[asyncpg.Record]:
    """Orders that were created but never paid (buyer left without paying)."""
    async with _connect() as conn:
        return await conn.fetch(
            "SELECT * FROM orders WHERE status = $1 ORDER BY id DESC LIMIT $2",
            STATUS_CREATED, limit,
        )


async def clear_abandoned_orders() -> int:
    """Delete only abandoned (unpaid/created) orders. Returns count deleted."""
    async with _connect() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE status = $1", STATUS_CREATED
        )
        await conn.execute("DELETE FROM orders WHERE status = $1", STATUS_CREATED)
        return n


async def clear_successful_orders() -> int:
    """Delete only non-abandoned orders (pending/approved/rejected). Returns count."""
    async with _connect() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE status != $1", STATUS_CREATED
        )
        await conn.execute("DELETE FROM orders WHERE status != $1", STATUS_CREATED)
        # Reset the id sequence only if no orders remain at all.
        remaining = await conn.fetchval("SELECT COUNT(*) FROM orders")
        if remaining == 0:
            await conn.execute("ALTER SEQUENCE orders_id_seq RESTART WITH 1")
        return n


async def clear_data(wipe_products: bool = False) -> dict:
    """Delete all orders (and optionally all products), resetting id numbering so
    the next row starts at #1.

    No backup is made — the caller is responsible for confirming first. Orders are
    always deleted before products, so the products foreign key is never violated.
    Returns how many rows were removed: ``{"orders": n, "products": m}``.
    """
    async with _connect() as conn:
        orders = await conn.fetchval("SELECT COUNT(*) FROM orders")
        await conn.execute("DELETE FROM orders")
        await conn.execute("ALTER SEQUENCE orders_id_seq RESTART WITH 1")
        products = 0
        if wipe_products:
            products = await conn.fetchval("SELECT COUNT(*) FROM products")
            await conn.execute("DELETE FROM products")
            await conn.execute("ALTER SEQUENCE products_id_seq RESTART WITH 1")
        return {"orders": orders, "products": products}


async def earnings_summary() -> dict:
    """Totals across all approved (delivered) orders."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount), 0) AS inr, COALESCE(SUM(amount_usdt), 0) AS usdt,
                      COUNT(*) AS n
               FROM orders WHERE status = $1""",
            STATUS_APPROVED,
        )
        return {"inr": row["inr"], "usdt": row["usdt"], "count": row["n"]}
```

- [ ] **Step 2: Commit**

```bash
git add app/db/orders.py
git commit -m "Rewrite app/db/orders.py for asyncpg/Postgres"
```

---

### Task 6: Rewrite `app/db/users.py` for asyncpg

**Files:**
- Modify: `app/db/users.py` (full rewrite)

- [ ] **Step 1: Replace the entire file**

```python
"""Broadcast audience tracking."""
import asyncpg

from app.db.schema import _connect, _now


async def record_user(user_id: int, first_name: str = "", username: str = "") -> None:
    """Remember a user the bot can later message. Idempotent (called on every update)."""
    async with _connect() as conn:
        await conn.execute(
            """INSERT INTO users (user_id, first_name, username, started_at, clicks)
               VALUES ($1, $2, $3, $4, 1)
               ON CONFLICT (user_id) DO UPDATE SET
                   first_name = EXCLUDED.first_name,
                   username   = EXCLUDED.username,
                   clicks     = users.clicks + 1""",
            user_id, first_name or "", username or "", _now(),
        )


async def get_user(user_id: int):
    async with _connect() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


async def all_users() -> list[asyncpg.Record]:
    """All tracked users, newest first."""
    async with _connect() as conn:
        return await conn.fetch("SELECT * FROM users ORDER BY started_at DESC")


async def all_recipient_ids() -> list[int]:
    """Every user to broadcast to: those who used the bot, plus anyone who ever
    placed an order (covers customers from before user-tracking existed)."""
    async with _connect() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM users UNION SELECT DISTINCT user_id FROM orders"
        )
        return [r["user_id"] for r in rows]
```

- [ ] **Step 2: Commit**

```bash
git add app/db/users.py
git commit -m "Rewrite app/db/users.py for asyncpg/Postgres"
```

---

### Task 7: Rewrite `app/db/wallet.py` for asyncpg

**Files:**
- Modify: `app/db/wallet.py` (full rewrite)

- [ ] **Step 1: Replace the entire file**

```python
"""Wallet balance, pending deposits, and idempotency/cursor bookkeeping for the
auto-confirmed crypto/Binance Pay top-up watchers (app.services.crypto_watch)."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

from app.db.schema import DEPOSIT_CREDITED, DEPOSIT_PENDING, _connect, _now


async def get_wallet_balance(user_id: int) -> int:
    """Balance in integer micro-USDT (amount * 1_000_000)."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            "SELECT wallet_balance_micro FROM users WHERE user_id = $1", user_id
        )
        return row["wallet_balance_micro"] if row else 0


async def _log_ledger(conn: asyncpg.Connection, user_id: int, delta_micro: int, reason: str, ref: str) -> None:
    balance_after = await conn.fetchval(
        "SELECT wallet_balance_micro FROM users WHERE user_id = $1", user_id
    )
    await conn.execute(
        """INSERT INTO wallet_ledger (user_id, delta_micro, reason, ref, balance_after_micro, created_at)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        user_id, delta_micro, reason, ref, balance_after, _now(),
    )


async def credit_wallet(user_id: int, amount_micro: int, reason: str, ref: str = "") -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro + $1 WHERE user_id = $2",
            amount_micro, user_id,
        )
        await _log_ledger(conn, user_id, amount_micro, reason, ref)


async def debit_wallet(user_id: int, amount_micro: int, reason: str, ref: str = "") -> bool:
    """Atomically deduct if the balance covers it. Returns False (no-op) if insufficient."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            "SELECT wallet_balance_micro FROM users WHERE user_id = $1", user_id
        )
        if not row or row["wallet_balance_micro"] < amount_micro:
            return False
        await conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro - $1 WHERE user_id = $2",
            amount_micro, user_id,
        )
        await _log_ledger(conn, user_id, -amount_micro, reason, ref)
        return True


async def get_ledger(user_id: int) -> list[asyncpg.Record]:
    """A user's full transaction history, oldest first."""
    async with _connect() as conn:
        return await conn.fetch(
            "SELECT * FROM wallet_ledger WHERE user_id = $1 ORDER BY id", user_id
        )


async def create_deposit(user_id: int, rail: str, tagged_amount_micro: int, expires_minutes: int) -> int:
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=expires_minutes)).isoformat(timespec="seconds")
    async with _connect() as conn:
        return await conn.fetchval(
            """INSERT INTO deposits (user_id, rail, tagged_amount_micro, status, created_at, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            user_id, rail, tagged_amount_micro, DEPOSIT_PENDING, _now(), expires_at,
        )


async def pending_tagged_amounts(rail: str) -> set[int]:
    """Tagged amounts still awaiting payment on this rail, so a new deposit can pick
    one that doesn't collide (expired ones are excluded, their amount is reusable)."""
    async with _connect() as conn:
        rows = await conn.fetch(
            """SELECT tagged_amount_micro FROM deposits
               WHERE rail = $1 AND status = $2 AND expires_at > $3""",
            rail, DEPOSIT_PENDING, _now(),
        )
        return {r["tagged_amount_micro"] for r in rows}


async def find_matching_deposit(rail: str, amount_micro: int) -> Optional[asyncpg.Record]:
    """The pending, unexpired deposit on this rail tagged for this exact amount."""
    async with _connect() as conn:
        return await conn.fetchrow(
            """SELECT * FROM deposits
               WHERE rail = $1 AND tagged_amount_micro = $2 AND status = $3 AND expires_at > $4
               ORDER BY id LIMIT 1""",
            rail, amount_micro, DEPOSIT_PENDING, _now(),
        )


async def mark_deposit_credited(deposit_id: int, tx_ref: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE deposits SET status = $1, credited_tx_ref = $2 WHERE id = $3",
            DEPOSIT_CREDITED, tx_ref, deposit_id,
        )


async def is_tx_processed(rail: str, tx_ref: str) -> bool:
    async with _connect() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM processed_tx WHERE rail = $1 AND tx_ref = $2)",
            rail, tx_ref,
        )


async def mark_tx_processed(rail: str, tx_ref: str) -> None:
    """Idempotent: a tx already marked processed is silently ignored (UNIQUE constraint)."""
    async with _connect() as conn:
        try:
            await conn.execute(
                "INSERT INTO processed_tx (rail, tx_ref, credited_at) VALUES ($1, $2, $3)",
                rail, tx_ref, _now(),
            )
        except asyncpg.UniqueViolationError:
            pass


async def get_cursor(rail: str) -> Optional[int]:
    async with _connect() as conn:
        row = await conn.fetchrow(
            "SELECT last_block FROM chain_cursor WHERE rail = $1", rail
        )
        return row["last_block"] if row else None


async def set_cursor(rail: str, last_block: int) -> None:
    async with _connect() as conn:
        await conn.execute(
            """INSERT INTO chain_cursor (rail, last_block) VALUES ($1, $2)
               ON CONFLICT (rail) DO UPDATE SET last_block = EXCLUDED.last_block""",
            rail, last_block,
        )
```

- [ ] **Step 2: Commit**

```bash
git add app/db/wallet.py
git commit -m "Rewrite app/db/wallet.py for asyncpg/Postgres"
```

---

### Task 8: Wire pool startup/shutdown into `app/main.py`

**Files:**
- Modify: `app/main.py:91-113` (`_post_init`)
- Modify: `app/main.py:115-130` (`build_application`)
- Modify: `app/main.py:445-447` (`main`)

- [ ] **Step 1: Initialize the pool inside `_post_init` (needs a running event loop)**

In `app/main.py`, at the top of `_post_init` (line 91), add the pool init as the first line:

```python
async def _post_init(app: Application) -> None:
    """Register the slash-command hints shown in Telegram's '/' menu.
    ...
    """
    await db.init_pool()

    # Default scope: what every user sees.
    await app.bot.set_my_commands(menu.CUSTOMER_CMDS)
    ...
```

- [ ] **Step 2: Add a `post_shutdown` hook that closes the pool**

Add a new function right after `_post_init`:

```python
async def _post_shutdown(app: Application) -> None:
    await db.close_pool()
```

Then in `build_application()` (line 115), add `.post_shutdown(_post_shutdown)` next to the
existing `.post_init(_post_init)` call (line 121):

```python
    builder = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .connect_timeout(config.CONNECT_TIMEOUT)
        ...
```

- [ ] **Step 3: Remove the old synchronous `db.init_db()` call from `main()`**

In `main()` (around line 445-447), delete this line entirely — schema creation now happens
inside `_post_init`, which needs the event loop `run_polling()` sets up:

```python
def main() -> None:
    config.validate()
    db.init_db()          # <-- delete this line
    app = build_application()
```

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "Move Postgres pool init/close into PTB post_init/post_shutdown hooks"
```

---

### Task 9: Convert every `app.db` call site to `await`

**Files:**
- Modify: every file that calls `db.<function>(...)` or `from app import db` — locate via the
  grep below. Based on the current codebase this includes all of `app/handlers/*.py`,
  `app/middleware/membership.py`, `app/services/crypto_watch.py`, `app/services/qr.py` (if it
  touches db), `view_db.py`, `reset_db.py`.

All handler callbacks in this codebase are already `async def` (python-telegram-bot v21
requires this), so this conversion is mechanical: every synchronous `db.func(...)` call becomes
`await db.func(...)`. No control flow changes.

- [ ] **Step 1: Find every call site**

Run: `grep -rn "db\.[a-z_]*(" app/handlers app/middleware app/services view_db.py reset_db.py`

This lists every line calling into the `app.db` facade. Work through them file by file.

- [ ] **Step 2: Convert each call site — worked example**

Before (e.g. in `app/handlers/catalog.py`, a callback handler):

```python
async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    products = db.list_products()
    ...
```

After:

```python
async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    products = await db.list_products()
    ...
```

Apply this same transformation — add `await` in front of every `db.<name>(...)` call found in
Step 1 — across all listed files. `view_db.py` and `reset_db.py` are standalone scripts (not
async), so their `main()`/entry logic needs wrapping in `asyncio.run(...)`; see Task 10 for the
same pattern applied to the migration script, and apply it identically there.

- [ ] **Step 3: Verify nothing was missed**

Run: `grep -rn "[^t ]db\.[a-z_]*(" app/handlers app/middleware app/services view_db.py reset_db.py | grep -v "await db\."`
Expected: no output (every remaining `db.` call is preceded by `await`).

- [ ] **Step 4: Commit**

```bash
git add app/handlers app/middleware app/services view_db.py reset_db.py
git commit -m "Await every app.db call site after the asyncpg migration"
```

---

### Task 10: One-off data migration script

**Files:**
- Create: `migrate_to_postgres.py`

- [ ] **Step 1: Write the script**

```python
"""One-off: copy every row from the old SQLite store into the new Postgres DB.

Run once during cutover:
    python migrate_to_postgres.py path/to/old/store.db

Requires DATABASE_URL to already point at the target Postgres instance (.env),
and that the bot has been started at least once against it (or app.db.schema.init_pool
has otherwise created the schema) so the tables already exist.
"""
import asyncio
import sqlite3
import sys

import asyncpg

from app import config


async def _copy_table(sconn: sqlite3.Connection, pconn: asyncpg.Connection, table: str, columns: list[str]) -> int:
    rows = sconn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
    await pconn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [tuple(row) for row in rows],
    )
    return len(rows)


async def main(sqlite_path: str) -> None:
    sconn = sqlite3.connect(sqlite_path)
    sconn.row_factory = sqlite3.Row
    pconn = await asyncpg.connect(config.DATABASE_URL)

    # FK-safe order: products before orders/product_keys.
    plan = [
        ("products", ["id", "name", "description", "price", "price_inr", "content", "stock",
                       "active", "created_at", "offer_price", "offer_price_inr", "offer_until"]),
        ("users", ["user_id", "first_name", "username", "started_at", "clicks", "wallet_balance_micro"]),
        ("orders", ["id", "user_id", "username", "product_id", "product_name", "amount",
                     "amount_usdt", "status", "utr", "method", "ref", "reason",
                     "created_at", "updated_at"]),
        ("product_keys", ["id", "product_id", "code", "used", "used_by_order_id"]),
        ("deposits", ["id", "user_id", "rail", "tagged_amount_micro", "status",
                       "created_at", "expires_at", "credited_tx_ref"]),
        ("wallet_ledger", ["id", "user_id", "delta_micro", "reason", "ref",
                            "balance_after_micro", "created_at"]),
        ("processed_tx", ["id", "rail", "tx_ref", "credited_at"]),
        ("chain_cursor", ["rail", "last_block"]),
    ]

    for table, columns in plan:
        n = await _copy_table(sconn, pconn, table, columns)
        print(f"{table}: copied {n} rows")

    # Realign Postgres BIGSERIAL sequences past the max copied id, so new inserts
    # don't collide with the migrated rows.
    for table in ("products", "orders", "product_keys", "deposits", "wallet_ledger", "processed_tx"):
        await pconn.execute(
            f"SELECT setval('{table}_id_seq', COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )

    await pconn.close()
    sconn.close()
    print("Migration complete.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python migrate_to_postgres.py path/to/old/store.db")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 2: Commit**

```bash
git add migrate_to_postgres.py
git commit -m "Add one-off SQLite-to-Postgres data migration script"
```

---

### Task 11: Self-check script (ponytail check for this migration)

**Files:**
- Create: `verify_postgres_migration.py`

Non-trivial logic touching money (`wallet.py`'s credit/debit + ledger invariant) and concurrency
(`products.py`'s `FOR UPDATE SKIP LOCKED`) needs one runnable check, per this repo's existing
convention (`python -m app.services.crypto_watch` is the same kind of self-check).

- [ ] **Step 1: Write the self-check**

```python
"""Self-check for the Postgres migration: exercises create/read/update across every
table plus the wallet-ledger invariant, against DATABASE_URL. Uses throwaway rows
only (a fake user_id / product), does not touch real data.

Run: python verify_postgres_migration.py
"""
import asyncio

from app import config, db
from app.db.schema import close_pool, init_pool

_FAKE_USER = -999999001  # negative, unreachable by any real Telegram user_id


async def main() -> None:
    config.validate()
    await init_pool()

    # Products + key pool + concurrent-safe pop.
    product_id = await db.add_product("Selfcheck product", "desc", 1.0, "secret-content", stock=2)
    added = await db.add_product_keys(product_id, ["KEY-A", "KEY-B"])
    assert added == 2
    assert await db.count_unused_keys(product_id) == 2

    # Orders.
    order_id = await db.create_order(_FAKE_USER, "selfcheck", product_id, "Selfcheck product", 1.0)
    key = await db.pop_unused_key(product_id, order_id)
    assert key in ("KEY-A", "KEY-B")
    assert await db.count_unused_keys(product_id) == 1
    await db.set_order_status(order_id, db.STATUS_APPROVED)
    order = await db.get_order(order_id)
    assert order["status"] == db.STATUS_APPROVED

    # Wallet + ledger invariant: SUM(delta_micro) must equal current balance.
    await db.credit_wallet(_FAKE_USER, 5_000_000, "selfcheck_credit")
    ok = await db.debit_wallet(_FAKE_USER, 2_000_000, "selfcheck_debit")
    assert ok is True
    balance = await db.get_wallet_balance(_FAKE_USER)
    assert balance == 3_000_000
    ledger = await db.get_ledger(_FAKE_USER)
    assert sum(r["delta_micro"] for r in ledger) == balance

    # Cleanup the throwaway rows.
    await db.clear_data(wipe_products=False)  # clears the selfcheck order
    async with db.schema._connect() as conn:  # noqa: SLF001 (self-check only)
        await conn.execute("DELETE FROM product_keys WHERE product_id = $1", product_id)
        await conn.execute("DELETE FROM products WHERE id = $1", product_id)
        await conn.execute(
            "DELETE FROM wallet_ledger WHERE user_id = $1", _FAKE_USER
        )
        await conn.execute("DELETE FROM users WHERE user_id = $1", _FAKE_USER)

    await close_pool()
    print("Postgres migration self-check passed.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it**

Run: `python verify_postgres_migration.py`
Expected: `Postgres migration self-check passed.` with no assertion errors.

- [ ] **Step 3: Commit**

```bash
git add verify_postgres_migration.py
git commit -m "Add self-check script for the Postgres migration"
```

---

### Task 12: Cutover

**Files:** none (operational steps)

- [ ] **Step 1:** Set `DATABASE_URL` in the production `.env`.
- [ ] **Step 2:** Start the bot once against the new DB (creates schema via `_post_init`), then stop it: confirms `init_pool()` succeeds and the schema script runs cleanly.
- [ ] **Step 3:** Run `python migrate_to_postgres.py path/to/old/store.db` against the production SQLite file.
- [ ] **Step 4:** Run `python verify_postgres_migration.py` — confirms the DB is reachable and read/write/ledger logic works before real traffic hits it.
- [ ] **Step 5:** Run `python -m app.services.crypto_watch` (existing self-check) to confirm the wallet-watcher matching logic still passes against Postgres.
- [ ] **Step 6:** Start the bot for real. Manually smoke-test: browse catalog, buy one item with wallet balance (auto-delivers a code), submit a UTR for a manual-payment order, approve it from Telegram as admin, run `python view_db.py --ledger` and confirm ledger sums still match cached balances.
- [ ] **Step 7:** Keep the old SQLite file as a cold backup; do not delete it yet.
