# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-user-style Telegram bot (python-telegram-bot v21, long-polling) that sells digital
products: browse catalog → pay via Binance Pay/crypto (manual) or an internal wallet
(auto-confirmed BSC/USDT-BEP20 top-ups and best-effort Binance Pay top-ups) → wallet purchases
auto-deliver a unique code instantly; manual-payment orders still go through buyer-submits-TxID →
admin approves/rejects in-chat → content delivered, stock decremented.

## Commands

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then fill in BOT_TOKEN, ADMIN_IDS, STORE_NAME, etc.
python bot.py                # run the bot (polling) — thin shim into app.main.main()
python view_db.py            # human-readable dump of products & orders
python view_db.py --json     # also writes data/export.json
python view_db.py --orders pending_review
python reset_db.py           # wipe/reset the SQLite DB (destructive)
$env:ALLOW_SELFCHECK_DB=1; python -m app.services.crypto_watch   # self-check (writes real rows — point DATABASE_URL at a scratch DB first)
$env:ALLOW_SELFCHECK_DB=1; python -m app.db.products             # self-check for the stock-decrement concurrency guard (same DB caveat)
```

There is no test suite/linter/build step beyond the self-checks above — run the relevant script to check.

## Architecture

Code lives in the `app/` package; `bot.py` at the repo root is just a two-line entrypoint
(`from app.main import main`). `view_db.py`/`reset_db.py` are standalone CLI utilities that
import `app.config`/`app.db` directly.

- **app/main.py** — composition root: `build_application()` wires every `ConversationHandler` /
  `CallbackQueryHandler` and the wallet-watcher background jobs, `main()` boots the bot.
- **app/config.py** — loads and validates `.env`. `validate()` fails fast if `BOT_TOKEN`/`ADMIN_IDS`
  or no payment method are set. Note `DB_PATH` defaults to a **sibling directory outside the
  repo** (`../telegram-seller-bot-db-main/store.db`), specifically so the DB is never inside the
  git tree — don't "fix" this path to point inside the repo.
- **app/db/** — thin sqlite3 layer (no ORM), split by table domain: `schema.py` (connection +
  migrations + shared constants), `products.py` (incl. the delivery key-pool), `orders.py`,
  `users.py`, `wallet.py` (balances/deposits/idempotency/cursor). `app/db/__init__.py` is a
  **facade** re-exporting the public functions, so callers still just do
  `from app import db; db.get_product(...)` — add new queries in the right submodule, not a
  new call site's own SQL.
- **app/formatting.py / app/keyboards.py / app/render.py** — shared, dependency-light helpers
  (money/text formatting, every keyboard builder, the `_send`/`_render`/`_edit_or_replace`
  primitives every handler goes through). Handler-specific keyboards can still live next to their
  handler; only cross-cutting ones belong in `keyboards.py`. `keyboards.py`'s `_btn()` helper wires
  an optional premium custom emoji (`icon_custom_emoji_id`, requires the bot owner's Telegram
  Premium — see `CUSTOM_EMOJI_IDS` in `.env.example`) onto a button without touching call sites
  that don't configure one; `app/handlers/menu.py::emoji_id_lookup` (admin-only, gated by
  `filters.Entity("custom_emoji")` in `app/main.py` so it can never intercept normal text) is how
  an admin gets real IDs to paste in.
- **app/handlers/** — one module per feature domain (menu, catalog, payments, topup, approvals,
  announcements, products_admin, orders_admin, broadcast, clean). `app/main.py` is the only place
  that imports across all of them to wire `ConversationHandler`s — a handler module importing
  another handler module for a specific helper (e.g. `topup.py` importing `_create_order_for`
  from `payments.py`) is normal; watch for genuine cycles (two modules needing each other) and
  break them by moving the shared piece to a lower-level module (this is why `_edit_admin_msg`/
  `_mark_notification` live in `render.py`, not in whichever handler used them first).
- **app/services/** — `qr.py` (crypto address QR PNGs) and `crypto_watch.py` (the BSC `eth_getLogs`
  poller + best-effort Binance Pay personal-API poller that auto-credit wallet top-ups; also
  backs the "check my payment" self-serve tx lookup). Run standalone
  (`python -m app.services.crypto_watch`) to self-check the matching/idempotency logic against a
  throwaway DB.
- **app/middleware/membership.py** — `TypeHandler` (group `-8`, runs before everything) that
  force-gates non-admin users behind joining two hardcoded Telegram channels. Imports
  `app.render`/`app.handlers.menu` normally at the top of the file (no circular-import workaround
  needed post-restructure — don't reintroduce one).

### Conversation flows (wired in `app/main.py`'s `build_application()`)

Every multi-step interaction (pay → submit UTR, add-product wizard, reject reason, edit field,
approve+deliver, wallet top-up, broadcast compose) is a `ConversationHandler` with
`allow_reentry=True` and a shared `common_fallbacks` list (so `/cancel`, `/start`, and the
persistent menu buttons always escape a stuck flow). States are plain ints in `app/states.py` —
add new ones there, not ad hoc in a handler file, and wire them into the matching
`ConversationHandler` in `build_application()` rather than hand-rolling state tracking in
`context.user_data`.

Handler registration order matters: `membership.force_join_check` runs in group `-8`,
`announcements._track_user` (records every user for broadcast audience) runs in group `-9`, then
the `ConversationHandler`s are added before the plain `CallbackQueryHandler`/`CommandHandler`
fallbacks — later non-conversation handlers only fire when no conversation claims the update.

### Callback data conventions

Inline button `callback_data` follows short colon-separated prefixes matched by regex, e.g.
`menu:home`, `catalog:<page>`, `view:<id>`, `buy:<id>`, `pm:wallet:<id>`,
`paid:<id>`, `approve:<id>`, `reject:<id>`, `edit:<field>:<id>`, `manage:<id>`, `toggle:<id>`,
`keys:<id>`, `topup:rail:<rail>`, `topup:check`, `ann:ok:<id>`. Follow this scheme
(`action:subaction:id`) for any new button rather than inventing a new format.

### Money/rendering helpers

Products carry a single USDT price, set manually per product — see `usdt()` in
`app/formatting.py`. `_pad`/`WIDTH_PAD` is
a Braille-character hack to force a minimum button-menu width in Telegram (no native width
control). Wallet amounts are stored/matched as **integer micro-USDT** (`amount * 1_000_000`),
never floats — see `app/formatting._format_usdt` and `app/db/wallet.py`.

### Order lifecycle

`created → pending_review → approved | rejected` (see `app/db/schema.py` order status column and
`set_order_status`/`reject_order`). Wallet purchases skip straight from `created` to `approved`
with zero admin involvement (`app/handlers/topup.py::_pay_from_wallet`).

### Wallet top-ups (auto-confirmed)

Buyers top up an internal balance via BSC (USDT-BEP20, watched directly on-chain via
`eth_getLogs` against public RPCs — no gateway, no fees, no custodian) and/or Binance Pay
(best-effort: polls a personal read-only API key's `/sapi/v1/pay/transactions`, not the official
Merchant API which needs business KYB). Both rails tag a requested amount with a unique 2-decimal
offset (`app/handlers/topup.py::_unique_tagged_amount`) so a background poller can match an
incoming transfer to the right pending deposit without needing per-user wallet addresses. A
"🔍 Check my payment" self-serve lookup (`crypto_watch.lookup_bsc_tx`/`lookup_binance_pay_txn`)
covers the rare tag mismatch without the buyer ever needing to contact support.

## Security notes

- `.env` and `data/`/the external DB folder are git-ignored; never commit secrets.
- `httpx` logger is forced to WARNING in `app/main.py` because it logs full API URLs containing
  the bot token — don't lower that log level.
- Admin-only commands/handlers gate on `config.is_admin(user_id)` against `ADMIN_IDS`.
- `BINANCE_API_KEY`/`BINANCE_API_SECRET` must be a **read-only** key (Enable Reading only, no
  trading/withdrawal permission) — it only needs to list Pay transaction history.
