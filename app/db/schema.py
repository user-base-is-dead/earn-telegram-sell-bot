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
DEPOSIT_REJECTED = "rejected"  # admin marked it as never received (see admin panel /deposits)

RAIL_BSC = "bsc"
RAIL_BINANCE_PAY = "binance_pay"

STORE_MODE_AUTO = "auto"        # wallet-only, instant delivery, zero admin involvement
STORE_MODE_MANUAL = "manual"    # UPI/Binance Pay/Crypto, admin reviews + delivers

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
    offer_until     TEXT NOT NULL DEFAULT '',
    name_html       TEXT,
    description_html TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    username      TEXT NOT NULL DEFAULT '',
    product_id    BIGINT NOT NULL REFERENCES products(id),
    product_name  TEXT NOT NULL,
    amount        DOUBLE PRECISION NOT NULL,
    amount_usdt   DOUBLE PRECISION NOT NULL DEFAULT 0,
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
    credited_tx_ref     TEXT NOT NULL DEFAULT '',
    notified            INTEGER NOT NULL DEFAULT 0
);
-- Two pending deposits on the same rail can never share a tagged amount — the
-- watcher matches an incoming transfer to a deposit by amount alone, so a
-- collision would let one buyer's payment get credited to a different buyer.
-- Enforced here (not just by picking an unused amount before inserting) so a
-- race between two concurrent top-up requests can't create one anyway.
CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_pending_tag
    ON deposits(rail, tagged_amount_micro) WHERE status = 'pending';
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

CREATE TABLE IF NOT EXISTS bot_settings (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    store_mode TEXT NOT NULL DEFAULT 'auto' CHECK (store_mode IN ('auto', 'manual'))
);
INSERT INTO bot_settings (id, store_mode) VALUES (1, 'auto') ON CONFLICT (id) DO NOTHING;
"""


async def init_pool() -> None:
    """Create the connection pool and the schema if it doesn't exist yet. Call once
    at startup, from inside a running event loop (e.g. python-telegram-bot's post_init)."""
    global _pool
    # statement_cache_size=0: Supabase's transaction-mode pooler (pgbouncer) doesn't
    # support prepared statements, which asyncpg uses by default.
    _pool = await asyncpg.create_pool(
        config.DATABASE_URL, min_size=1, max_size=10, statement_cache_size=0
    )
    async with _pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)
        # Added after initial deployment — CREATE TABLE IF NOT EXISTS above is a
        # no-op on an already-existing table, so these need their own guard to
        # reach production. Nullable: NULL means "no custom emoji captured,
        # render the plain name/description instead" (see app.formatting.render_name).
        await conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS name_html TEXT")
        await conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS description_html TEXT")
        # Per-product icon, settable in-chat (see products_admin.edit_icon): icon_char is
        # always the plain emoji character (works everywhere, no Premium needed);
        # icon_emoji_id is only set when the admin sent a Premium custom/animated emoji,
        # and takes priority when present (see app.formatting.render_name_with_icon /
        # app.keyboards._product_icon_btn). NULL icon_char -> fall back to the global
        # "product" CUSTOM_EMOJI_IDS icon.
        await conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS icon_char TEXT")
        await conn.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS icon_emoji_id TEXT")
        # notified: whether the buyer has been DM'd about a manual (admin-panel) credit —
        # only meaningful for deposits.credited_tx_ref LIKE 'manual:%'; auto-matched
        # deposits are notified inline by credit_deposit_once and never check this.
        await conn.execute("ALTER TABLE deposits ADD COLUMN IF NOT EXISTS notified INTEGER NOT NULL DEFAULT 0")
        # How many units an order is for — old rows default to 1 (they always
        # were exactly 1 unit, from before quantity > 1 purchases existed).
        await conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS qty INTEGER NOT NULL DEFAULT 1")
        # Freeform admin context on a buyer (e.g. "disputed a charge once,
        # watch for repeat"), editable from the admin panel's user detail page.
        # Single field, overwritten on save — not a timestamped note history.
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT ''")
        row = await conn.fetchrow("SELECT store_mode FROM bot_settings WHERE id = 1")
        config.STORE_MODE = row["store_mode"] if row else "auto"


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
