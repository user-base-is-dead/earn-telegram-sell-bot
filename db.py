"""SQLite storage: products, stock (one row per login), orders ("invoices") and every incoming
USDT transfer the payment checker has processed.

All access goes through one connection guarded by one asyncio lock, and every write runs inside a
`BEGIN IMMEDIATE` transaction. So a payment is settled (transfer recorded, order marked paid,
logins marked sold) all at once or not at all, and the same transfer can never pay twice.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

Row = aiosqlite.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL
);

-- One row per login: available until an order is paid, then sold to that order.
CREATE TABLE IF NOT EXISTS stock (
    id         INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    content    TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'available',
    invoice_id INTEGER REFERENCES invoices(id),
    created_at INTEGER NOT NULL,
    sold_at    INTEGER,
    UNIQUE (product_id, content)
);
CREATE INDEX IF NOT EXISTS ix_stock_product ON stock (product_id, status);
CREATE INDEX IF NOT EXISTS ix_stock_invoice ON stock (invoice_id);

-- status: pending (open) | expired | cancelled | paid | backorder (paid, but no stock was left)
CREATE TABLE IF NOT EXISTS invoices (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    buyer        TEXT    NOT NULL,
    product_id   INTEGER NOT NULL REFERENCES products(id),
    qty          INTEGER NOT NULL,
    amount_units INTEGER NOT NULL,  -- USDT x 10^4 the buyer must send: 52043 = 5.2043 USDT
    status       TEXT    NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    message_id   INTEGER,           -- the payment message, edited once the order ends
    paid_at      INTEGER,
    tx_hash      TEXT,
    paid_raw     TEXT               -- what actually arrived, in token base units (18 decimals)
);
CREATE INDEX IF NOT EXISTS ix_invoices_user ON invoices (user_id, status);
CREATE INDEX IF NOT EXISTS ix_invoices_created ON invoices (created_at);

-- Every incoming transfer ever processed. The primary key is what makes a transfer usable
-- exactly once, however many times the chain is re-read.
CREATE TABLE IF NOT EXISTS transfers (
    tx_hash      TEXT    NOT NULL,
    log_index    INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    block_time   INTEGER NOT NULL,
    from_address TEXT    NOT NULL,
    value_raw    TEXT    NOT NULL,
    invoice_id   INTEGER REFERENCES invoices(id),
    note         TEXT    NOT NULL,  -- matched | unmatched | ambiguous | dust
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Everyone who has used the bot, for the admin's 👥 Users list.
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,  -- Telegram user id
    username    TEXT,
    name        TEXT    NOT NULL,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    clicks      INTEGER NOT NULL DEFAULT 0,
    last_action TEXT
);
CREATE INDEX IF NOT EXISTS ix_users_last_seen ON users (last_seen);
"""

INVOICE_SQL = "SELECT i.*, p.name AS product_name FROM invoices i JOIN products p ON p.id = i.product_id"
PRODUCT_SQL = """
    SELECT p.*,
           (SELECT COUNT(*) FROM stock s WHERE s.product_id = p.id AND s.status = 'available') AS in_stock,
           (SELECT COUNT(*) FROM stock s WHERE s.product_id = p.id AND s.status = 'sold') AS sold
    FROM products p
"""


@dataclass
class Transfer:
    """One incoming USDT transfer, as read from the chain."""

    tx_hash: str
    log_index: int
    block_number: int
    from_address: str
    value_raw: int  # token base units; USDT on BSC has 18 decimals
    block_time: int = 0  # unix seconds


@dataclass
class Settlement:
    """What processing one transfer did."""

    note: str  # matched | unmatched | ambiguous | dust | seen
    invoice: Row | None = None  # the order it paid, now 'paid' or 'backorder'
    items: list[str] = field(default_factory=list)  # the logins handed over
    candidate_ids: list[int] = field(default_factory=list)  # orders an ambiguous amount fitted
    sold_out: bool = False  # the product has no stock left afterwards


Matcher = Callable[[int, list[Row]], list[Row]]
AmountPicker = Callable[[int, set[int]], int | None]


def _now() -> int:
    return int(time.time())


async def _all(conn: aiosqlite.Connection, sql: str, params: Sequence = ()) -> list[Row]:
    async with conn.execute(sql, params) as cur:
        return list(await cur.fetchall())


async def _one(conn: aiosqlite.Connection, sql: str, params: Sequence = ()) -> Row | None:
    async with conn.execute(sql, params) as cur:
        return await cur.fetchone()


async def _take_stock(conn: aiosqlite.Connection, invoice: Row, now: int) -> list[str] | None:
    """Mark `qty` free logins of the order's product sold to it. None (and nothing changes) if
    there aren't enough: stock is only ever taken by a payment, never by opening an order."""
    rows = await _all(
        conn,
        "SELECT id, content FROM stock WHERE product_id = ? AND status = 'available' ORDER BY id LIMIT ?",
        (invoice["product_id"], invoice["qty"]),
    )
    if len(rows) < invoice["qty"]:
        return None
    await conn.executemany(
        "UPDATE stock SET status = 'sold', invoice_id = ?, sold_at = ? WHERE id = ?",
        [(invoice["id"], now, r["id"]) for r in rows],
    )
    return [r["content"] for r in rows]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: no implicit transactions; tx() opens them explicitly.
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        for pragma in ("journal_mode=WAL", "foreign_keys=ON", "busy_timeout=5000", "synchronous=NORMAL"):
            await self._conn.execute(f"PRAGMA {pragma}")
        await self._conn.executescript(SCHEMA)
        # Earlier versions held stock for open orders. Stock is only taken by a payment now, so
        # anything still held from before goes straight back on sale.
        await self._conn.execute("UPDATE stock SET status = 'available', invoice_id = NULL WHERE status = 'reserved'")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[aiosqlite.Connection]:
        """An exclusive write transaction: committed on success, rolled back on any error."""
        async with self._lock:
            conn = self._conn
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                await conn.execute("ROLLBACK")
                raise
            await conn.execute("COMMIT")

    async def _read_all(self, sql: str, params: Sequence = ()) -> list[Row]:
        async with self._lock:
            return await _all(self._conn, sql, params)

    async def _read_one(self, sql: str, params: Sequence = ()) -> Row | None:
        async with self._lock:
            return await _one(self._conn, sql, params)

    # ---- products & stock ---------------------------------------------------------------------

    async def products(self) -> list[Row]:
        return await self._read_all(PRODUCT_SQL + " WHERE p.active = 1 ORDER BY p.id")

    async def product(self, product_id: int) -> Row | None:
        return await self._read_one(PRODUCT_SQL + " WHERE p.id = ? AND p.active = 1", (product_id,))

    async def add_product(self, name: str, price_cents: int) -> int:
        async with self.tx() as conn:
            cur = await conn.execute(
                "INSERT INTO products (name, price_cents, created_at) VALUES (?, ?, ?)", (name, price_cents, _now())
            )
            return cur.lastrowid

    async def set_price(self, product_id: int, price_cents: int) -> bool:
        """Changes the price for new orders; open orders keep the amount they were given."""
        async with self.tx() as conn:
            cur = await conn.execute(
                "UPDATE products SET price_cents = ? WHERE id = ? AND active = 1", (price_cents, product_id)
            )
            return cur.rowcount == 1

    async def delete_product(self, product_id: int) -> int | None:
        """Hide the product and drop its unsold logins (sold ones stay for order history).
        Returns how many logins were dropped, or None if there is no such product."""
        async with self.tx() as conn:
            cur = await conn.execute("UPDATE products SET active = 0 WHERE id = ? AND active = 1", (product_id,))
            if cur.rowcount != 1:
                return None
            cur = await conn.execute("DELETE FROM stock WHERE product_id = ? AND status = 'available'", (product_id,))
            return cur.rowcount

    async def add_stock(self, product_id: int, items: Sequence[str]) -> tuple[int, int] | None:
        """Add logins. Anything this product already has (or ever sold) is skipped, so the same
        login can never be sold twice. Returns (added, skipped), or None if no such product."""
        async with self.tx() as conn:
            if await _one(conn, "SELECT 1 FROM products WHERE id = ? AND active = 1", (product_id,)) is None:
                return None
            added, now = 0, _now()
            for item in items:
                cur = await conn.execute(
                    "INSERT OR IGNORE INTO stock (product_id, content, created_at) VALUES (?, ?, ?)",
                    (product_id, item, now),
                )
                added += cur.rowcount
            return added, len(items) - added

    async def clear_stock(self, product_id: int) -> int:
        async with self.tx() as conn:
            cur = await conn.execute("DELETE FROM stock WHERE product_id = ? AND status = 'available'", (product_id,))
            return cur.rowcount

    # ---- orders -------------------------------------------------------------------------------

    async def create_invoice(
        self,
        *,
        user_id: int,
        buyer: str,
        product_id: int,
        qty: int,
        fee_cents: int,
        pay_seconds: int,
        reserve_seconds: int,
        max_per_hour: int,
        pick_amount: AmountPicker,
    ) -> tuple[str, Row | None]:
        """Open an order with an amount that no other payable order has. Its logins stay on sale:
        they are only taken when the payment arrives, by whoever pays first.

        Returns (outcome, order). Outcomes: 'created'; 'open' (the buyer already has an open order,
        which is returned); 'rate' (too many orders this hour); 'gone' (no such product);
        'stock' (not enough stock right now); 'busy' (every amount for this price is taken).
        """
        now = _now()
        async with self.tx() as conn:
            existing = await _one(conn, INVOICE_SQL + " WHERE i.user_id = ? AND i.status = 'pending'", (user_id,))
            if existing is not None:
                return "open", existing
            recent = await _one(
                conn, "SELECT COUNT(*) FROM invoices WHERE user_id = ? AND created_at >= ?", (user_id, now - 3600)
            )
            if recent[0] >= max_per_hour:
                return "rate", None
            product = await _one(conn, "SELECT price_cents FROM products WHERE id = ? AND active = 1", (product_id,))
            if product is None:
                return "gone", None
            available = await _one(
                conn, "SELECT COUNT(*) FROM stock WHERE product_id = ? AND status = 'available'", (product_id,)
            )
            if available[0] < qty:
                return "stock", None
            recent_rows = await _all(conn, "SELECT amount_units FROM invoices WHERE created_at >= ?", (now - reserve_seconds,))
            taken = {r[0] for r in recent_rows}
            amount = pick_amount((product["price_cents"] * qty + fee_cents) * 100, taken)
            if amount is None:
                return "busy", None
            cur = await conn.execute(
                "INSERT INTO invoices (user_id, buyer, product_id, qty, amount_units, status, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (user_id, buyer, product_id, qty, amount, now, now + pay_seconds),
            )
            return "created", await _one(conn, INVOICE_SQL + " WHERE i.id = ?", (cur.lastrowid,))

    async def invoice(self, invoice_id: int) -> Row | None:
        return await self._read_one(INVOICE_SQL + " WHERE i.id = ?", (invoice_id,))

    async def set_invoice_message(self, invoice_id: int, message_id: int) -> None:
        async with self.tx() as conn:
            await conn.execute("UPDATE invoices SET message_id = ? WHERE id = ?", (message_id, invoice_id))

    async def cancel_invoice(self, invoice_id: int, user_id: int) -> bool:
        """Close the buyer's own open order. An order that was paid a moment ago is never cancelled
        out from under its buyer."""
        async with self.tx() as conn:
            cur = await conn.execute(
                "UPDATE invoices SET status = 'cancelled' WHERE id = ? AND user_id = ? AND status = 'pending'",
                (invoice_id, user_id),
            )
            return cur.rowcount == 1

    async def expire_invoices(self, grace: int) -> list[Row]:
        """Close open orders whose time ran out (plus `grace` seconds)."""
        async with self.tx() as conn:
            rows = await _all(conn, INVOICE_SQL + " WHERE i.status = 'pending' AND i.expires_at + ? < ?", (grace, _now()))
            for r in rows:
                await conn.execute("UPDATE invoices SET status = 'expired' WHERE id = ?", (r["id"],))
            return rows

    async def items_of(self, invoice_id: int) -> list[str]:
        rows = await self._read_all(
            "SELECT content FROM stock WHERE invoice_id = ? AND status = 'sold' ORDER BY id", (invoice_id,)
        )
        return [r["content"] for r in rows]

    async def orders(self, user_id: int, limit: int = 10) -> list[tuple[Row, list[str]]]:
        rows = await self._read_all(
            INVOICE_SQL + " WHERE i.user_id = ? AND i.status IN ('paid', 'backorder') ORDER BY i.id DESC LIMIT ?",
            (user_id, limit),
        )
        return [(r, await self.items_of(r["id"])) for r in rows]

    async def fulfil_backorders(self, product_id: int) -> list[tuple[Row, list[str]]]:
        """Deliver paid-but-unstocked orders from new stock, oldest first."""
        now, done = _now(), []
        async with self.tx() as conn:
            waiting = await _all(
                conn, INVOICE_SQL + " WHERE i.status = 'backorder' AND i.product_id = ? ORDER BY i.paid_at, i.id", (product_id,)
            )
            for inv in waiting:
                items = await _take_stock(conn, inv, now)
                if items is None:
                    break
                await conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (inv["id"],))
                done.append((await _one(conn, INVOICE_SQL + " WHERE i.id = ?", (inv["id"],)), items))
        return done

    async def summary(self) -> Row:
        since = _now() - 86400
        return await self._read_one(
            """
            SELECT (SELECT COUNT(*) FROM invoices WHERE status = 'pending') AS open_orders,
                   (SELECT COUNT(*) FROM invoices WHERE status = 'backorder') AS backorders,
                   (SELECT COUNT(*) FROM invoices WHERE status IN ('paid', 'backorder') AND paid_at >= ?) AS sales,
                   (SELECT COALESCE(SUM(amount_units), 0) FROM invoices
                     WHERE status IN ('paid', 'backorder') AND paid_at >= ?) AS units
            """,
            (since, since),
        )

    # ---- users ----------------------------------------------------------------------------------

    async def touch_user(self, user_id: int, username: str | None, name: str, action: str) -> None:
        """Record that a user did something (any message or button press)."""
        now = _now()
        async with self.tx() as conn:
            await conn.execute(
                "INSERT INTO users (id, username, name, first_seen, last_seen, clicks, last_action)"
                " VALUES (?, ?, ?, ?, ?, 1, ?)"
                " ON CONFLICT (id) DO UPDATE SET username = excluded.username, name = excluded.name,"
                " last_seen = excluded.last_seen, clicks = clicks + 1, last_action = excluded.last_action",
                (user_id, username, name, now, now, action),
            )

    async def users(self, limit: int | None = None) -> list[Row]:
        """Users, most recently active first, with how many orders each has paid for."""
        return await self._read_all(
            "SELECT u.*, (SELECT COUNT(*) FROM invoices i WHERE i.user_id = u.id"
            " AND i.status IN ('paid', 'backorder')) AS orders FROM users u ORDER BY u.last_seen DESC LIMIT ?",
            (limit if limit else -1,),
        )

    async def user_stats(self) -> Row:
        return await self._read_one(
            "SELECT COUNT(*) AS total, COALESCE(SUM(last_seen >= ?), 0) AS active FROM users", (_now() - 86400,)
        )

    # ---- payments -----------------------------------------------------------------------------

    async def get_cursor(self) -> int | None:
        row = await self._read_one("SELECT value FROM kv WHERE key = 'bsc_cursor'")
        return int(row["value"]) if row else None

    async def set_cursor(self, block: int) -> None:
        async with self.tx() as conn:
            await conn.execute(
                "INSERT INTO kv (key, value) VALUES ('bsc_cursor', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(block),),
            )

    async def transfer_seen(self, tx_hash: str, log_index: int) -> bool:
        row = await self._read_one("SELECT 1 FROM transfers WHERE tx_hash = ? AND log_index = ?", (tx_hash, log_index))
        return row is not None

    async def settle_transfer(self, t: Transfer, *, window: int, slack: int, dust: int, match: Matcher) -> Settlement:
        """Record one incoming transfer and, if it pays exactly one order, complete that order.

        Candidates are orders that were payable when the transfer was made: created at most
        `window` seconds before it and not after it (`slack` absorbs clock differences), and not
        already paid. `match` picks which of them the amount fits; only a single fit is a payment.
        """
        now = _now()
        async with self.tx() as conn:
            if await _one(conn, "SELECT 1 FROM transfers WHERE tx_hash = ? AND log_index = ?", (t.tx_hash, t.log_index)):
                return Settlement("seen")
            fits: list[Row] = []
            if t.value_raw >= dust:
                candidates = await _all(
                    conn,
                    INVOICE_SQL + " WHERE i.status IN ('pending', 'expired', 'cancelled') AND i.created_at BETWEEN ? AND ?",
                    (t.block_time - window - slack, t.block_time + slack),
                )
                fits = match(t.value_raw, candidates)
            if t.value_raw < dust:
                note = "dust"
            else:
                note = "matched" if len(fits) == 1 else "ambiguous" if fits else "unmatched"
            invoice = fits[0] if note == "matched" else None
            await conn.execute(
                "INSERT INTO transfers (tx_hash, log_index, block_number, block_time, from_address, value_raw,"
                " invoice_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t.tx_hash,
                    t.log_index,
                    t.block_number,
                    t.block_time,
                    t.from_address,
                    str(t.value_raw),
                    invoice["id"] if invoice else None,
                    note,
                    now,
                ),
            )
            if invoice is None:
                return Settlement(note, candidate_ids=[f["id"] for f in fits])

            items = await _take_stock(conn, invoice, now)  # None: sold out to earlier payers -> backorder
            await conn.execute(
                "UPDATE invoices SET status = ?, paid_at = ?, tx_hash = ?, paid_raw = ? WHERE id = ?",
                ("paid" if items is not None else "backorder", now, t.tx_hash, str(t.value_raw), invoice["id"]),
            )
            left = await _one(
                conn, "SELECT COUNT(*) FROM stock WHERE product_id = ? AND status = 'available'", (invoice["product_id"],)
            )
            return Settlement(
                note,
                invoice=await _one(conn, INVOICE_SQL + " WHERE i.id = ?", (invoice["id"],)),
                items=items or [],
                sold_out=left[0] == 0,
            )
