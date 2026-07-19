# Timed Discounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin put a product on a timed discount (discounted USDT/INR price + duration) that auto-expires and reverts, with the discount applying to actual checkout price, plus an announcement broadcast on launch.

**Architecture:** Finish the already-present-but-unused `offer_price`/`offer_price_inr`/`offer_until` columns on `products`. A pure `effective_price()` helper (mirroring the existing `effective_stock()`) is the single source of truth read at every buyer-facing price site; a 60s background job clears expired offers; a small admin ConversationHandler (mirrors the existing keys-upload flow) sets/ends a discount.

**Tech Stack:** python-telegram-bot v21 ConversationHandler, asyncpg, existing `app/db`, `app/handlers`, `app/keyboards.py` patterns. No test framework — this repo uses assert-based `_demo()` self-check functions run via `python -m app.db.products` (see `ALLOW_SELFCHECK_DB=1` convention in CLAUDE.md).

---

### Task 1: `effective_price` / offer-management functions in `app/db/products.py`

**Files:**
- Modify: `app/db/products.py`

- [ ] **Step 1: Add the write/clear/compute functions**

Add after `get_effective_stock` (after line 177):

```python
async def set_product_offer(product_id: int, offer_price: float, offer_price_inr: float, offer_until: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE products SET offer_price = $1, offer_price_inr = $2, offer_until = $3 WHERE id = $4",
            offer_price, offer_price_inr, offer_until, product_id,
        )


async def clear_offer(product_id: int) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE products SET offer_price = 0, offer_price_inr = 0, offer_until = '' WHERE id = $1",
            product_id,
        )


def effective_price(p) -> tuple[float, float, bool]:
    """A product's real charge/display price: its discount price while
    offer_until is set and still in the future, else its normal price.
    Pure (no DB call) so callers can use it on a Record they already have,
    the same shape as effective_stock(). ISO-8601 UTC strings compare
    correctly lexicographically (see app.db.wallet's expires_at checks)."""
    offer_until = p["offer_until"] if "offer_until" in p.keys() else ""
    if offer_until and offer_until > _now():
        return float(p["offer_price"]), float(p["offer_price_inr"]), True
    return float(p["price"]), float(p["price_inr"]), False


async def clear_expired_offers() -> list[asyncpg.Record]:
    """Revert every product whose discount window has passed. Returns the
    rows that were cleared, for the caller to log/inspect."""
    async with _connect() as conn:
        return await conn.fetch(
            """UPDATE products SET offer_price = 0, offer_price_inr = 0, offer_until = ''
               WHERE offer_until != '' AND offer_until <= $1
               RETURNING *""",
            _now(),
        )
```

- [ ] **Step 2: Export the new functions from the facade**

Read `app/db/__init__.py` first to see the exact existing import/`__all__` block shape (it lists `products.py` functions individually, e.g. `"decrement_stock", "delete_product", "effective_stock", "get_effective_stock",`).

Add `clear_expired_offers`, `clear_offer`, `effective_price`, `set_product_offer` to both the `from app.db.products import (...)` block and the `__all__` list, alphabetically alongside the existing entries.

- [ ] **Step 3: Write the self-check**

Add a new function at the end of `app/db/products.py`, right before the `if __name__ == "__main__":` block:

```python
async def _demo_effective_price() -> None:
    """Self-check: effective_price uses the offer price only while offer_until
    is in the future; clear_expired_offers reverts only rows whose window has
    passed, and never touches rows with no offer or a future one. Writes real
    rows — must be run against a disposable/test DATABASE_URL, never production."""
    from datetime import datetime, timedelta, timezone

    from app import db as _db

    await _db.init_pool()
    try:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")

        active_pid = await add_product("selftest-offer-active", "", 10.0, "content", price_inr=900.0)
        await set_product_offer(active_pid, 5.0, 450.0, future)
        p = await get_product(active_pid)
        usdt_p, inr_p, discounted = effective_price(p)
        assert (usdt_p, inr_p, discounted) == (5.0, 450.0, True), (
            f"expected discounted price while offer_until is future, got {(usdt_p, inr_p, discounted)}"
        )

        expired_pid = await add_product("selftest-offer-expired", "", 10.0, "content", price_inr=900.0)
        await set_product_offer(expired_pid, 5.0, 450.0, past)
        p = await get_product(expired_pid)
        usdt_p, inr_p, discounted = effective_price(p)
        assert (usdt_p, inr_p, discounted) == (10.0, 900.0, False), (
            f"expected normal price once offer_until is past, got {(usdt_p, inr_p, discounted)}"
        )

        no_offer_pid = await add_product("selftest-offer-none", "", 10.0, "content", price_inr=900.0)
        p = await get_product(no_offer_pid)
        usdt_p, inr_p, discounted = effective_price(p)
        assert (usdt_p, inr_p, discounted) == (10.0, 900.0, False), (
            f"expected normal price with no offer set, got {(usdt_p, inr_p, discounted)}"
        )

        cleared = await clear_expired_offers()
        cleared_ids = {r["id"] for r in cleared}
        assert expired_pid in cleared_ids, "expired offer must be cleared"
        assert active_pid not in cleared_ids, "future offer must not be cleared"
        assert no_offer_pid not in cleared_ids, "product with no offer must not be touched"

        p = await get_product(expired_pid)
        assert p["offer_until"] == "" and p["offer_price"] == 0, "cleared row must have offer fields reset"
        p = await get_product(active_pid)
        assert p["offer_until"] == future, "future offer must be untouched by clear_expired_offers"

        await clear_offer(active_pid)
        p = await get_product(active_pid)
        assert p["offer_until"] == "" and p["offer_price"] == 0, "clear_offer must reset all offer fields"

        await delete_product(active_pid)
        await delete_product(expired_pid)
        await delete_product(no_offer_pid)
    finally:
        await _db.close_pool()

    print("effective-price self-check: all assertions passed")
```

Then update the `if __name__ == "__main__":` block at the bottom of the file to also run it:

```python
if __name__ == "__main__":
    import asyncio

    from app import config

    config.require_disposable_db_for_selfcheck()
    asyncio.run(_demo())
    asyncio.run(_demo_effective_stock())
    asyncio.run(_demo_effective_price())
```

- [ ] **Step 4: Run the self-check**

Run: `$env:ALLOW_SELFCHECK_DB=1; python -m app.db.products` (point `DATABASE_URL` at a scratch DB first, per CLAUDE.md)
Expected: all three self-check functions print "... all assertions passed", no assertion errors.

- [ ] **Step 5: Commit**

```bash
git add app/db/products.py app/db/__init__.py
git commit -m "feat: add effective_price and offer expiry to product db layer"
```

---

### Task 2: Wire buyer-facing price sites to `effective_price`

**Files:**
- Modify: `app/handlers/catalog.py`
- Modify: `app/handlers/payments.py`
- Modify: `app/handlers/topup.py`

- [ ] **Step 1: `catalog.py` product detail view**

In `view_product` (`app/handlers/catalog.py`), replace:

```python
    text += f"\n{cemoji('money', '💰')} <b>Price: {esc(price_both(p['price'], p['price_inr']))}</b>"
```

with:

```python
    offer_usdt, offer_inr, is_discounted = db.effective_price(p)
    if is_discounted:
        text += (
            f"\n{cemoji('sale', '🔥')} <b>Flash sale!</b>\n"
            f"{cemoji('money', '💰')} <s>{esc(price_both(p['price'], p['price_inr']))}</s> → "
            f"<b>{esc(price_both(offer_usdt, offer_inr))}</b>"
        )
    else:
        text += f"\n{cemoji('money', '💰')} <b>Price: {esc(price_both(p['price'], p['price_inr']))}</b>"
```

- [ ] **Step 2: `payments.py` — the 3 sites reading raw price**

In `app/handlers/payments.py`, replace each of these three occurrences:

Line 67 (`buy_product`):
```python
    usdt_price, inr_price = float(p["price"]), float(p["price_inr"] or 0)
```
→
```python
    usdt_price, inr_price, _ = db.effective_price(p)
```

Line 162 (`_offer_payment_methods`):
```python
    usdt_price, inr_price = float(p["price"]), float(p["price_inr"] or 0)
```
→
```python
    usdt_price, inr_price, _ = db.effective_price(p)
```

Line 251 (`_create_order_for`):
```python
    usdt_price, inr_price = float(p["price"]), float(p["price_inr"] or 0)
```
→
```python
    usdt_price, inr_price, _ = db.effective_price(p)
```

- [ ] **Step 3: `topup.py` — wallet debit amount**

In `_pay_from_wallet` (`app/handlers/topup.py:209`), replace:

```python
    price_micro = int(Decimal(str(p["price"])) * 1_000_000) * qty
```

with:

```python
    usdt_price, _, _ = db.effective_price(p)
    price_micro = int(Decimal(str(usdt_price)) * 1_000_000) * qty
```

- [ ] **Step 4: Manual smoke check (no test framework covers Telegram handlers in this repo)**

Run: `python bot.py` (point `DATABASE_URL` at a scratch/dev DB), then in Telegram:
1. As admin, run the discount wizard once Task 4 is done (or temporarily `UPDATE products SET offer_price=1, offer_price_inr=90, offer_until='2099-01-01T00:00:00+00:00' WHERE id=<some product>` by hand for now) and confirm the product's catalog page shows the strikethrough price and checkout charges the discounted amount.
2. Skip this step now if Task 4 isn't done yet — come back to it after Task 4's Step 5.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/catalog.py app/handlers/payments.py app/handlers/topup.py
git commit -m "feat: apply effective (discounted) price at checkout and catalog"
```

---

### Task 3: Expiry background job

**Files:**
- Modify: `app/handlers/announcements.py`
- Modify: `app/main.py`

- [ ] **Step 1: Add `expire_offers` to `announcements.py`**

Add near the top of `app/handlers/announcements.py`, after the imports (this needs `ContextTypes` for the job-queue callback signature — check the existing `crypto_watch.poll_bsc` signature in `app/services/crypto_watch.py` for the exact param name/type used by this codebase's other `run_repeating` jobs and match it):

```python
async def expire_offers(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: revert any product whose discount window has passed.
    No broadcast on expiry — only the start of a sale is announced."""
    cleared = await db.clear_expired_offers()
    if cleared:
        logger.info("Expired %d discount(s): %s", len(cleared), [r["id"] for r in cleared])
```

- [ ] **Step 2: Wire the job in `main.py`**

In `app/main.py`, after the `config.wallet_topup_enabled()` block (around line 404, right before the "Once-daily ops summary DM" comment), add:

```python
    # Discount expiry — reverts any product past its offer_until window.
    # Unconditional (unlike the wallet-watcher jobs above): discounts don't
    # depend on any payment rail being configured.
    app.job_queue.run_repeating(announcements.expire_offers, interval=60, first=30)
```

- [ ] **Step 3: Manual check**

Run: `python bot.py` (scratch DB), set a product's `offer_until` to a few seconds in the future via the DB (or Task 4's wizard once built), watch the log for the "Expired N discount(s)" line within ~90 seconds of it passing.

- [ ] **Step 4: Commit**

```bash
git add app/handlers/announcements.py app/main.py
git commit -m "feat: expire timed discounts on a 60s background job"
```

---

### Task 4: Admin discount wizard + manage-screen status line

**Files:**
- Modify: `app/states.py`
- Modify: `app/keyboards.py`
- Modify: `app/handlers/products_admin.py`
- Modify: `app/handlers/announcements.py`
- Modify: `app/main.py`

- [ ] **Step 1: Add conversation states**

In `app/states.py`, add after `BUY_QTY = 33`:

```python
DISCOUNT_PRICE, DISCOUNT_PRICE_INR, DISCOUNT_DURATION = range(34, 37)
```

- [ ] **Step 2: Add the `"sale"` announcement text kind**

In `app/handlers/announcements.py`, add a new function near `_price_change_text` (after it):

```python
def _sale_text(name: str, old_usdt, old_inr, offer_usdt, offer_inr, duration_label: str) -> str:
    """Flash-sale announcement: struck-through old price -> discounted price,
    with the human-readable duration (e.g. '24h', '3d'). `name` must already
    be HTML-safe — pass render_name(p), not a raw string."""
    return (
        f"{cemoji('sale', '🔥')} <b>Flash Sale!</b> {cemoji('sale', '🔥')}\n\n"
        f"<b>{name}</b>\n"
        f"{cemoji('money', '💰')} Was: <s>{esc(price_both(old_usdt, old_inr))}</s>\n"
        f"{cemoji('new', '🎉')} Now: <b>{esc(price_both(offer_usdt, offer_inr))}</b>\n\n"
        f"{cemoji('bell', '⏰')} Ends in <b>{esc(duration_label)}</b> — grab it before it's gone!\n\n"
        f"{cemoji('cart', '🛍')} Tap Browse products to buy now."
    )
```

- [ ] **Step 3: Add the manage-screen discount status line**

In `app/handlers/products_admin.py`, modify `product_manage_text` (around line 120):

```python
async def product_manage_text(p) -> str:
    stock = "∞ (unlimited)" if p["stock"] == db.UNLIMITED_STOCK else p["stock"]
    state = f"{cemoji('check', '✅')} Active" if p["active"] else f"{cemoji('block', '🚫')} Inactive"
    desc = render_desc(p) if p["description"] else "—"
    offer_usdt, offer_inr, is_discounted = db.effective_price(p)
    discount_line = ""
    if is_discounted:
        until_local = datetime.fromisoformat(p["offer_until"]).astimezone(IST).strftime("%d %b %H:%M IST")
        discount_line = (
            f"{cemoji('sale', '🔥')} Discount: <b>{esc(price_both(offer_usdt, offer_inr))}</b> "
            f"until {until_local}\n"
        )
    return (
        f"<b>Manage #{p['id']} — {render_name_with_icon(p)}</b>\n\n"
        f"{cemoji('pencil', '📝')} Description: {desc}\n"
        f"{cemoji('money', '💰')} Price: <b>{esc(price_both(p['price'], p['price_inr']))}</b>\n"
        f"{discount_line}"
        f"{cemoji('restock', '📦')} Stock: <b>{stock}</b>\n"
        f"{cemoji('key', '🔑')} Delivery codes: <b>{await db.count_unused_keys(p['id'])}</b> unused\n"
        f"Status: {state}"
    )
```

This needs `datetime` and `IST` imported. At the top of `app/handlers/products_admin.py`, change:

```python
from app.formatting import (
    cemoji, esc, money, price_both, render_desc, render_name, render_name_with_icon, truncate_safe, usdt,
)
```

to:

```python
from datetime import datetime

from app.formatting import (
    IST, cemoji, esc, money, price_both, render_desc, render_name, render_name_with_icon, truncate_safe, usdt,
)
```

- [ ] **Step 4: Add the Discount / End discount button to `manage_keyboard`**

In `app/keyboards.py`, modify `manage_keyboard` (around line 276). It needs `db.effective_price` — check the top of `app/keyboards.py` for its existing `from app import db` import (it already imports `db` for `_stock_suffix`'s key-pool counts, confirm the exact import line before editing) and reuse it.

```python
def manage_keyboard(p) -> InlineKeyboardMarkup:
    toggle_text = "❌ Deactivate" if p["active"] else "✅ Activate"
    toggle_key = "deactivate" if p["active"] else "activate"
    pid = p["id"]
    _, _, is_discounted = db.effective_price(p)
    discount_btn = (
        _btn("❌ End discount", "cancel", callback_data=f"enddiscount:{pid}") if is_discounted
        else _btn("🏷️ Discount", "sale", callback_data=f"discount:{pid}")
    )
    rows = [
        [_btn("💰 Price (USDT + INR)", "price", callback_data=f"edit:price_both:{pid}")],
        [
            _btn("📦 Stock", "stock", callback_data=f"edit:stock:{pid}"),
            _btn("🔤 Name", "name", callback_data=f"edit:name:{pid}"),
        ],
        [_btn("📝 Description", "description", callback_data=f"edit:description:{pid}")],
        [_btn("🏷 Icon", "icon", callback_data=f"edit:icon:{pid}")],
        [_btn("🔑 Delivery codes", "keys", callback_data=f"keys:{pid}")],
        [discount_btn],
        [_btn(toggle_text, toggle_key, callback_data=f"toggle:{pid}")],
        [_btn("🗑 Delete", "delete", callback_data=f"del:{pid}")],
        [_btn("⬅️ Products", "products", callback_data="menu:products")],
    ]
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 5: Write the discount wizard handlers**

In `app/handlers/products_admin.py`, add after `keys_submit` (end of file):

```python
# --------------------------------------------------------------------------- #
# Admin: timed discount wizard
# --------------------------------------------------------------------------- #
_DURATION_RE = re.compile(r"^(\d+)([hdm])$")
_DURATION_UNITS = {"h": "hours", "d": "days", "m": "minutes"}


def _parse_duration(raw: str) -> "timedelta | None":
    m = _DURATION_RE.match(raw.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        return None
    return timedelta(**{_DURATION_UNITS[unit]: n})


async def discount_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    pid = int(query.data.split(":")[1])
    p = await db.get_product(pid)
    if not p:
        await _edit_or_replace(query, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb("menu:products"))
        return ConversationHandler.END
    context.user_data["discount_pid"] = pid
    await context.bot.send_message(
        update.effective_chat.id,
        f"{cemoji('sale', '🏷️')} <b>Discount for {render_name(p)}</b>\n"
        f"Current price: <code>{esc(price_both(p['price'], p['price_inr']))}</code>\n\n"
        "Step 1/3 — Send the discounted <b>price in USDT</b> "
        "(must be lower than the current USDT price).",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return DISCOUNT_PRICE


async def discount_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid = context.user_data.get("discount_pid")
    if pid is None:
        return ConversationHandler.END
    p = await db.get_product(pid)
    raw = update.message.text.strip()
    try:
        usdt_val = float(raw.replace(",", ""))
        if usdt_val <= 0 or usdt_val >= float(p["price"]):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Send a number greater than 0 and less than the current "
            f"price ({esc(usdt(p['price']))}).",
            parse_mode=ParseMode.HTML,
        )
        return DISCOUNT_PRICE
    context.user_data["discount_usdt"] = usdt_val
    await update.message.reply_text(
        "Step 2/3 — Send the discounted <b>price in INR</b> (number, or <code>0</code> for none).",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return DISCOUNT_PRICE_INR


async def discount_price_inr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid = context.user_data.get("discount_pid")
    if pid is None:
        return ConversationHandler.END
    raw = update.message.text.strip()
    try:
        inr_val = float(raw.replace(",", ""))
        if inr_val < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Send a valid INR number (0 for none), e.g. 599",
            parse_mode=ParseMode.HTML,
        )
        return DISCOUNT_PRICE_INR
    context.user_data["discount_inr"] = inr_val
    await update.message.reply_text(
        "Step 3/3 — Send how long the discount should run: "
        "e.g. <code>24h</code>, <code>3d</code>, or <code>30m</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return DISCOUNT_DURATION


async def discount_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid = context.user_data.get("discount_pid")
    if pid is None:
        return ConversationHandler.END
    raw = update.message.text.strip()
    delta = _parse_duration(raw)
    if delta is None:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Send a duration like <code>24h</code>, <code>3d</code>, or <code>30m</code>.",
            parse_mode=ParseMode.HTML,
        )
        return DISCOUNT_DURATION

    p = await db.get_product(pid)
    offer_usdt = context.user_data.pop("discount_usdt")
    offer_inr = context.user_data.pop("discount_inr")
    context.user_data.pop("discount_pid", None)
    offer_until = (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")
    await db.set_product_offer(pid, offer_usdt, offer_inr, offer_until)

    p = await db.get_product(pid)
    await update.message.reply_text(
        f"{cemoji('check', '✅')} Discount set for <b>{raw}</b>.", parse_mode=ParseMode.HTML
    )
    await context.bot.send_message(
        update.effective_chat.id, await product_manage_text(p),
        parse_mode=ParseMode.HTML, reply_markup=manage_keyboard(p),
    )
    if p["active"]:
        text = _sale_text(render_name(p), p["price"], p["price_inr"], offer_usdt, offer_inr, raw)
        await _offer_announcement(context, update.effective_chat.id, "Flash sale", text, pid)
    return ConversationHandler.END


async def enddiscount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    await query.answer()
    pid = int(query.data.split(":")[1])
    await db.clear_offer(pid)
    p = await db.get_product(pid)
    if not p:
        await _edit_or_replace(query, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb("menu:products"))
        return
    await _edit_or_replace(query, await product_manage_text(p), manage_keyboard(p))
```

Add the needed imports at the top of `app/handlers/products_admin.py`:

```python
import re
from datetime import datetime, timedelta, timezone
```

(merge with the `from datetime import datetime` added in Step 3 above — end result is one `from datetime import datetime, timedelta, timezone` line)

And add `_sale_text` to the existing `from app.handlers.announcements import (...)` block, and `DISCOUNT_DURATION, DISCOUNT_PRICE, DISCOUNT_PRICE_INR` to the existing `from app.states import (...)` block (alphabetically).

- [ ] **Step 6: Wire the new conversation + callback in `app/main.py`**

Add to the `from app.states import (...)` block in `app/main.py`: `DISCOUNT_DURATION, DISCOUNT_PRICE, DISCOUNT_PRICE_INR`.

Add a new `ConversationHandler` right after `keys_conv` (after line 271):

```python
    discount_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(products_admin.discount_start, pattern=r"^discount:\d+$")],
        states={
            DISCOUNT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.discount_price)],
            DISCOUNT_PRICE_INR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.discount_price_inr)],
            DISCOUNT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.discount_duration)],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )
```

Register it next to `app.add_handler(keys_conv)` (after line 349):

```python
    app.add_handler(discount_conv)
```

Register the plain (non-conversation) `enddiscount` callback next to the other plain product callbacks (after line 377, `app.add_handler(CallbackQueryHandler(products_admin.delete_do, pattern=r"^delok:\d+$"))`):

```python
    app.add_handler(CallbackQueryHandler(products_admin.enddiscount, pattern=r"^enddiscount:\d+$"))
```

- [ ] **Step 7: Add a `sale` custom-emoji key (optional cosmetic, skip if `CUSTOM_EMOJI_IDS` isn't being touched right now)**

`cemoji('sale', '🔥')` used above already degrades cleanly to the plain 🔥 fallback per `cemoji`'s existing behavior (`app/formatting.py:152`) when `"sale"` isn't in `CUSTOM_EMOJI_IDS` — no config change required for this to work.

- [ ] **Step 8: Manual end-to-end check**

Run: `python bot.py` (scratch DB). As admin:
1. Open a product's Manage screen, tap 🏷️ Discount, walk through USDT price → INR price → `2m` duration.
2. Confirm the manage screen now shows the `🔥 Discount: ... until ...` line and the button switched to `❌ End discount`.
3. Confirm the Approve/Deny flash-sale card arrives; tap Approve, confirm the broadcast lands (in a test chat / your own DM if you're in `all_recipient_ids()`).
4. As a buyer, open the product in Catalog — confirm the struck-through price + discounted price show, and that checkout charges the discounted amount (wallet purchase debits the discounted USDT amount).
5. Wait ~2-3 minutes past the discount's end, confirm the background job (Task 3) reverts it — manage screen no longer shows the discount line, catalog shows the normal price again.
6. Tap 🏷️ Discount again, complete it, then immediately tap ❌ End discount — confirm it clears without sending any broadcast.

- [ ] **Step 9: Commit**

```bash
git add app/states.py app/keyboards.py app/handlers/products_admin.py app/handlers/announcements.py app/main.py
git commit -m "feat: add admin timed-discount wizard with flash-sale broadcast"
```

---

## Post-plan cleanup

- [ ] Go back to Task 2 Step 4 and re-run the manual catalog/checkout discount check now that Task 4's wizard exists, instead of hand-editing the DB.
