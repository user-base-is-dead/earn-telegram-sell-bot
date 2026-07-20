"""Connection helper, schema, and shared status constants (local SQLite via aiosqlite).

This module used to wrap a managed Postgres pool (asyncpg). It now backs onto a
single local SQLite file (config.DB_PATH, e.g. ../earn-seller-bot-db-main/store.db)
through a thin asyncpg-compatible shim, so the query modules in app/db/*.py keep
their exact call surface — ``conn.fetch/fetchrow/fetchval/execute/executemany``,
``$1`` placeholders, ``conn.transaction()`` and ``asyncpg.Record``-style rows —
without a rewrite of every caller.

How the shim maps to SQLite:
  * ``$1, $2 ...`` positional placeholders are rewritten to SQLite's numbered
    ``?1, ?2 ...`` form (so a repeated/out-of-order ``$N`` still binds correctly).
  * ``FOR UPDATE [SKIP LOCKED]`` is stripped — every DB block runs under one
    process-wide asyncio lock inside a single transaction, which serializes
    writers more strictly than the row locks the Postgres code relied on.
  * ``execute()`` returns an asyncpg-style command tag ("UPDATE 1", "DELETE 3")
    so callers that inspect it (``result.endswith(" 1")`` / ``result.split()``)
    keep working.
  * rows are ``sqlite3.Row`` (index + name access + ``.keys()``).
"""
import asyncio
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional, Sequence

import aiosqlite

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
DEPOSIT_REJECTED = "rejected"  # admin marked it as never received (see admin panel /deposits)

RAIL_BSC = "bsc"
RAIL_BINANCE_PAY = "binance_pay"

STORE_MODE_AUTO = "auto"        # wallet-only, instant delivery, zero admin involvement
STORE_MODE_MANUAL = "manual"    # Binance Pay/Crypto, admin reviews + delivers

# Public order reference: date + random code (e.g. 260618-K7P2). Unambiguous
# alphabet (no 0/O/1/I/L) so it never collides with or resembles an old ID.
_REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# --------------------------------------------------------------------------- #
# asyncpg -> SQLite dialect shim
# --------------------------------------------------------------------------- #
Record = sqlite3.Row  # asyncpg.Record stand-in: index + name access + .keys()

_PLACEHOLDER_RE = re.compile(r"\$(\d+)")
# Strips a trailing "FOR UPDATE" / "FOR UPDATE SKIP LOCKED" (Postgres row locks
# SQLite has no equivalent for — the process-wide lock makes them unnecessary).
_FOR_UPDATE_RE = re.compile(r"\s+FOR\s+UPDATE(\s+SKIP\s+LOCKED)?", re.IGNORECASE)


def _translate(sql: str) -> str:
    """Rewrite Postgres-flavoured SQL into what SQLite accepts. Only the two
    differences the query modules actually use are handled here; anything more
    exotic (arrays, ALTER SEQUENCE) is rewritten at the call site instead."""
    sql = _FOR_UPDATE_RE.sub("", sql)
    sql = _PLACEHOLDER_RE.sub(r"?\1", sql)
    return sql


class Connection:
    """asyncpg.Connection-compatible facade over one aiosqlite connection."""

    def __init__(self, conn: "aiosqlite.Connection") -> None:
        self._conn = conn

    async def fetch(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        cur = await self._conn.execute(_translate(sql), args)
        try:
            return list(await cur.fetchall())
        finally:
            await cur.close()

    async def fetchrow(self, sql: str, *args: Any) -> Optional[sqlite3.Row]:
        cur = await self._conn.execute(_translate(sql), args)
        try:
            return await cur.fetchone()
        finally:
            await cur.close()

    async def fetchval(self, sql: str, *args: Any) -> Any:
        row = await self.fetchrow(sql, *args)
        if row is None:
            return None
        return row[0]

    async def execute(self, sql: str, *args: Any) -> str:
        cur = await self._conn.execute(_translate(sql), args)
        try:
            verb = sql.strip().split(None, 1)[0].upper() if sql.strip() else ""
            count = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            return f"{verb} {count}"
        finally:
            await cur.close()

    async def executemany(self, sql: str, args_seq: Sequence[Sequence[Any]]) -> str:
        cur = await self._conn.executemany(_translate(sql), list(args_seq))
        try:
            return "EXECUTE"
        finally:
            await cur.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["Connection"]:
        """No-op nested transaction: the surrounding _connect() block already
        runs inside a single serialized transaction, so a nested asyncpg-style
        .transaction() needs no separate savepoint to stay all-or-nothing."""
        yield self


_conn: Optional[aiosqlite.Connection] = None
_lock: Optional[asyncio.Lock] = None

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    price           REAL NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    stock           INTEGER NOT NULL DEFAULT -1,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    offer_price     REAL NOT NULL DEFAULT 0,
    offer_until     TEXT NOT NULL DEFAULT '',
    name_html       TEXT,
    description_html TEXT,
    icon_char       TEXT,
    icon_emoji_id   TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    username      TEXT NOT NULL DEFAULT '',
    product_id    INTEGER NOT NULL REFERENCES products(id),
    product_name  TEXT NOT NULL,
    amount        REAL NOT NULL DEFAULT 0,
    amount_usdt   REAL NOT NULL DEFAULT 0,
    qty           INTEGER NOT NULL DEFAULT 1,
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
    user_id              INTEGER PRIMARY KEY,
    first_name           TEXT NOT NULL DEFAULT '',
    username             TEXT NOT NULL DEFAULT '',
    started_at           TEXT NOT NULL,
    clicks               INTEGER NOT NULL DEFAULT 0,
    wallet_balance_micro INTEGER NOT NULL DEFAULT 0,
    notes                TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS deposits (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    rail                TEXT NOT NULL,
    tagged_amount_micro INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    credited_tx_ref     TEXT NOT NULL DEFAULT '',
    notified            INTEGER NOT NULL DEFAULT 0
);
-- Two pending deposits on the same rail can never share a tagged amount — the
-- watcher matches an incoming transfer to a deposit by amount alone, so a
-- collision would let one buyer's payment get credited to a different buyer.
CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_pending_tag
    ON deposits(rail, tagged_amount_micro) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_deposits_rail_status ON deposits(rail, status);

CREATE TABLE IF NOT EXISTS wallet_ledger (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    delta_micro          INTEGER NOT NULL,
    reason               TEXT NOT NULL,
    ref                  TEXT NOT NULL DEFAULT '',
    balance_after_micro  INTEGER NOT NULL,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user ON wallet_ledger(user_id);

CREATE TABLE IF NOT EXISTS product_keys (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id       INTEGER NOT NULL REFERENCES products(id),
    code             TEXT NOT NULL,
    used             INTEGER NOT NULL DEFAULT 0,
    used_by_order_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_product_keys_unused ON product_keys(product_id, used);

CREATE TABLE IF NOT EXISTS processed_tx (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rail        TEXT NOT NULL,
    tx_ref      TEXT NOT NULL,
    credited_at TEXT NOT NULL,
    UNIQUE(rail, tx_ref)
);

CREATE TABLE IF NOT EXISTS chain_cursor (
    rail       TEXT PRIMARY KEY,
    last_block INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_settings (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    store_mode TEXT NOT NULL DEFAULT 'auto' CHECK (store_mode IN ('auto', 'manual'))
);
INSERT INTO bot_settings (id, store_mode) VALUES (1, 'auto') ON CONFLICT (id) DO NOTHING;
"""


async def _column_exists(conn: aiosqlite.Connection, table: str, column: str) -> bool:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    return any(r[1] == column for r in rows)  # r[1] = column name


async def _add_column_if_missing(
    conn: aiosqlite.Connection, table: str, column: str, decl: str
) -> None:
    """SQLite has no ``ADD COLUMN IF NOT EXISTS``; emulate it so upgrading an
    already-existing local store.db from an older schema is a safe no-op."""
    if not await _column_exists(conn, table, column):
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def init_pool() -> None:
    """Open the local SQLite file (creating it and its parent dir if needed),
    apply the schema, and load the runtime store-mode. Call once at startup from
    inside a running event loop (e.g. python-telegram-bot's post_init)."""
    global _conn, _lock
    path = config.DB_PATH
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    # isolation_level=None -> autocommit; we drive BEGIN/COMMIT explicitly in
    # _connect() so each DB block is one atomic, serialized transaction.
    _conn = await aiosqlite.connect(path, isolation_level=None)
    _conn.row_factory = sqlite3.Row
    _lock = asyncio.Lock()

    await _conn.execute("PRAGMA journal_mode=WAL")     # readers (view_db) don't block the bot's writes
    await _conn.execute("PRAGMA foreign_keys=ON")      # enforce product_keys -> products, etc.
    await _conn.execute("PRAGMA busy_timeout=5000")    # wait, don't error, on brief lock contention

    await _conn.executescript(_SCHEMA_SQL)

    # Columns added after the original schema shipped — guarded so an older
    # local DB picks them up without wiping data (see _add_column_if_missing).
    await _add_column_if_missing(_conn, "products", "name_html", "TEXT")
    await _add_column_if_missing(_conn, "products", "description_html", "TEXT")
    await _add_column_if_missing(_conn, "products", "icon_char", "TEXT")
    await _add_column_if_missing(_conn, "products", "icon_emoji_id", "TEXT")
    await _add_column_if_missing(_conn, "deposits", "notified", "INTEGER NOT NULL DEFAULT 0")
    await _add_column_if_missing(_conn, "orders", "qty", "INTEGER NOT NULL DEFAULT 1")
    await _add_column_if_missing(_conn, "users", "notes", "TEXT NOT NULL DEFAULT ''")

    cur = await _conn.execute("SELECT store_mode FROM bot_settings WHERE id = 1")
    try:
        row = await cur.fetchone()
    finally:
        await cur.close()
    config.STORE_MODE = row["store_mode"] if row else "auto"


async def close_pool() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


@asynccontextmanager
async def _connect() -> AsyncIterator[Connection]:
    """Yield a connection wrapped in one serialized, all-or-nothing transaction.

    A single process-wide lock guarantees no two DB blocks interleave, so the
    read-then-write sequences the money paths depend on (stock decrement, wallet
    debit, deposit crediting) stay atomic without Postgres row locks."""
    if _conn is None or _lock is None:
        raise RuntimeError("Database not initialized — call init_pool() first.")
    async with _lock:
        await _conn.execute("BEGIN")
        try:
            yield Connection(_conn)
        except BaseException:
            await _conn.rollback()
            raise
        else:
            await _conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
