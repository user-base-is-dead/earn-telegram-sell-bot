# Stock Management (Bot Stock Tab + Web Stock Tab) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the admin a dedicated Stock view on both the Telegram bot and the web admin panel — sorted low-stock-first, with a type-aware quick action per product and multi-select bulk actions (deactivate / reactivate / clear unused delivery codes) — instead of drilling into each product's manage screen one at a time.

**Architecture:** Two independent, mirrored surfaces built on top of one shared concept both codebases already have (`effective_stock` — a keyed product's real stock is its unused delivery-code count, not the manually-typed field). Bot: a new top-level `📦 Stock` menu entry backed entirely by plain `CallbackQueryHandler`s (no new conversation states) in `app/handlers/products_admin.py`, reusing the existing `keys:<id>` and `edit:stock:<id>` conversation entry points for single-product actions. Web: a new "Stock" tab inside the existing `/products` page (`admin/src/app/(admin)/products/page.tsx`), reusing the existing `ProductFormDialog` for single-product actions.

**Tech Stack:** Python 3.11 / python-telegram-bot 21 / asyncpg (bot); Next.js (App Router) / Supabase JS / shadcn-style components (web).

**Spec:** `docs/superpowers/specs/2026-07-18-stock-management-design.md`

---

## Task 1: DB — `clear_unused_keys` + self-check

**Files:**
- Modify: `app/db/products.py`
- Modify: `app/db/__init__.py`

- [ ] **Step 1: Add the function**

In `app/db/products.py`, add directly below `get_key_pool_counts` (after line 164, before `def effective_stock`):

```python
async def clear_unused_keys(product_id: int) -> int:
    """Delete every unused delivery code for a product. Returns count deleted.
    Used codes (used=1) are never touched — they're the audit trail for past
    orders, same reasoning as delete_product's own key cleanup."""
    async with _connect() as conn:
        result = await conn.execute(
            "DELETE FROM product_keys WHERE product_id = $1 AND used = 0", product_id
        )
        # asyncpg execute() returns a command tag string like "DELETE 3".
        return int(result.split()[-1])
```

- [ ] **Step 2: Add the self-check**

In `app/db/products.py`, add a new function right after `_demo_effective_stock` (after its closing `print(...)` line, before `_demo_effective_price`):

```python
async def _demo_clear_unused_keys() -> None:
    """Self-check: clear_unused_keys deletes only unused codes and leaves used
    ones (the audit trail) untouched. Writes real rows — must be run against a
    disposable/test DATABASE_URL, never production."""
    from app import db as _db

    await _db.init_pool()
    try:
        pid = await add_product("selftest-clear-keys", "", 1.0, "content", stock=0)
        await add_product_keys(pid, ["CODE-A", "CODE-B", "CODE-C"])
        await pop_unused_keys(pid, order_id=0, n=1)  # CODE-A becomes used
        deleted = await clear_unused_keys(pid)
        assert deleted == 2, f"expected 2 unused codes deleted, got {deleted}"
        assert await count_unused_keys(pid) == 0, "no unused codes should remain"

        async with _connect() as conn:
            remaining = await conn.fetch(
                "SELECT code FROM product_keys WHERE product_id = $1", pid
            )
        assert {r["code"] for r in remaining} == {"CODE-A"}, (
            f"the used code must survive clear_unused_keys, got {[r['code'] for r in remaining]}"
        )

        empty_deleted = await clear_unused_keys(pid)
        assert empty_deleted == 0, "clearing again with nothing unused must return 0, not error"

        async with _connect() as conn:
            await conn.execute("DELETE FROM product_keys WHERE product_id = $1", pid)
        await delete_product(pid)
    finally:
        await _db.close_pool()

    print("clear-unused-keys self-check: all assertions passed")
```

- [ ] **Step 3: Wire it into the `__main__` block**

In `app/db/products.py`, find:

```python
if __name__ == "__main__":
    import asyncio

    from app import config

    config.require_disposable_db_for_selfcheck()
    asyncio.run(_demo())
    asyncio.run(_demo_effective_stock())
    asyncio.run(_demo_effective_price())
```

Change the last three lines to:

```python
    config.require_disposable_db_for_selfcheck()
    asyncio.run(_demo())
    asyncio.run(_demo_effective_stock())
    asyncio.run(_demo_clear_unused_keys())
    asyncio.run(_demo_effective_price())
```

- [ ] **Step 4: Export it from the facade**

In `app/db/__init__.py`, add `clear_unused_keys` to the `from app.db.products import (...)` block (alphabetically, right after `clear_offer`):

```python
from app.db.products import (
    EDITABLE_FIELDS,
    add_product,
    add_product_keys,
    clear_expired_offers,
    clear_offer,
    clear_unused_keys,
    count_unused_keys,
    ...
```

And add `"clear_unused_keys"` to `__all__` in the same file, next to `"clear_offer"`:

```python
    "clear_offer", "clear_unused_keys", "count_unused_keys",
```

- [ ] **Step 5: Run the self-check**

Point `DATABASE_URL` at a disposable/scratch database (never production), then:

```bash
ALLOW_SELFCHECK_DB=1 python -m app.db.products
```

Expected output ends with:
```
products self-check: all assertions passed
effective-stock self-check: all assertions passed
clear-unused-keys self-check: all assertions passed
effective-price self-check: all assertions passed
```

- [ ] **Step 6: Commit**

```bash
git add app/db/products.py app/db/__init__.py
git commit -m "feat: add clear_unused_keys for the new Stock tab's clear-codes action"
```

---

## Task 2: Bot — `📦 Stock` menu entry

**Files:**
- Modify: `app/keyboards.py`

- [ ] **Step 1: Add the menu button**

In `app/keyboards.py`, inside `main_menu_keyboard`, find:

```python
    if config.is_admin(user_id):
        rows += [
            [_btn("➕ Add product", "add", callback_data="menu:add")],
            [
                _btn("📦 Products", "products", callback_data="menu:products"),
                _btn("🧾 All orders", "all_orders", callback_data="menu:orders"),
            ],
```

Change to:

```python
    if config.is_admin(user_id):
        rows += [
            [_btn("➕ Add product", "add", callback_data="menu:add")],
            [
                _btn("📦 Products", "products", callback_data="menu:products"),
                _btn("📊 Stock", "restock", callback_data="menu:stock"),
            ],
            [
                _btn("🧾 All orders", "all_orders", callback_data="menu:orders"),
            ],
```

(Reuses the existing `restock` `CUSTOM_EMOJI_IDS` key so an admin who's already configured a custom stock/restock icon gets it here too, no new key needed.)

- [ ] **Step 2: Verify it compiles**

```bash
python -m py_compile app/keyboards.py
```

Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add app/keyboards.py
git commit -m "feat: add Stock button to the admin menu"
```

---

## Task 3: Bot — Stock list + type-aware quick action sheet

**Files:**
- Modify: `app/handlers/products_admin.py`

- [ ] **Step 1: Import `LOW_STOCK_THRESHOLD`**

In `app/handlers/products_admin.py`, find:

```python
from app.keyboards import (
    CATALOG_NAME_MAX, _btn, _is_out_of_stock, _name_sort_key, _product_icon_btn, _stock_suffix, cancel_kb,
    manage_keyboard, refresh_menu_kb, wizard_nav_kb,
)
```

Change to:

```python
from app.keyboards import (
    CATALOG_NAME_MAX, LOW_STOCK_THRESHOLD, _btn, _is_out_of_stock, _name_sort_key, _product_icon_btn,
    _stock_suffix, cancel_kb, manage_keyboard, refresh_menu_kb, wizard_nav_kb,
)
```

- [ ] **Step 2: Add the Stock list handler**

Add this right after `admin_products` (after its closing line, which is `)` for the `await _render(...)` call, before the `# --------------------------------------------------------------------------- #` / "Admin: manage / edit / delete a product" section):

```python
async def stock_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The 📦 Stock tab: every product sorted out-of-stock/low-stock first,
    tap one for a type-aware quick action, or ☑️ Select for bulk actions."""
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        if query:
            await query.answer("🚫 Not authorized.", show_alert=True)
        return
    context.user_data.pop("stock_selected", None)

    all_products = await db.list_products(only_active=False)
    if not all_products:
        await _render(
            update,
            f"No products yet. Tap {cemoji('plus', '➕')} <b>Add product</b> to create one.",
            refresh_menu_kb("menu:products"),
        )
        return

    key_counts = await db.get_key_pool_counts()

    def _sort_key(p):
        stock = db.effective_stock(p["stock"], key_counts.get(p["id"]))
        if not p["active"]:
            rank = 2
        elif _is_out_of_stock(stock):
            rank = 0
        elif stock != db.UNLIMITED_STOCK and stock <= LOW_STOCK_THRESHOLD:
            rank = 1
        else:
            rank = 1.5
        return (rank, _name_sort_key(p))

    all_products = sorted(all_products, key=_sort_key)

    page = 0
    if query and ":" in query.data:
        try:
            page = int(query.data.split(":")[-1])
        except (ValueError, IndexError):
            page = 0

    page_size = 15
    total = len(all_products)
    start = page * page_size
    products = all_products[start:start + page_size]

    rows = []
    for p in products:
        stock = db.effective_stock(p["stock"], key_counts.get(p["id"]))
        out_of_stock = p["active"] and _is_out_of_stock(stock)
        icon_char = "❌" if out_of_stock else (p["icon_char"] if "icon_char" in p.keys() else None) or "🏷"
        name = truncate_safe((p["name"] or "").strip(), CATALOG_NAME_MAX)
        if not p["active"]:
            suffix = " · deactivated"
        elif stock == db.UNLIMITED_STOCK:
            suffix = " · ∞"
        else:
            suffix = _stock_suffix(stock)
        label = f"{icon_char} {name}{suffix}"
        rows.append([_product_icon_btn(label, p, f"stock:row:{p['id']}:{page}", out_of_stock)])

    nav = []
    if page > 0:
        nav.append(_btn("⬅️ Prev", "prev", callback_data=f"menu:stock:{page - 1}"))
    if start + page_size < total:
        nav.append(_btn("Next ➡️", "next", callback_data=f"menu:stock:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        _btn("☑️ Select", "check", callback_data=f"stock:sel:{page}"),
        _btn("🔄 Refresh", "refresh", callback_data=f"menu:stock:{page}"),
    ])
    rows.append([_btn("⬅️ Menu", "menu", callback_data="menu:home")])

    await _render(
        update,
        f"{cemoji('products', '📦')} <b>Stock</b>\n"
        "Sorted low/out-of-stock first. Tap a product for quick actions, or ☑️ Select to bulk-act.",
        InlineKeyboardMarkup(rows),
    )


async def stock_row_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tapped a product in the Stock list (browse mode) — type-aware quick actions."""
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    _, _, pid_s, page_s = query.data.split(":")
    pid, page = int(pid_s), int(page_s)
    p = await db.get_product(pid)
    if not p:
        await _render(update, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb(f"menu:stock:{page}"))
        return

    key_counts = await db.get_key_pool_count(pid)
    is_keyed = key_counts is not None
    stock = db.effective_stock(p["stock"], key_counts)
    stock_txt = "∞ (unlimited)" if stock == db.UNLIMITED_STOCK else str(stock)
    state = f"{cemoji('check', '✅')} Active" if p["active"] else f"{cemoji('block', '🚫')} Inactive"
    text = (
        f"<b>{render_name_with_icon(p)}</b>\n"
        f"{cemoji('restock', '📦')} Stock: <b>{stock_txt}</b> ({'keyed' if is_keyed else 'manual'})\n"
        f"Status: {state}"
    )

    rows = []
    if is_keyed:
        rows.append([_btn("➕ Add codes", "keys", callback_data=f"keys:{pid}")])
        rows.append([_btn("🗑 Clear unused codes", "delete", callback_data=f"stock:clear:{pid}:{page}")])
    else:
        rows.append([_btn("✏️ Edit stock number", "stock", callback_data=f"edit:stock:{pid}")])
    rows.append([_btn("📄 Full product page", "products", callback_data=f"manage:{pid}")])
    rows.append([_btn("⬅️ Back to Stock", "back", callback_data=f"menu:stock:{page}")])

    await _render(update, text, InlineKeyboardMarkup(rows))


async def stock_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    _, _, pid_s, page_s = query.data.split(":")
    pid, page = int(pid_s), int(page_s)
    p = await db.get_product(pid)
    if not p:
        await _render(update, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb(f"menu:stock:{page}"))
        return
    n = await db.count_unused_keys(pid)
    kb = InlineKeyboardMarkup(
        [
            [_btn(f"🗑 Yes, clear {n}", callback_data=f"stock:clearok:{pid}:{page}")],
            [_btn("⬅️ No, keep them", callback_data=f"stock:row:{pid}:{page}")],
        ]
    )
    await _render(
        update,
        f"{cemoji('warn', '⚠️')} Clear <b>{n}</b> unused code(s) for <b>{render_name(p)}</b>? "
        "<i>This can't be undone.</i>",
        kb,
    )


async def stock_clear_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    _, _, pid_s, page_s = query.data.split(":")
    pid, page = int(pid_s), int(page_s)
    n = await db.clear_unused_keys(pid)
    p = await db.get_product(pid)
    name = render_name(p) if p else str(pid)
    await _render(
        update,
        f"{cemoji('check', '✅')} Cleared <b>{n}</b> unused code(s) for <b>{name}</b>.",
        InlineKeyboardMarkup([[_btn("⬅️ Back to Stock", callback_data=f"menu:stock:{page}")]]),
    )
```

- [ ] **Step 3: Verify it compiles**

```bash
python -m py_compile app/handlers/products_admin.py
```

Expected: no output (success).

- [ ] **Step 4: Commit**

```bash
git add app/handlers/products_admin.py
git commit -m "feat: add Stock list + quick action sheet handlers"
```

(Handlers aren't reachable yet — Task 5 wires them into `main.py`. Compiling is the only mechanical check available until then; Task 5's final step is the real manual verification pass.)

---

## Task 4: Bot — select mode + bulk actions

**Files:**
- Modify: `app/handlers/products_admin.py`

- [ ] **Step 1: Add the select-mode + bulk-action handlers**

Add this immediately after `stock_clear_do` (from Task 3):

```python
async def _render_stock_select(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Shared renderer for select mode — used by entering select mode, toggling
    a row, and returning from a bulk confirm step."""
    all_products = sorted(await db.list_products(only_active=False), key=_name_sort_key)
    key_counts = await db.get_key_pool_counts()
    page_size = 15
    total = len(all_products)
    start = page * page_size
    products = all_products[start:start + page_size]
    selected = context.user_data.get("stock_selected", set())

    rows = []
    for p in products:
        stock = db.effective_stock(p["stock"], key_counts.get(p["id"]))
        mark = "☑️" if p["id"] in selected else "⬜"
        name = truncate_safe((p["name"] or "").strip(), CATALOG_NAME_MAX)
        stock_txt = "∞" if stock == db.UNLIMITED_STOCK else str(stock)
        status = "deactivated" if not p["active"] else stock_txt
        label = f"{mark} {name} · {status}"
        rows.append([_btn(label, callback_data=f"stock:tog:{p['id']}:{page}")])

    nav = []
    if page > 0:
        nav.append(_btn("⬅️ Prev", "prev", callback_data=f"stock:sel:{page - 1}"))
    if start + page_size < total:
        nav.append(_btn("Next ➡️", "next", callback_data=f"stock:sel:{page + 1}"))
    if nav:
        rows.append(nav)

    n = len(selected)
    if n:
        rows.append([_btn(f"🚫 Deactivate ({n})", callback_data=f"stock:bulk:deactivate:{page}")])
        rows.append([
            _btn(f"♻️ Reactivate ({n})", callback_data=f"stock:bulk:reactivate:{page}"),
            _btn(f"🗑 Clear codes ({n})", callback_data=f"stock:bulk:clearcodes:{page}"),
        ])
    rows.append([_btn("✖️ Cancel select", "cancel_x", callback_data=f"stock:cancelsel:{page}")])

    await _render(
        update,
        f"{cemoji('products', '📦')} <b>Stock — select products</b>\n"
        f"Tap to toggle. <b>{n}</b> selected.",
        InlineKeyboardMarkup(rows),
    )


async def stock_select_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    page = int(query.data.split(":")[-1])
    context.user_data["stock_selected"] = set()
    await _render_stock_select(update, context, page)


async def stock_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    _, _, pid_s, page_s = query.data.split(":")
    pid, page = int(pid_s), int(page_s)
    selected = context.user_data.setdefault("stock_selected", set())
    if pid in selected:
        selected.discard(pid)
    else:
        selected.add(pid)
    await _render_stock_select(update, context, page)


async def stock_cancel_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    context.user_data.pop("stock_selected", None)
    await stock_list(update, context)


BULK_ACTION_LABELS = {
    "deactivate": "Deactivate",
    "reactivate": "Reactivate",
    "clearcodes": "Clear codes for",
}


async def stock_bulk_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    _, _, action, page_s = query.data.split(":")
    page = int(page_s)
    selected = context.user_data.get("stock_selected", set())
    if not selected:
        await query.answer("Nothing selected.", show_alert=True)
        return
    n = len(selected)
    kb = InlineKeyboardMarkup(
        [
            [_btn(f"✅ Yes, {BULK_ACTION_LABELS[action]} {n}", callback_data=f"stock:bulkok:{action}:{page}")],
            [_btn("⬅️ No, go back", callback_data=f"stock:sel:{page}")],
        ]
    )
    await _render(
        update, f"{cemoji('warn', '⚠️')} {BULK_ACTION_LABELS[action]} <b>{n}</b> selected product(s)?", kb
    )


async def stock_bulk_apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    _, _, action, page_s = query.data.split(":")
    page = int(page_s)
    selected = context.user_data.pop("stock_selected", set())
    applied = 0
    for pid in selected:
        p = await db.get_product(pid)
        if not p:
            continue
        if action == "deactivate" and p["active"]:
            await db.set_product_active(pid, False)
            applied += 1
        elif action == "reactivate" and not p["active"]:
            await db.set_product_active(pid, True)
            applied += 1
        elif action == "clearcodes" and await db.count_unused_keys(pid) > 0:
            await db.clear_unused_keys(pid)
            applied += 1

    await _render(
        update,
        f"{cemoji('check', '✅')} Applied to <b>{applied}</b> of <b>{len(selected)}</b> selected product(s).",
        InlineKeyboardMarkup([[_btn("⬅️ Back to Stock", callback_data=f"menu:stock:{page}")]]),
    )
```

- [ ] **Step 2: Verify it compiles**

```bash
python -m py_compile app/handlers/products_admin.py
```

Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add app/handlers/products_admin.py
git commit -m "feat: add Stock select mode + bulk deactivate/reactivate/clear-codes"
```

---

## Task 5: Bot — wire everything into `main.py` + manual verification

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Register the new handlers**

In `app/main.py`, find this block (the plain, non-conversation product-admin handlers):

```python
    # Admin: manage products
    app.add_handler(CallbackQueryHandler(products_admin.manage_product, pattern=r"^manage:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.toggle_active, pattern=r"^toggle:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.delete_confirm, pattern=r"^del:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.delete_do, pattern=r"^delok:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.enddiscount, pattern=r"^enddiscount:\d+$"))
```

Change to:

```python
    # Admin: manage products
    app.add_handler(CallbackQueryHandler(products_admin.manage_product, pattern=r"^manage:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.toggle_active, pattern=r"^toggle:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.delete_confirm, pattern=r"^del:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.delete_do, pattern=r"^delok:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.enddiscount, pattern=r"^enddiscount:\d+$"))

    # Admin: Stock tab (browse, quick action sheet, select mode, bulk actions)
    app.add_handler(CallbackQueryHandler(products_admin.stock_list, pattern=r"^menu:stock(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_row_tap, pattern=r"^stock:row:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_clear_confirm, pattern=r"^stock:clear:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_clear_do, pattern=r"^stock:clearok:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_select_mode, pattern=r"^stock:sel:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_toggle, pattern=r"^stock:tog:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_cancel_select, pattern=r"^stock:cancelsel:\d+$"))
    app.add_handler(
        CallbackQueryHandler(products_admin.stock_bulk_confirm, pattern=r"^stock:bulk:(deactivate|reactivate|clearcodes):\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(products_admin.stock_bulk_apply, pattern=r"^stock:bulkok:(deactivate|reactivate|clearcodes):\d+$")
    )
```

- [ ] **Step 2: Verify it compiles**

```bash
python -m py_compile app/main.py
```

Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: wire the Stock tab's callback handlers into the bot"
```

- [ ] **Step 4: Manual verification pass**

Point `.env`'s `DATABASE_URL` at your real (or a staging) DB, run `python bot.py`, then in Telegram as an admin:

1. Main menu → **📊 Stock**. Confirm the list appears, sorted with any out-of-stock/low-stock products first, deactivated products last, each row showing the right icon (❌ if out of stock) and suffix.
2. Tap a **keyed** product (one with delivery codes) → confirm you see "➕ Add codes" and "🗑 Clear unused codes". Tap "➕ Add codes", paste a code, confirm it saves and the restock announcement offer still appears as before.
3. Tap a **manual** (no delivery codes) product → confirm you see "✏️ Edit stock number" only, and it opens the existing stock-edit flow.
4. From the quick action sheet, tap "🗑 Clear unused codes" on a keyed product with unused codes → confirm the count in the confirmation prompt matches, confirm, and verify the product now shows 0/Sold out back in the Stock list.
5. From the Stock list, tap **☑️ Select** → toggle 2-3 products on/off, confirm the ☑️/⬜ marks update and the bulk action row only appears once ≥1 is selected.
6. With a mixed selection (some active, some inactive; some keyed, some manual), tap **🚫 Deactivate** → confirm the "Deactivate N?" prompt → confirm → verify the "Applied to X of N" count only counts the ones that were actually active before, and that they're now deactivated.
7. Repeat step 6 for **♻️ Reactivate** and **🗑 Clear codes**, confirming each only applies to the products it's actually relevant to.
8. Tap **✖️ Cancel select** mid-selection → confirm it returns to browse mode with the selection cleared (re-entering Select mode shows nothing checked).

---

## Task 6: Web — `clearProductKeys` server action

**Files:**
- Modify: `admin/src/app/(admin)/products/actions.ts`

- [ ] **Step 1: Add the action**

In `admin/src/app/(admin)/products/actions.ts`, add this after `addProductKeys` (before `deleteProduct`):

```ts
export async function clearProductKeys(id: number): Promise<{ deleted: number }> {
  const db = supabaseServer();
  const { error, count } = await db
    .from("product_keys")
    .delete({ count: "exact" })
    .eq("product_id", id)
    .eq("used", 0);
  if (error) throw new Error(error.message);
  revalidatePath("/products");
  return { deleted: count ?? 0 };
}
```

- [ ] **Step 2: Type-check**

```bash
cd admin && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/\(admin\)/products/actions.ts
git commit -m "feat: add clearProductKeys server action for the web Stock tab"
```

---

## Task 7: Web — `StockTable` component

**Files:**
- Create: `admin/src/components/stock-table.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";
import { useMemo, useState, useTransition } from "react";
import { toast } from "sonner";
import { AlertTriangle, KeyRound, PackageX } from "lucide-react";
import { KpiCard } from "@/components/kpi-card";
import { KpiRow } from "@/components/kpi-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ProductFormDialog } from "@/components/product-form-dialog";
import { clearProductKeys, setProductActive } from "@/app/(admin)/products/actions";

const LOW_STOCK_THRESHOLD = 5;

type StockRow = {
  id: number;
  name: string;
  stock: number;
  active: number;
  hasKeys: boolean;
  unusedKeys: number;
  [key: string]: unknown;
};

type BulkAction = "deactivate" | "reactivate" | "clearcodes";

const BULK_LABELS: Record<BulkAction, string> = {
  deactivate: "Deactivate",
  reactivate: "Reactivate",
  clearcodes: "Clear unused codes for",
};

function effectiveStockOf(r: StockRow): number {
  return r.hasKeys ? r.unusedKeys : r.stock;
}

function rankOf(r: StockRow): number {
  const stock = effectiveStockOf(r);
  if (r.active === 0) return 2;
  if (stock === 0) return 0;
  if (stock !== -1 && stock <= LOW_STOCK_THRESHOLD) return 1;
  return 1.5;
}

export function StockTable({ products }: { products: StockRow[] }) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<StockRow | null>(null);
  const [confirmAction, setConfirmAction] = useState<BulkAction | null>(null);
  const [pending, startTransition] = useTransition();

  const rows = useMemo(
    () => [...products].sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name)),
    [products]
  );

  const outOfStockCount = rows.filter((r) => r.active === 1 && effectiveStockOf(r) === 0).length;
  const lowStockCount = rows.filter((r) => {
    const s = effectiveStockOf(r);
    return r.active === 1 && s !== -1 && s > 0 && s <= LOW_STOCK_THRESHOLD;
  }).length;
  const totalUnusedCodes = rows.filter((r) => r.hasKeys).reduce((sum, r) => sum + r.unusedKeys, 0);

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function applyBulk() {
    if (!confirmAction) return;
    const ids = [...selected];
    startTransition(async () => {
      try {
        let applied = 0;
        for (const id of ids) {
          const row = rows.find((r) => r.id === id);
          if (!row) continue;
          if (confirmAction === "deactivate" && row.active === 1) {
            await setProductActive(id, false);
            applied++;
          } else if (confirmAction === "reactivate" && row.active === 0) {
            await setProductActive(id, true);
            applied++;
          } else if (confirmAction === "clearcodes" && row.hasKeys && row.unusedKeys > 0) {
            await clearProductKeys(id);
            applied++;
          }
        }
        toast(`Applied to ${applied} of ${ids.length} selected product(s).`);
        setSelected(new Set());
        setConfirmAction(null);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Bulk action failed");
      }
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <KpiRow>
        <KpiCard
          label="Out of stock"
          value={String(outOfStockCount)}
          icon={<PackageX className="size-5 text-white" />}
          accent="coral"
        />
        <KpiCard
          label="Low stock"
          value={String(lowStockCount)}
          icon={<AlertTriangle className="size-5 text-white" />}
          accent="gold"
        />
        <KpiCard
          label="Total unused codes"
          value={String(totalUnusedCodes)}
          icon={<KeyRound className="size-5 text-white" />}
          accent="teal"
        />
      </KpiRow>

      {selected.size > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-[var(--brand-violet-to)]/40 bg-[var(--brand-violet-to)]/10 px-4 py-2">
          <span className="text-sm">{selected.size} selected</span>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" onClick={() => setConfirmAction("deactivate")}>
              Deactivate
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmAction("reactivate")}>
              Reactivate
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmAction("clearcodes")}>
              Clear codes
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Clear selection
            </Button>
          </div>
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead />
            <TableHead>Product</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Stock</TableHead>
            <TableHead>Status</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => {
            const stock = effectiveStockOf(r);
            const statusLabel =
              r.active === 0 ? "Inactive" : stock === 0 ? "Sold out" : stock !== -1 && stock <= LOW_STOCK_THRESHOLD ? "Low stock" : "In stock";
            const statusClass =
              r.active === 0
                ? "bg-[var(--ink-muted)]/15 text-[var(--ink-muted)]"
                : stock === 0
                  ? "bg-[var(--brand-coral-to)]/15 text-[var(--brand-coral-to)]"
                  : stock !== -1 && stock <= LOW_STOCK_THRESHOLD
                    ? "bg-[var(--brand-gold-to)]/15 text-[var(--brand-gold-to)]"
                    : "bg-[var(--brand-teal-to)]/15 text-[var(--brand-teal-to)]";
            return (
              <TableRow key={r.id}>
                <TableCell>
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--brand-violet-to)]"
                    checked={selected.has(r.id)}
                    onChange={() => toggle(r.id)}
                  />
                </TableCell>
                <TableCell className="font-medium text-[var(--ink)]">{r.name}</TableCell>
                <TableCell className="text-[var(--ink-muted)]">{r.hasKeys ? "Keyed" : "Manual"}</TableCell>
                <TableCell>{stock === -1 ? "∞" : stock}</TableCell>
                <TableCell>
                  <Badge className={statusClass}>{statusLabel}</Badge>
                </TableCell>
                <TableCell>
                  <button
                    className="text-xs text-[var(--brand-violet-to)] hover:underline"
                    onClick={() => setEditing(r)}
                  >
                    {r.hasKeys ? "Add codes →" : "Edit stock →"}
                  </button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      {editing && (
        <ProductFormDialog
          product={editing}
          hasKeys={editing.hasKeys}
          unusedKeys={editing.unusedKeys}
          onClose={() => setEditing(null)}
        />
      )}

      <Dialog open={!!confirmAction} onOpenChange={(open) => !pending && !open && setConfirmAction(null)}>
        <DialogContent className="w-full max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {confirmAction && `${BULK_LABELS[confirmAction]} ${selected.size} product(s)?`}
            </DialogTitle>
          </DialogHeader>
          {confirmAction === "clearcodes" && (
            <p className="text-sm text-[var(--ink-muted)]">This can&apos;t be undone.</p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" disabled={pending} onClick={() => setConfirmAction(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant={confirmAction === "reactivate" ? "default" : "destructive"}
              disabled={pending}
              onClick={applyBulk}
            >
              Confirm
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd admin && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/stock-table.tsx
git commit -m "feat: add StockTable component (KPI strip + bulk-select table)"
```

---

## Task 8: Web — wire the Stock tab into `/products` + manual verification

**Files:**
- Modify: `admin/src/app/(admin)/products/page.tsx`

- [ ] **Step 1: Add the tab toggle and StockTable**

Replace the full contents of `admin/src/app/(admin)/products/page.tsx` with:

```tsx
import { supabaseServer } from "@/lib/supabase-server";
import { ProductCard } from "@/components/product-card";
import { NewProductButton } from "@/components/new-product-button";
import { StockTable } from "@/components/stock-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export const dynamic = "force-dynamic";

export default async function ProductsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const db = supabaseServer();
  const { data: products } = await db.from("products").select("*").order("id");
  const { data: keyCounts } = await db.from("product_keys").select("product_id, used");

  const unusedByProduct = new Map<number, number>();
  const hasKeysByProduct = new Set<number>();
  for (const row of keyCounts ?? []) {
    hasKeysByProduct.add(row.product_id);
    if (row.used === 0) {
      unusedByProduct.set(row.product_id, (unusedByProduct.get(row.product_id) ?? 0) + 1);
    }
  }

  const needle = q?.toLowerCase();
  const filtered = (products ?? []).filter(
    (p) => !needle || p.name.toLowerCase().includes(needle)
  );

  const stockRows = (products ?? []).map((p) => ({
    ...p,
    hasKeys: hasKeysByProduct.has(p.id),
    unusedKeys: unusedByProduct.get(p.id) ?? 0,
  }));

  return (
    <div>
      <div className="flex justify-end">
        <NewProductButton />
      </div>
      <Tabs defaultValue="all" className="mt-6">
        <TabsList>
          <TabsTrigger value="all">All products</TabsTrigger>
          <TabsTrigger value="stock">Stock</TabsTrigger>
        </TabsList>
        <TabsContent value="all" className="mt-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                unusedKeys={unusedByProduct.get(p.id) ?? 0}
                hasKeys={hasKeysByProduct.has(p.id)}
              />
            ))}
            {filtered.length === 0 && (
              <p className="text-sm text-[var(--ink-muted)]">
                {q ? `No products match "${q}".` : "No products yet."}
              </p>
            )}
          </div>
        </TabsContent>
        <TabsContent value="stock" className="mt-6">
          <StockTable products={stockRows} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd admin && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add admin/src/app/\(admin\)/products/page.tsx
git commit -m "feat: add Stock tab to the web /products page"
```

- [ ] **Step 4: Manual verification pass**

```bash
cd admin && npm run dev
```

Open the app, go to **Products**, then:

1. Confirm two tabs: "All products" (unchanged card grid) and "Stock". Switch to "Stock".
2. Confirm the KPI strip shows correct **Out of stock**, **Low stock** (≤5), and **Total unused codes** counts against your actual data.
3. Confirm the table is sorted out-of-stock → low-stock → rest → deactivated last, with correct type ("Keyed"/"Manual"), stock number (or ∞), and status badge per row.
4. Click "Add codes →" on a keyed product (or "Edit stock →" on a manual one) — confirm it opens the existing `ProductFormDialog`, exactly like the "All products" tab's Edit button does.
5. Check 2-3 boxes (mixed active/inactive, keyed/manual) — confirm the bulk action bar appears with the right count.
6. Click **Deactivate** → confirm the dialog names the count → Confirm → verify a toast reports "Applied to X of N" and only the previously-active ones actually deactivated (check by switching back to "All products" or reloading).
7. Repeat for **Reactivate** and **Clear codes**, confirming each only touches applicable rows.
8. Click **Clear selection** — confirm the bulk bar disappears and all checkboxes clear.

---

## Task 9: Final full-repo sanity pass

**Files:** none (verification only)

- [ ] **Step 1: Bot — full self-check suite**

```bash
ALLOW_SELFCHECK_DB=1 python -m app.db.products
ALLOW_SELFCHECK_DB=1 python -m app.services.crypto_watch
```

Expected: both print their "all assertions passed" lines with no errors.

- [ ] **Step 2: Bot — compile every touched file**

```bash
python -m py_compile app/db/products.py app/db/__init__.py app/keyboards.py app/handlers/products_admin.py app/main.py
```

Expected: no output (success).

- [ ] **Step 3: Web — full type-check + build**

```bash
cd admin && npx tsc --noEmit && npm run build
```

Expected: both succeed with no errors.

- [ ] **Step 4: Re-run both manual verification passes end-to-end** (Task 5 Step 4 and Task 8 Step 4) against a live bot + live dev server one more time, back to back, to confirm nothing regressed between tasks.

- [ ] **Step 5: Final commit (if anything was fixed during this pass)**

```bash
git add -A
git commit -m "fix: address issues found in stock-management final verification pass"
```

(Skip this step entirely if nothing needed fixing.)
