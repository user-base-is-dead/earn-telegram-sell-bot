# Timed Discounts — Design

## Problem

Admins have no way to run a time-limited sale on a product. The `products` table
already has unused `offer_price` / `offer_price_inr` / `offer_until` columns
(added in a prior migration, never wired up) — this feature finishes that work.

## Storage

Reuse the existing columns on `products`:

- `offer_price DOUBLE PRECISION DEFAULT 0`
- `offer_price_inr DOUBLE PRECISION DEFAULT 0`
- `offer_until TEXT DEFAULT ''` — ISO-8601 UTC timestamp string (same format as
  `_now()` elsewhere in the codebase); empty string means no active offer.

No new migration needed.

## `app/db/products.py` additions

- `set_product_offer(product_id, offer_usdt, offer_inr, offer_until: str) -> None`
  — writes all three columns.
- `clear_offer(product_id) -> None` — blanks all three columns (offer_price=0,
  offer_price_inr=0, offer_until='').
- `effective_price(p) -> tuple[float, float, bool]` — pure function, no DB call
  (mirrors `effective_stock`). Returns `(usdt, inr, is_discounted)`:
  - if `p["offer_until"]` is non-empty and parses to a time in the future,
    returns `(p["offer_price"], p["offer_price_inr"], True)`
  - else returns `(p["price"], p["price_inr"], False)`
- `clear_expired_offers() -> list[asyncpg.Record]` — single statement:
  `UPDATE products SET offer_price=0, offer_price_inr=0, offer_until=''
  WHERE offer_until != '' AND offer_until <= $1 RETURNING *`. Returns the
  rows that were cleared (empty list if none), for the poller to know what
  changed (not currently used for anything beyond logging, but matches the
  "return what changed" pattern of `decrement_stock`/`pop_unused_keys`).

## Background expiry job

New function `expire_offers(context)` in `app/handlers/announcements.py`
(co-located with the other broadcast/stock-event logic), called by a
`run_repeating` job wired in `app/main.py` next to the `crypto_watch` jobs,
interval 60s. It calls `db.clear_expired_offers()` and does nothing else —
no broadcast on expiry (out of scope; only the start of a sale is announced).

## Buyer-facing price

`db.effective_price(p)` replaces raw `p["price"]/p["price_inr"]` at every
site that displays a price the buyer will actually pay or charges an amount:

- `app/handlers/catalog.py:58` — product detail view
- `app/handlers/payments.py` — the 3 sites reading `p["price"]`/`p["price_inr"]`
  to build an order (this is what makes the discount actually apply at checkout,
  not just cosmetic)
- `app/handlers/topup.py:209` — wallet-purchase charge amount

Admin-only screens (`products_admin.py` manage view, wizards, edit-field
confirmations) keep showing the base `price`/`price_inr` as-is — the admin
needs to see/edit the real price, not the discounted one — plus a new status
line when a discount is currently active (see below).

## Admin flow

**`manage_keyboard` (`app/keyboards.py`)** gets one more row. Label depends on
whether `p["offer_until"]` is currently active (reuse `effective_price`'s
"is it active" check):
- no active offer → `🏷️ Discount` → `discount:<id>`
- active offer → `❌ End discount` → `enddiscount:<id>`

**`product_manage_text` (`products_admin.py`)** gains one line when a discount
is active: `🔥 Discount: <b>{price_both(offer_usdt, offer_inr)}</b> until {local time}`.

**New 3-step wizard** (new `ConversationHandler`, same shape as the existing
keys-upload flow): states `DISCOUNT_PRICE, DISCOUNT_PRICE_INR, DISCOUNT_DURATION`
added to `app/states.py`.

1. `discount:<id>` callback → prompt for discounted USDT price → `DISCOUNT_PRICE`
2. text reply (float, must be > 0 and < current `price`, else re-prompt) →
   prompt for discounted INR price → `DISCOUNT_PRICE_INR`
3. text reply (float ≥ 0; 0 means "no INR discount shown", same convention as
   `price_inr`; no upper-bound check against `price_inr` since a product can
   legitimately have `price_inr == 0` set) → prompt for duration → `DISCOUNT_DURATION`
4. text reply matching `^(\d+)([hdm])$` (hours/days/minutes) → compute
   `offer_until = now + duration`, call `set_product_offer`, show the updated
   manage screen, then call `_offer_announcement` (existing helper) with a new
   announcement kind.

Invalid input at any step re-prompts with an inline error, same pattern as the
existing add-product wizard (`WIZARD_PROMPTS` / validation in `products_admin.py`).
`cancel_kb()` / the shared `common_fallbacks` (`/cancel`) escape the flow, same
as every other conversation in this codebase.

`enddiscount:<id>` callback → `db.clear_offer(pid)` → re-render manage screen.
No confirmation broadcast (ending early is a correction, not an event).

## Announcement text

New `kind == "sale"` branch in `_announcement_text` (`app/handlers/announcements.py`):

```
🔥 Flash Sale! 🔥

<b>{name}</b>
💰 Was: <s>{price_both(old_usdt, old_inr)}</s>
🎉 Now: <b>{price_both(offer_usdt, offer_inr)}</b>

⏰ Ends in {human duration, e.g. "24h"} — grab it before it's gone!

Tap 🛍 Browse products to buy now.
```

Sent through the existing `_offer_announcement` Approve/Deny admin gate — same
mechanism as restock/price-change, nothing new needed there.

## Error handling

- Discount price must be `> 0` and `< price` (a "discount" that isn't cheaper
  is a bug the wizard should catch, not let through) — validated at step 1.
- Duration regex rejects garbage input inline, same as every other numeric
  step in this codebase (re-prompt, don't crash the conversation).
- `effective_price` treats a malformed/unparseable `offer_until` the same as
  "no offer" (falls back to base price) — never lets a parse error block a
  purchase.

## Testing

Extend `app/db/products.py`'s self-check module (`python -m app.db.products`,
`ALLOW_SELFCHECK_DB=1`) with one more `_demo_*` function:
- `effective_price` returns the offer price when `offer_until` is in the
  future, and the base price when it's in the past or empty.
- `clear_expired_offers` clears only rows whose `offer_until` has passed, and
  leaves future/empty ones untouched.

## Out of scope

- No "sale ended" broadcast.
- No stacking discount with `stock`/key-pool restock announcements — they're
  independent events, each gets its own Approve/Deny card if both happen to
  fire.
- No recurring/scheduled discounts (one-shot, admin re-triggers manually).
