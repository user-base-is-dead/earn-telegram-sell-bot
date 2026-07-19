# Auto/Manual Store Mode Toggle — Design

## Problem

Every payment rail (UPI, Binance Pay, Crypto, Wallet) is independently on/off via `.env` presence
checks (`config.upi_enabled()` etc.) — there is no single switch. The seller sometimes runs as a
plain reseller: buyers pay via UPI/Binance Pay/Crypto and an admin manually reviews + delivers each
order. Other times the seller wants zero admin involvement: buyers can only pay from their wallet
balance, which auto-delivers instantly. Today, flipping between these two operating modes means
editing `.env` (blanking `UPI_ID`/`BINANCE_PAY_ID`/`CRYPTO_ADDRESS`) and restarting the bot process
— not something an admin can do from Telegram, and not something that can be flipped live.

## Goal

A single admin-facing toggle, live in the bot, with two mutually exclusive modes:

- **Auto** — only the Wallet payment method is offered. Purchases auto-deliver instantly, no
  admin involvement. Wallet top-up remains available so buyers can fund their balance.
- **Manual** — only UPI / Binance Pay / Crypto are offered (whichever are configured, per the
  existing `.env`-based `_enabled()` checks). Wallet purchases and wallet top-up are both hidden.
  Buyers submit a UTR/TxID/screenshot and an admin approves/rejects + delivers, exactly as today.

Switching modes takes effect immediately for the next screen a buyer opens — no restart. Each
mode's UI (buttons, `/help` text) must read as if the other mode doesn't exist: no mention of
"wallet" while in Manual mode, no mention of "UTR/screenshot/admin review" while in Auto mode.

## Non-goals

- No per-product mode override — this is a single global switch for the whole store.
- No effect on free products (price = 0): `claim_free` already always routes to admin manual
  delivery regardless of payment method, and stays that way in both modes.
- No pause of the BSC/Binance-Pay background watcher jobs or the admin-panel manual-deposit-resolve
  poller — they keep running regardless of mode (still gated only by whether a rail is *configured*
  at all, as today), so a deposit or top-up already in flight when an admin flips the mode still
  resolves correctly instead of being silently abandoned.
- No confirmation dialog on the toggle itself — it's admin-only, instantly reversible, and low
  frequency; a single tap flips it.

## Storage & runtime cache

New single-row table, seeded in `app/db/schema.py`'s `_SCHEMA_SQL` (alongside the existing
migration-guard pattern already used there for `products`/`deposits` columns):

```sql
CREATE TABLE IF NOT EXISTS bot_settings (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    store_mode TEXT NOT NULL DEFAULT 'auto' CHECK (store_mode IN ('auto', 'manual'))
);
INSERT INTO bot_settings (id, store_mode) VALUES (1, 'auto') ON CONFLICT (id) DO NOTHING;
```

Defaults to `'auto'` on first deploy (confirmed default), and `ON CONFLICT DO NOTHING` means an
existing deployment's chosen mode is never overwritten by a future redeploy.

New `app/db/settings.py` (same per-domain split as `products.py`/`wallet.py`/etc.):

```python
STORE_MODE_AUTO = "auto"
STORE_MODE_MANUAL = "manual"

async def get_store_mode() -> str: ...      # SELECT store_mode FROM bot_settings WHERE id = 1
async def set_store_mode(mode: str) -> None: ...  # UPDATE ... ; also sets config.STORE_MODE
```

Re-exported through `app/db/__init__.py`'s facade like every other query module.

**Runtime cache, not a DB read per render:** `main_menu_keyboard`/`main_reply_keyboard` are plain
sync functions called on nearly every screen; making them async just to check the mode would touch
every call site. Instead, `app/config.py` gets a plain mutable module-level cache, matching the
style of its existing `*_enabled()` predicates:

```python
# Runtime-mutable store mode ("auto" | "manual"). Loaded from the DB once at
# startup (app.db.init_pool) and kept in sync live by app.db.settings.set_store_mode
# — not read from .env, so an admin's choice survives restarts without a redeploy.
STORE_MODE = "auto"

def is_auto_mode() -> bool:
    return STORE_MODE == "auto"
```

`app/db/schema.py::init_pool()` reads the row once after creating/migrating the schema and sets
`config.STORE_MODE` from it (it already imports `app.config`, so this is a same-module assignment,
no new import cycle). `app.db.settings.set_store_mode()` is the only other place that mutates it,
right after the DB write, so cache and DB never disagree.

## The toggle: `/mode`

New admin-only command (added to `menu.ADMIN_CMDS`). Shows the current mode and one button to flip
it — same shape as the existing product active/inactive toggle in `products_admin.py`:

```
⚡ Current mode: AUTO (wallet-only)
Buyers can only pay via wallet balance. Purchases deliver instantly with no review.

[ Switch to MANUAL ]
```

Tapping the button fires `mode:set:<auto|manual>`, which calls `db.set_store_mode()` and edits the
message in place to reflect the new state (mirrors `_edit_admin_msg` usage elsewhere). Lives in
`app/handlers/menu.py` next to `refresh_commands`/`help_cmd`; wired in `app/main.py` next to the
other simple `CommandHandler`/`CallbackQueryHandler` admin utilities.

## Purchase flow (`app/handlers/payments.py::buy_product`)

Wrap each rail's existing button-building condition with the mode check, rather than restructuring
the function:

```python
auto = config.is_auto_mode()
wallet_available = auto and config.wallet_topup_enabled() and await db.count_unused_keys(product_id) > 0

methods = []
if not auto:
    if config.upi_enabled() and inr_price > 0:
        methods.append(...)          # UPI button — unchanged except the `not auto` wrap
    if config.binance_pay_enabled():
        methods.append(...)          # Binance Pay button
    if config.blockchain_enabled():
        methods.append(...)          # Crypto button
if wallet_available:
    methods.append(...)              # Wallet button — unchanged except `auto` folded into wallet_available
```

Because Auto mode can only ever produce the single Wallet button (or zero, if wallet isn't
configured/in stock), the existing multi-method chooser screen — and its "This item is fulfilled
manually" note — can never render in Auto mode; no changes needed there. The "single method → skip
straight to it" shortcut just below gets the same `not auto` / `wallet_available` guards on its
existing branches.

If Auto mode has zero methods (wallet not configured, or product's key pool empty), the existing
"no payment method available for this price" fallback screen covers it unchanged.

## Wallet entry points

- `app/keyboards.py::main_menu_keyboard` — the Balance button only appears `if config.is_auto_mode()`;
  Profile keeps its own row alone when Balance is hidden.
- `app/keyboards.py::main_reply_keyboard` — `BTN_TOPUP` row gated by
  `config.is_auto_mode() and config.wallet_topup_enabled()` (currently just the latter).
- `app/handlers/topup.py::show_balance`, `topup_start`, `pay_wallet` — each gets a defensive
  mode check at the top (`if not config.is_auto_mode(): <generic "not available" message>`), so a
  stale button from a screen rendered before an admin's mode flip fails safely instead of opening a
  wallet flow that shouldn't be reachable anymore. The fallback message is generic ("This isn't
  available right now") — it does not mention wallets, to satisfy the no-cross-mode-leakage
  requirement even on this stale-button edge case.
- `app/handlers/payments.py::pay_upi`, `pay_binance`, `pay_blockchain` — same defensive check, the
  other direction (`if config.is_auto_mode(): <generic message>`), for a stale manual-rail button
  tapped after a flip to Auto.

## `/help` text (`app/handlers/menu.py::help_cmd`)

The "How to buy" steps block becomes two separate copies selected by `config.is_auto_mode()`,
rather than filtering individual lines — the two flows differ structurally (step count, what to do
after paying), not just in which rail names appear:

- **Auto copy:** browse → buy → pay from wallet → instant delivery. Mentions topping up the wallet
  as the one and only way to pay. No mention of UTR, screenshots, or admin review.
- **Manual copy:** browse → buy → choose UPI/Binance Pay/Crypto (whichever configured) → pay exact
  amount → tap "I've Paid" → send UTR/TxID/screenshot → wait for admin review. No mention of the
  wallet or instant delivery.

The existing admin-only section (add product / manage / all orders / users / cleanup) is unaffected
by mode and unchanged.

## Testing

No test suite exists for the Python bot side (per `CLAUDE.md`, only the two `ALLOW_SELFCHECK_DB=1`
self-checks exist, neither touches this). Verification is manual, run against a scratch DB:

1. Fresh DB → confirm `bot_settings` seeds to `'auto'` and `/mode` reports Auto.
2. In Auto mode: open a paid product → confirm only the Wallet button shows (or the "no payment
   method" screen if wallet isn't configured); confirm Balance/top-up buttons are visible and work;
   confirm `/help` never mentions UTR/screenshot/admin review.
3. Tap `/mode` → Switch to Manual → confirm the command message updates immediately.
4. In Manual mode: open the same product → confirm only UPI/Binance Pay/Crypto show (per whatever
   is configured in `.env`); confirm Balance/top-up buttons are gone from both the inline and
   reply keyboards; confirm `/help` never mentions the wallet.
5. Tap a stale Wallet-purchase button captured from step 2 while still in Manual mode → confirm a
   generic "not available" response, not a completed purchase.
6. Start a wallet top-up in Auto mode, flip to Manual before the deposit is credited, confirm the
   background poller still credits it once the transfer lands (background jobs are mode-independent).
