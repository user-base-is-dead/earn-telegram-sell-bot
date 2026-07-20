"""Admin: list/manage/edit/delete products, the add-product wizard, and the
per-product delivery-key upload."""
import re
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app import config, db
from app.formatting import (
    IST, cemoji, esc, render_desc, render_name, render_name_with_icon, truncate_safe, usdt,
)
from app.handlers.announcements import (
    _announcement_text, _broadcast, _is_restock, _offer_announcement, _price_change_text, _sale_text,
)
from app.keyboards import (
    CATALOG_NAME_MAX, LOW_STOCK_THRESHOLD, _admin_row_label, _btn, _is_out_of_stock, _name_sort_key,
    _product_icon_btn, cancel_kb, manage_keyboard, refresh_menu_kb, wizard_nav_kb,
)
from app.render import _edit_or_replace, _render, _send
from app.states import (
    ADD_DESC, ADD_ICON, ADD_NAME, ADD_PRICE, ADD_STOCK, DISCOUNT_DURATION,
    DISCOUNT_PRICE, EDIT_VALUE, KEYS_INPUT, WIZARD_ORDER,
)

WIZARD_PROMPTS = {
    ADD_NAME: (
        f"{cemoji('name', '🏷️')} <b>Step 1/5 — Name</b>\n"
        "What should this product be called?"
    ),
    ADD_DESC: (
        f"{cemoji('pencil', '📝')} <b>Step 2/5 — Description</b>\n"
        "Send a short, punchy description — or <code>-</code> to skip."
    ),
    ADD_PRICE: (
        f"{cemoji('money', '💵')} <b>Step 3/5 — Price (USDT)</b>\n"
        "Send the price in USDT, e.g. <code>9.99</code>."
    ),
    ADD_STOCK: (
        f"{cemoji('restock', '📦')} <b>Step 4/5 — Stock</b>\n"
        "Send the stock count, or <code>unlimited</code>."
    ),
    ADD_ICON: (
        f"{cemoji('name', '🏷️')} <b>Step 5/5 — Icon</b>\n"
        "Send a single emoji (custom/animated ones work too) to use as this "
        "product's icon everywhere it's shown — or <code>-</code> to use the default 🏷."
    ),
}


# --------------------------------------------------------------------------- #
# Admin: list products & orders
# --------------------------------------------------------------------------- #
async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.is_admin(update.effective_user.id):
        await _render(update, f"{cemoji('block', '🚫')} Not authorized.")
        return
    all_products = await db.list_products(only_active=False)
    all_products = sorted(all_products, key=_name_sort_key)  # A→Z by name
    if not all_products:
        await _render(
            update,
            f"No products yet. Tap {cemoji('plus', '➕')} <b>Add product</b> to create one.",
            refresh_menu_kb("menu:products"),
        )
        return

    q = update.callback_query
    page = 0
    if q and ":" in q.data:
        try:
            # callback_data is "menu:products:<page>", so take the LAST segment.
            # (the plain "menu:products" button has no number -> stays on page 0)
            page = int(q.data.split(":")[-1])
        except (ValueError, IndexError):
            page = 0

    page_size = 15
    total = len(all_products)
    start = page * page_size
    products = all_products[start:start + page_size]

    # Keyed ("automatic") products' real stock is their unused-key count, not
    # the manually-typed stock field — same fix as the buyer-facing catalog
    # (see app.db.products.effective_stock), fetched once for the whole page.
    key_counts = await db.get_key_pool_counts()

    rows = []
    for p in products:
        stock = db.effective_stock(p["stock"], key_counts.get(p["id"]))
        # Per-product icon (same as the buyer-facing catalog), swapped for ❌
        # when out of stock — stock and active state also shown as plain text.
        label, out_of_stock = _admin_row_label(p, stock)
        rows.append([_product_icon_btn(label, p, f"manage:{p['id']}", out_of_stock)])

    nav = []
    if page > 0:
        nav.append(_btn("⬅️ Prev", "prev", callback_data=f"menu:products:{page - 1}"))
    if start + page_size < total:
        nav.append(_btn("Next ➡️", "next", callback_data=f"menu:products:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append(
        [
            _btn("🔄 Refresh", "refresh", callback_data=f"menu:products:{page}"),
            _btn("⬅️ Menu", "menu", callback_data="menu:home"),
        ]
    )
    await _render(
        update,
        f"{cemoji('products', '📦')} <b>Products</b> — tap one to manage",
        InlineKeyboardMarkup(rows),
    )


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
        label, out_of_stock = _admin_row_label(p, stock)
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

    key_pool = await db.get_key_pool_count(pid)
    is_keyed = key_pool is not None
    stock = db.effective_stock(p["stock"], key_pool)
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
    try:
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
    except Exception:  # noqa: BLE001  (report partial progress, don't swallow silently)
        await _render(
            update,
            f"{cemoji('warn', '⚠️')} Applied to <b>{applied}</b> of <b>{len(selected)}</b> before an error occurred.",
            InlineKeyboardMarkup([[_btn("⬅️ Back to Stock", callback_data=f"menu:stock:{page}")]]),
        )
        raise

    await _render(
        update,
        f"{cemoji('check', '✅')} Applied to <b>{applied}</b> of <b>{len(selected)}</b> selected product(s).",
        InlineKeyboardMarkup([[_btn("⬅️ Back to Stock", callback_data=f"menu:stock:{page}")]]),
    )


# --------------------------------------------------------------------------- #
# Admin: manage / edit / delete a product
# --------------------------------------------------------------------------- #
async def product_manage_text(p) -> str:
    effective = await db.get_effective_stock(p["id"], p["stock"])
    stock = "∞ (unlimited)" if effective == db.UNLIMITED_STOCK else effective
    state = f"{cemoji('check', '✅')} Active" if p["active"] else f"{cemoji('block', '🚫')} Inactive"
    desc = render_desc(p) if p["description"] else "—"
    offer_usdt, is_discounted = db.effective_price(p)
    discount_line = ""
    if is_discounted:
        until_local = datetime.fromisoformat(p["offer_until"]).astimezone(IST).strftime("%d %b %H:%M IST")
        discount_line = (
            f"{cemoji('sale', '🔥')} Discount: <b>{esc(usdt(offer_usdt))}</b> "
            f"until {until_local}\n"
        )
    return (
        f"<b>Manage #{p['id']} — {render_name_with_icon(p)}</b>\n\n"
        f"{cemoji('pencil', '📝')} Description: {desc}\n"
        f"{cemoji('money', '💰')} Price: <b>{esc(usdt(p['price']))}</b>\n"
        f"{discount_line}"
        f"{cemoji('restock', '📦')} Stock: <b>{stock}</b>\n"
        f"{cemoji('key', '🔑')} Delivery codes: <b>{await db.count_unused_keys(p['id'])}</b> unused\n"
        f"Status: {state}"
    )


async def manage_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    pid = int(query.data.split(":")[1])
    p = await db.get_product(pid)
    if not p:
        await _render(update, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb("menu:products"))
        return
    await _render(update, await product_manage_text(p), manage_keyboard(p))


async def toggle_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    pid = int(query.data.split(":")[1])
    p = await db.get_product(pid)
    if not p:
        await _render(update, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb("menu:products"))
        return
    await db.set_product_active(pid, not p["active"])
    await _render(update, await product_manage_text(await db.get_product(pid)), manage_keyboard(await db.get_product(pid)))


async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    pid = int(query.data.split(":")[1])
    p = await db.get_product(pid)
    if not p:
        await _render(update, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb("menu:products"))
        return
    kb = InlineKeyboardMarkup(
        [
            [_btn("🗑 Yes, delete", "delete", callback_data=f"delok:{pid}")],
            [_btn("⬅️ No, keep it", "back", callback_data=f"manage:{pid}")],
        ]
    )
    await _render(
        update,
        f"{cemoji('warn', '⚠️')} Delete <b>{render_name(p)}</b> permanently?\n"
        "<i>If it has past orders it will be deactivated (hidden) instead, so order "
        "history stays intact.</i>",
        kb,
    )


async def delete_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    pid = int(query.data.split(":")[1])
    p = await db.get_product(pid)
    name = p["name"] if p else str(pid)
    try:
        await db.delete_product(pid)
        msg = f"{cemoji('trash', '🗑')} Deleted <b>{esc(name)}</b>."
    except Exception:  # noqa: BLE001  (FK: product has orders)
        await db.set_product_active(pid, False)
        msg = (
            f"{cemoji('warn', '⚠️')} <b>{esc(name)}</b> has past orders, so it was "
            "<b>deactivated</b> (hidden from the catalog) instead of deleted."
        )
    await _render(update, msg, refresh_menu_kb("menu:products"))


FIELD_HINTS = {
    "price": "Send the new <b>price in USDT</b> (number, e.g. <code>9.99</code>).",
    "stock": "Send the new <b>stock</b> count, or <code>unlimited</code>.",
    "name": "Send the new <b>name</b>.",
    "description": "Send the new <b>description</b> (or <code>-</code> for none).",
}


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    _, field, pid = query.data.split(":")
    pid = int(pid)
    p = await db.get_product(pid)
    if not p or (field not in db.EDITABLE_FIELDS and field != "icon"):
        await _edit_or_replace(query, f"{cemoji('warn', '⚠️')} Product not found.", refresh_menu_kb("menu:products"))
        return ConversationHandler.END
    context.user_data["edit_pid"] = pid
    context.user_data["edit_field"] = field
    if field == "icon":
        cur = p["icon_char"] if "icon_char" in p.keys() and p["icon_char"] else "— (using the default)"
        await context.bot.send_message(
            update.effective_chat.id,
            f"{cemoji('edit', '✏️')} Editing <b>icon</b> of <b>{render_name(p)}</b>\n"
            f"Current: {cur}\n\n"
            "Send an emoji for this product — a plain one (e.g. 🤖), or a Premium "
            "animated emoji (just send/forward it, no need to look up its ID). "
            "Send <code>-</code> to clear it and use the default.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb(),
        )
        return EDIT_VALUE
    cur = p[field]
    if field == "price":
        cur = usdt(cur)
    elif field == "stock":
        cur = "unlimited" if cur == db.UNLIMITED_STOCK else cur
    await context.bot.send_message(
        update.effective_chat.id,
        f"{cemoji('edit', '✏️')} Editing <b>{esc(field)}</b> of <b>{render_name(p)}</b>\n"
        f"Current: <code>{esc(cur) or '—'}</code>\n\n{FIELD_HINTS[field]}",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return EDIT_VALUE


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid = context.user_data.get("edit_pid")
    field = context.user_data.get("edit_field")
    if pid is None or field is None:
        return ConversationHandler.END
    raw = (update.message.text or "").strip()

    icon_emoji_id = None
    if field == "icon":
        # A Premium/animated emoji arrives as a custom_emoji entity — its .text is
        # the plain fallback char, its .custom_emoji_id is what buttons/tg-emoji need.
        found = update.message.parse_entities(["custom_emoji"])
        if found:
            entity, char = next(iter(found.items()))
            value, icon_emoji_id = char, entity.custom_emoji_id
        elif raw == "-":
            value = None
        elif raw and len(raw) <= 8:
            value = raw
        else:
            await update.message.reply_text(
                f"{cemoji('warn', '⚠️')} Send a single emoji (or '-' to clear it).",
                parse_mode=ParseMode.HTML,
            )
            return EDIT_VALUE
        field = "icon_char"

    elif field == "price":
        try:
            value = float(raw.replace(",", ""))
            if value < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                f"{cemoji('warn', '⚠️')} Please send a valid number (0 for free), e.g. 9.99",
                parse_mode=ParseMode.HTML,
            )
            return EDIT_VALUE
    elif field == "stock":
        low = raw.lower()
        if low in ("unlimited", "-", "-1", "∞"):
            value = db.UNLIMITED_STOCK
        else:
            try:
                value = int(low)
                if value < 0:
                    value = db.UNLIMITED_STOCK
            except ValueError:
                await update.message.reply_text(
                    f"{cemoji('warn', '⚠️')} Send a whole number or 'unlimited'.",
                    parse_mode=ParseMode.HTML,
                )
                return EDIT_VALUE
    elif field == "description":
        value = "" if raw == "-" else raw
    elif field == "name":
        value = raw
    # else: field == "icon_char" — value already computed above.

    old_prod = await db.get_product(pid) if field in ("stock", "price") else None
    old_stock = old_prod["stock"] if old_prod else None
    await db.update_product(pid, field, value)
    if field in ("name", "description"):
        # Preserves any custom/Premium emoji the admin typed — .text alone
        # only carries its plain fallback character (see render_name/render_desc).
        html_value = update.message.text_html.strip() if value else ""
        await db.update_product(pid, f"{field}_html", html_value)
    elif field == "icon_char":
        await db.update_product(pid, "icon_emoji_id", icon_emoji_id)
    context.user_data.pop("edit_pid", None)
    context.user_data.pop("edit_field", None)
    p = await db.get_product(pid)
    field_label = "icon" if field == "icon_char" else field
    await update.message.reply_text(f"{cemoji('check', '✅')} Updated <b>{esc(field_label)}</b>.", parse_mode=ParseMode.HTML)
    await context.bot.send_message(
        update.effective_chat.id, await product_manage_text(p),
        parse_mode=ParseMode.HTML, reply_markup=manage_keyboard(p),
    )
    # Offer to broadcast notable changes: a restock, or a price move up / down.
    if p and p["active"] and old_prod is not None:
        if field == "stock":
            if value == 0 and old_stock != 0:
                await _broadcast(context, _announcement_text("out", p))
            elif _is_restock(old_stock, value):
                await _offer_announcement(
                    context, update.effective_chat.id, "Restock",
                    _announcement_text("restock", p), pid,
                )
        elif field == "price":
            old_val = float(old_prod["price"])
            if value != old_val:
                text = _price_change_text(
                    render_name(p), old_prod["price"], p["price"], value > old_val,
                )
                await _offer_announcement(
                    context, update.effective_chat.id, "Price update", text, pid
                )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Admin: add product conversation
#
# The wizard is a linear sequence of steps (see WIZARD_ORDER). Every forward
# handler stores its answer, then calls `_wizard_prompt` for the NEXT step —
# which also records the current step in user_data["step"]. That memory lets
# `/back` (or the inline ⬅️ Back button) jump one step back from anywhere.
# --------------------------------------------------------------------------- #
async def _wizard_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, state: int, prefix: str = ""
) -> int:
    """Send a wizard step's prompt and remember the step so the user can go back."""
    context.user_data["step"] = state
    hint = "\n\n<i>Use the buttons below to go back or cancel.</i>"
    await _send(
        update, prefix + WIZARD_PROMPTS[state] + hint,
        reply_markup=wizard_nav_kb(show_back=state != ADD_NAME),
    )
    return state


async def add_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step one prompt back in the wizard (re-prompts step 1 if already first)."""
    current = context.user_data.get("step", ADD_NAME)
    idx = WIZARD_ORDER.index(current) if current in WIZARD_ORDER else 0
    if idx == 0:
        return await _wizard_prompt(
            update, context, ADD_NAME, prefix=f"{cemoji('back', '↩️')} You're already at the first step.\n\n"
        )
    return await _wizard_prompt(update, context, WIZARD_ORDER[idx - 1])


async def addproduct_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not config.is_admin(update.effective_user.id):
        await _send(update, f"{cemoji('block', '🚫')} Not authorized.")
        return ConversationHandler.END
    context.user_data["new_product"] = {}
    return await _wizard_prompt(
        update, context, ADD_NAME,
        prefix=(
            f"{cemoji('plus', '➕')} <b>Add New Product</b>\n"
            "<i>Let's get this listed — 5 quick steps.</i>\n\n"
        ),
    )


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_product"]["name"] = update.message.text.strip()
    # Preserves any custom/Premium emoji typed — .text alone only carries its
    # plain fallback character (see render_name in app.formatting).
    context.user_data["new_product"]["name_html"] = update.message.text_html.strip()
    return await _wizard_prompt(update, context, ADD_DESC)


async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["new_product"]["description"] = "" if text == "-" else text
    context.user_data["new_product"]["description_html"] = (
        "" if text == "-" else update.message.text_html.strip()
    )
    return await _wizard_prompt(update, context, ADD_PRICE)


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().replace(",", "")
    try:
        price = float(raw)
        if price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Please send a valid number (0 for free), e.g. 9.99",
            parse_mode=ParseMode.HTML,
        )
        return ADD_PRICE
    context.user_data["new_product"]["price"] = price
    return await _wizard_prompt(update, context, ADD_STOCK)


async def add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().lower()
    if raw in ("unlimited", "-", "-1", "∞"):
        stock = db.UNLIMITED_STOCK
    else:
        try:
            stock = int(raw)
            if stock < 0:
                stock = db.UNLIMITED_STOCK
        except ValueError:
            await update.message.reply_text(
                f"{cemoji('warn', '⚠️')} Send a whole number or 'unlimited'.",
                parse_mode=ParseMode.HTML,
            )
            return ADD_STOCK

    context.user_data["new_product"]["stock"] = stock
    return await _wizard_prompt(update, context, ADD_ICON)


async def add_icon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    icon_char = icon_emoji_id = None
    found = update.message.parse_entities(["custom_emoji"])
    if found:
        entity, char = next(iter(found.items()))
        icon_char, icon_emoji_id = char, entity.custom_emoji_id
    elif raw == "-":
        pass
    elif raw and len(raw) <= 8:
        icon_char = raw
    else:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Send a single emoji (or '-' to use the default 🏷).",
            parse_mode=ParseMode.HTML,
        )
        return ADD_ICON

    p = context.user_data["new_product"]
    stock = p["stock"]
    product_id = await db.add_product(
        name=p["name"],
        description=p.get("description", ""),
        price=p["price"],
        content=p.get("content", ""),
        stock=stock,
        name_html=p.get("name_html") or None,
        description_html=p.get("description_html") or None,
        icon_char=icon_char,
        icon_emoji_id=icon_emoji_id,
    )
    context.user_data.clear()
    stock_txt = "∞ unlimited" if stock == db.UNLIMITED_STOCK else str(stock)
    new_p = await db.get_product(product_id)
    await update.message.reply_text(
        f"{cemoji('check', '✅')} <b>Product added!</b>\n\n"
        f"<blockquote>{cemoji('new', '🆕')} <b>#{product_id} {render_name(new_p)}</b>\n"
        f"{cemoji('money', '💰')} {esc(usdt(p['price']))}\n"
        f"{cemoji('restock', '📦')} Stock: {stock_txt}</blockquote>\n\n"
        "Tap below to view, edit, or add delivery codes.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[_btn("📦 Manage products", "products", callback_data="menu:products")]]
        ),
    )
    await _offer_announcement(
        context, update.effective_chat.id, "New product",
        _announcement_text("new", new_p), product_id,
    )
    return ConversationHandler.END


async def _add_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"{cemoji('cancel_x', '✖️')} Left the current step. Tap {cemoji('plus', '➕')} "
        "<b>Add product</b> again to start adding.",
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------------------------------- #
# Admin: per-product delivery-key upload
# --------------------------------------------------------------------------- #
async def keys_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    context.user_data["keys_pid"] = pid
    await context.bot.send_message(
        update.effective_chat.id,
        f"{cemoji('key', '🔑')} <b>{render_name(p)}</b> — {await db.count_unused_keys(pid)} unused code(s).\n\n"
        "Paste codes to add, one per line.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return KEYS_INPUT


async def keys_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid = context.user_data.get("keys_pid")
    if pid is None:
        return ConversationHandler.END
    lines = [l.strip() for l in update.message.text.splitlines() if l.strip()]
    if not lines:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} No codes found — send at least one line.",
            parse_mode=ParseMode.HTML,
        )
        return KEYS_INPUT
    old_unused = await db.count_unused_keys(pid)
    n = await db.add_product_keys(pid, lines)
    context.user_data.pop("keys_pid", None)
    p = await db.get_product(pid)
    new_unused = await db.count_unused_keys(pid)
    await update.message.reply_text(
        f"{cemoji('check', '✅')} Added <b>{n}</b> code(s). <b>{new_unused}</b> unused remaining.",
        parse_mode=ParseMode.HTML,
    )
    await context.bot.send_message(
        update.effective_chat.id, await product_manage_text(p),
        parse_mode=ParseMode.HTML, reply_markup=manage_keyboard(p),
    )
    # A keyed product's real stock is its unused-key count (see effective_stock),
    # so a restock here is "had none, now has some" rather than a `stock` field edit.
    if p and p["active"] and new_unused > old_unused:
        restock_p = dict(p)
        restock_p["stock"] = new_unused
        await _offer_announcement(
            context, update.effective_chat.id, "Restock",
            _announcement_text("restock", restock_p), pid,
        )
    return ConversationHandler.END


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
        f"Current price: <code>{esc(usdt(p['price']))}</code>\n\n"
        "Step 1/2 — Send the discounted <b>price in USDT</b> "
        "(must be lower than the current price).",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return DISCOUNT_PRICE


async def discount_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pid = context.user_data.get("discount_pid")
    if pid is None:
        return ConversationHandler.END
    p = await db.get_product(pid)
    if not p:
        context.user_data.pop("discount_pid", None)
        await update.message.reply_text(f"{cemoji('warn', '⚠️')} Product not found.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
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
        "Step 2/2 — Send how long the discount should run: "
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

    offer_usdt = context.user_data.pop("discount_usdt")
    context.user_data.pop("discount_pid", None)
    offer_until = (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")
    await db.set_product_offer(pid, offer_usdt, offer_until)

    p = await db.get_product(pid)
    if not p:
        await update.message.reply_text(f"{cemoji('warn', '⚠️')} Product not found.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    await update.message.reply_text(
        f"{cemoji('check', '✅')} Discount set for <b>{raw}</b>.", parse_mode=ParseMode.HTML
    )
    await context.bot.send_message(
        update.effective_chat.id, await product_manage_text(p),
        parse_mode=ParseMode.HTML, reply_markup=manage_keyboard(p),
    )
    if p["active"]:
        text = _sale_text(render_name(p), p["price"], offer_usdt, raw)
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
