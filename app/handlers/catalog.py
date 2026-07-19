"""Customer: catalog browsing & product detail."""
from telegram import Update
from telegram.ext import ContextTypes

from app import config, db
from app.formatting import cemoji, esc, price_both, render_desc, render_name_with_icon
from app.keyboards import back_to_menu_kb, catalog_keyboard, product_detail_keyboard, refresh_menu_kb
from app.render import _render


async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await db.list_products(only_active=True, include_out_of_stock=True):
        await _render(
            update,
            f"{cemoji('warn', '⚠️')} No products available right now.",
            refresh_menu_kb("catalog"),
        )
        return
    q = update.callback_query
    page = 0
    if q and ":" in q.data:
        try:
            page = int(q.data.split(":")[1])
        except (ValueError, IndexError):
            page = 0
    await _render(
        update,
        (
            f"{cemoji('cart', '🛍')} <b>Catalog</b>\n"
            "<i>Everything in stock, ready for instant delivery.</i>\n\n"
            "Tap a product to see its <b>details</b> and <b>price</b>."
        ),
        await catalog_keyboard(page),
    )


async def view_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    product_id = int(update.callback_query.data.split(":", 1)[1])
    p = await db.get_product(product_id)
    if not p or not p["active"]:
        await _render(
            update, f"{cemoji('warn', '⚠️')} This product is no longer available.", back_to_menu_kb()
        )
        return

    stock = await db.get_effective_stock(product_id, p["stock"])
    in_stock = stock == db.UNLIMITED_STOCK or stock > 0
    if stock == db.UNLIMITED_STOCK:
        stock_line = f"\n{cemoji('check', '✅')} <b>In stock</b>"
    elif stock > 0:
        stock_line = f"\n{cemoji('check', '✅')} In stock: <b>{stock}</b>"
    else:
        stock_line = f"\n{cemoji('out_of_stock', '❌')} <b>Out of stock</b>"
    desc = (p["description"] or "").strip()
    text = f"<b>{render_name_with_icon(p)}</b>\n"
    if desc:
        text += f"{render_desc(p)}\n"
    offer_usdt, offer_inr, is_discounted = db.effective_price(p)
    inr = 0.0 if config.is_auto_mode() else offer_inr
    if is_discounted:
        orig_inr = 0.0 if config.is_auto_mode() else p["price_inr"]
        text += (
            f"\n{cemoji('sale', '🔥')} <b>Flash sale!</b>\n"
            f"{cemoji('money', '💰')} <s>{esc(price_both(p['price'], orig_inr))}</s> → "
            f"<b>{esc(price_both(offer_usdt, inr))}</b>"
        )
    else:
        text += f"\n{cemoji('money', '💰')} <b>Price: {esc(price_both(offer_usdt, inr))}</b>"
    text += stock_line
    await _render(update, text, product_detail_keyboard(p, in_stock))
