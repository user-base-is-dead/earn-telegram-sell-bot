# Admin panel design

Date: 2026-07-15

## Goal

Replace the current "admin does everything through Telegram messages" workflow with a proper
web admin panel: live dashboard/KPIs, product management, order/manual-approval queue, and
user/wallet visibility. Single admin (the bot owner), authenticated with Telegram.

Prerequisite decision: move the datastore from local SQLite to a managed Supabase Postgres
instance, so the DB survives the server dying and the panel can read it without depending on
the bot process or the bot's filesystem.

This is two sequential sub-projects:

- **Phase 1** — swap SQLite for Supabase Postgres under the existing bot. Bot behavior is
  unchanged; only `app/db/` internals change.
- **Phase 2** — build the Next.js admin panel as a new, separately-deployed app reading/writing
  the same Postgres database.

Each phase is independently testable and gets its own implementation plan; this spec covers the
design for both since they share one database schema decision.

## Phase 1 — Postgres migration

### Driver choice

`app/db/` today is a thin hand-written-SQL layer over `sqlite3` (see `schema.py`, `products.py`,
`orders.py`, `users.py`, `wallet.py`), explicitly not an ORM. Keep that shape — swap the driver
to `asyncpg` (async connection pool), which matches python-telegram-bot v21's asyncio model and
requires no new query-building abstraction. No SQLAlchemy, no ORM: the existing pattern already
fits, just on a different driver.

- `config.DB_PATH` → `config.DATABASE_URL` (Supabase Postgres connection string, `.env`).
- `app/db/schema.py::_connect()` becomes an `asyncpg.Pool` created once in `app/main.py` at
  startup and passed down (or held as a module-level pool, matching how the sqlite connection is
  currently opened per-call — a pool is the direct equivalent).
- All `?` placeholders become `$1, $2, ...`; `sqlite3.Row` access patterns (`row["col"]`) map
  directly onto `asyncpg.Record`, which is also dict-like — call sites in `products.py`,
  `orders.py`, `users.py`, `wallet.py` need placeholder + `execute`/`fetch` syntax updates but no
  structural rewrite.
- Drop the SQLite-only PRAGMAs (`journal_mode`, `busy_timeout`, `synchronous`,
  `foreign_keys` — Postgres doesn't need WAL tuning and always enforces FKs on declared
  constraints).

### Schema mapping

| SQLite (current)                    | Postgres                              |
|--------------------------------------|----------------------------------------|
| `INTEGER PRIMARY KEY AUTOINCREMENT`  | `BIGSERIAL PRIMARY KEY`                |
| `TEXT`                                | `TEXT`                                 |
| `REAL` (prices: `amount`, `price`, `price_inr`, `offer_price`, `offer_price_inr`, `amount_usdt`) | `DOUBLE PRECISION` (unchanged semantics — these are already display/manual prices, not accumulated) |
| `INTEGER` (micro-USDT: `tagged_amount_micro`, `delta_micro`, `balance_after_micro`, `wallet_balance_micro`) | `BIGINT` (was already integer math in SQLite; BIGINT avoids any future overflow past `INTEGER`'s 32-bit range in a strict Postgres type) |
| `TEXT` timestamps (`created_at` etc, ISO8601 strings via `_now()`) | keep as `TEXT` — no behavior depends on native Postgres `TIMESTAMPTZ`, and every read/write already goes through `_now()`; changing the type is a separate, unrequested improvement |
| `FOREIGN KEY (product_id) REFERENCES products(id)` | same, unchanged |
| `CREATE INDEX IF NOT EXISTS ...` | same, unchanged (all 6 existing indexes carry over as-is) |

All 8 tables carry over 1:1: `products`, `orders`, `users`, `deposits`, `wallet_ledger`,
`product_keys`, `processed_tx`, `chain_cursor`. No new tables needed for Phase 1 — the migrations
block at the bottom of `schema.py` (the `ALTER TABLE ... ADD COLUMN` guards) is a
SQLite-vs-fresh-DB concern that Postgres's `CREATE TABLE` script replaces outright; a fresh
Postgres schema is created with every current column already present, so those `ALTER TABLE`
guards are dropped, not ported.

### Data migration

One-off script `migrate_to_postgres.py` (repo root, alongside `reset_db.py`/`view_db.py`):
connects to the existing SQLite file and the new Postgres DB, copies every table's rows over in
FK-safe order (`products` → `orders`/`product_keys`, `users`, `deposits`, `wallet_ledger`,
`processed_tx`, `chain_cursor`), run once during cutover. Not a repeating sync — after cutover,
Postgres is the only datastore and the SQLite file is retired.

### Cutover plan

1. Provision Supabase project, run the new Postgres schema script.
2. Run `migrate_to_postgres.py` against the current production SQLite file.
3. Deploy the `asyncpg`-based bot pointed at `DATABASE_URL`, verify against
   `python -m app.services.crypto_watch` self-check plus a manual smoke pass (browse catalog, buy
   with wallet, submit UTR, approve).
4. Old SQLite file kept as a cold backup for a while, then deleted.

## Phase 2 — Admin panel

### Stack

Next.js (App Router) + Tailwind + shadcn/ui + Recharts, deployed as its own app (e.g. Vercel) in
a new top-level `admin/` directory — separate `package.json`, separate deploy, no shared runtime
with the Python bot. The two apps' only coupling is the Postgres database.

### Auth — Telegram Login Widget

Single admin, so no user-management system:

1. Login page embeds the Telegram Login Widget, configured with the bot's username. (One-time
   manual prerequisite: set the panel's domain on the bot via BotFather's `/setdomain`.)
2. Widget redirects back with a signed payload (`id`, `first_name`, `username`, `auth_date`,
   `hash`). One Route Handler (`app/api/auth/telegram/route.ts`) verifies the HMAC-SHA256 hash
   using `BOT_TOKEN` (server-side env var only, never shipped to the browser) per Telegram's
   documented widget-verification algorithm, and checks `id` against `ADMIN_IDS` (same list as
   the bot's, duplicated as a panel env var — it's one or two numeric IDs that rarely change, not
   worth a shared config service).
3. On success, sets a signed httpOnly session cookie (e.g. via `jose`/`iron-session` — one
   dependency, no full auth framework). Next.js middleware checks this cookie on every
   `/admin/*` route and redirects to login otherwise.

No Supabase Auth. A single hardcoded admin doesn't need Supabase's user/session system — that
would mean provisioning a Supabase Auth user, managing its password/JWT lifecycle, and writing
RLS policies for exactly one caller. The httpOnly cookie is smaller and does the same job.

### Data access

All Postgres reads/writes happen **server-side only** (Server Components, Server Actions, Route
Handlers) using the Supabase **service-role key**, which is never sent to the browser. Because
there's exactly one trusted caller (the authenticated admin session, already gated by the cookie
+ middleware above), Postgres Row-Level-Security policies are skipped entirely — RLS exists to
constrain which rows an untrusted client can touch, and there is no untrusted client here. Adding
RLS would mean also adding Supabase Auth (JWTs carry the identity RLS policies check against),
which was already ruled out above.

### Live updates

Supabase Realtime (logical replication over the existing Postgres instance, no extra
infrastructure to run) is used, but subscribed to **server-side** in one Route Handler using the
service-role key, and relayed to the browser tab over Server-Sent Events. This keeps the
service-role key off the client while still giving push updates. The alternative — subscribing
directly from the browser with the public anon key — would require RLS + Supabase Auth to be
safe (anyone with the anon key, which ships in the JS bundle, could otherwise read order/wallet
rows), which is more total complexity than the one SSE-relay route.

Scope of what pushes live: new orders, new pending-review submissions, low-stock crossings,
wallet deposits credited. Everything else (historical tables, reports) loads on navigation/normal
fetch — no need for every screen to be a live stream.

### Feature scope

- **Dashboard** — revenue (INR/USDT) and order-count KPIs (today/week/month), a sales trend
  chart, a low-stock banner (products where `stock >= 0 AND stock <= threshold`), a
  pending-approval counter linking into the Orders view.
- **Products** — list/create/edit/deactivate, stock adjustment, price + offer-price/offer-until
  fields (mirrors what `products_admin.py` already does via bot conversation), and a view into
  the `product_keys` delivery pool (remaining unused count per product, ability to bulk-add
  codes).
- **Orders** — table of all orders, filterable by status; a queue view for `pending_review` with
  the submitted UTR/ref and one-click approve/reject (writes to the same `orders` table the bot
  reads, so a Telegram-side approval and a panel-side approval can never both fire — whichever
  transitions the status first wins, the other sees it's no longer `pending_review`).
- **Users & wallet** — user list with wallet balance, a per-user `wallet_ledger` view (reuses the
  existing ledger-sums-match-balance invariant from `view_db.py --ledger` as the panel's audit
  check), and deposit history per rail (`bsc` / `binance_pay`).

### Notifications

Stay on Telegram — the bot already messages the admin for approvals; low-stock and other alerts
extend that same channel rather than adding browser push (service worker + VAPID key management)
for a single admin who's already watching Telegram.

## Non-goals / explicitly deferred

- Multi-admin roles/permissions — single admin only.
- Postgres RLS — no untrusted client exists yet; add if the panel ever gets a second caller class.
- Browser push notifications — Telegram covers it.
- A custom analytics/reporting engine — dashboard queries read directly off existing tables; add
  a materialized view only if a specific query is measurably slow, not preemptively.
- Timestamp column type migration (`TEXT` → `TIMESTAMPTZ`) — unrelated to this project's goal,
  can be a separate cleanup later if it ever matters.

## Implementation phases

1. **Phase 1a** — Postgres schema + `asyncpg` migration of `app/db/`, data migration script,
   cutover, smoke-test.
2. **Phase 2a** — Next.js app scaffold, Telegram Login Widget auth, session middleware.
3. **Phase 2b** — Dashboard + Orders (highest-value: replaces the most frequent Telegram-admin
   interaction).
4. **Phase 2c** — Products management + delivery-key pool.
5. **Phase 2d** — Users/wallet ledger view.
6. **Phase 2e** — Realtime (SSE relay) wired into Dashboard/Orders once the static versions work.
