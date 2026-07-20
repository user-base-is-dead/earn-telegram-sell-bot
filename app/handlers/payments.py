"""Customer: buy -> show payment details -> UTR/screenshot submission conversation."""
import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app import config, db
from app.formatting import (
    _fmt, _format_usdt, _order_no, _pad, cemoji, esc, order_amount_str,
    qty_suffix, render_name, usdt,
)
from app.keyboards import _btn, back_to_menu_kb, cancel_kb, review_keyboard
from app.render import _edit_or_replace, _send
from app.states import BUY_QTY, PAY_UTR

logger = logging.getLogger(__name__)

# Sane per-order ceiling — guards against a fat-fingered "500" instead of "5"
# and keeps unlimited-stock products from offering an unbounded quantity.
MAX_ORDER_QTY = 20


def order_review_text(order) -> str:
    uname = f"@{order['username']}" if order["username"] else "(no username)"
    method = order["method"] if "method" in order.keys() else ""
    return (
        f"{cemoji('receipt', '🧾')} <b>New payment to review</b>\n\n"
        f"Order: <b>{esc(_order_no(order))}</b>\n"
        f"Product: <b>{esc(order['product_name'])}{qty_suffix(order)}</b>\n"
        f"Amount: <b>{esc(order_amount_str(order))}</b>\n"
        f"Method: <b>{esc(method or '—')}</b>\n"
        f"Buyer: {esc(uname)} (id <code>{order['user_id']}</code>)\n"
        f"UTR / TxID: <code>{esc(order['utr'] or '—')}</code>"
    )


async def _payment_buttons(order_id: int) -> InlineKeyboardMarkup:
    order = await db.get_order(order_id)
    back_cb = f"view:{order['product_id']}" if order else "catalog"
    return InlineKeyboardMarkup(
        [
            [_btn("⬅️ Menu", "menu", callback_data="menu:home")],
            [_btn("✅ I've Paid", "check", callback_data=f"paid:{order_id}")],
            [
                _btn("🛍 Catalog", "browse", callback_data="catalog"),
                _btn("⬅️ Back", "back", callback_data=back_cb),
            ],
        ]
    )


async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":", 1)[1])
    p = await db.get_product(product_id)
    stock = await db.get_effective_stock(product_id, p["stock"]) if p else 0
    if not p or not p["active"] or stock == 0:
        await query.edit_message_text(
            f"{cemoji('out_of_stock', '❌')} Sorry, this product is out of stock.",
            parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb()
        )
        return ConversationHandler.END

    usdt_price, _ = db.effective_price(p)

    # Free product -> create order immediately and show Claim. No qty prompt:
    # claiming more than one free unit isn't a real purchase to plan around.
    if usdt_price == 0:
        user = update.effective_user
        order_id = await db.create_order(
            user_id=user.id, username=user.username or "",
            product_id=p["id"], product_name=p["name"],
            amount_usdt=usdt_price,
        )
        await _edit_or_replace(
            query,
            f"{cemoji('gift', '🎁')} <b>{render_name(p)}</b> is <b>Free!</b>\n\n"
            f"Tap <b>{cemoji('gift', '🎁')} Claim</b> to receive it instantly.",
            InlineKeyboardMarkup([
                [_btn("🎁 Claim", "gift", callback_data=f"claim:{order_id}")],
                [_btn("⬅️ Back", "back", callback_data=f"view:{p['id']}")],
            ]),
        )
        return ConversationHandler.END

    cap = MAX_ORDER_QTY if stock == db.UNLIMITED_STOCK else min(stock, MAX_ORDER_QTY)
    if cap <= 1:
        # Nothing to ask — only one unit could ever be bought anyway.
        await _offer_payment_methods(update, context, p, qty=1)
        return ConversationHandler.END

    context.user_data["buy_pid"] = product_id
    await _edit_or_replace(
        query,
        f"{cemoji('cart', '🛍')} <b>{render_name(p)}</b>\n\n"
        f"How many would you like? Send a number from <b>1</b> to <b>{cap}</b>.",
        cancel_kb(),
    )
    return BUY_QTY


async def buy_qty_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    product_id = context.user_data.get("buy_pid")
    if product_id is None:
        return ConversationHandler.END
    raw = update.message.text.strip()
    try:
        qty = int(raw)
    except ValueError:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Please send a whole number, e.g. 2.",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb(),
        )
        return BUY_QTY

    # Re-fetch fresh — stock may have moved while the buyer was typing.
    p = await db.get_product(product_id)
    stock = await db.get_effective_stock(product_id, p["stock"]) if p else 0
    if not p or not p["active"] or stock == 0:
        await update.message.reply_text(
            f"{cemoji('out_of_stock', '❌')} Sorry, this product is no longer available.",
            parse_mode=ParseMode.HTML,
        )
        context.user_data.pop("buy_pid", None)
        return ConversationHandler.END

    cap = MAX_ORDER_QTY if stock == db.UNLIMITED_STOCK else min(stock, MAX_ORDER_QTY)
    if qty < 1 or qty > cap:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Please send a number from 1 to {cap}.",
            parse_mode=ParseMode.HTML, reply_markup=cancel_kb(),
        )
        return BUY_QTY

    context.user_data.pop("buy_pid", None)
    await _offer_payment_methods(update, context, p, qty)
    return ConversationHandler.END


async def _render_screen(update: Update, text: str, kb: InlineKeyboardMarkup) -> None:
    """Show a screen whether we're still handling a callback (edit in place)
    or replying to a plain text message — e.g. the buyer's typed quantity, or
    the single-payment-method shortcut reached from it — where there's no
    button-tap message left to edit. Does not call query.answer(): every
    caller has already answered its own callback (or there isn't one)."""
    if update.callback_query:
        await _edit_or_replace(update.callback_query, text, kb)
    else:
        await update.message.reply_text(_pad(text, kb), parse_mode=ParseMode.HTML, reply_markup=kb)


async def _offer_payment_methods(
    update: Update, context: ContextTypes.DEFAULT_TYPE, p, qty: int
) -> None:
    from app.handlers.topup import wallet_confirm_prompt

    product_id = p["id"]
    user = update.effective_user
    usdt_price, _ = db.effective_price(p)
    total_usdt = usdt_price * qty

    # Build method buttons — carry product_id, order created on method selection.
    # Auto mode: wallet is the only rail ever offered. Manual mode: the reverse.
    auto = config.is_auto_mode()
    wallet_available = (
        auto and config.wallet_topup_enabled() and await db.count_unused_keys(product_id) >= qty
    )
    methods = []
    if not auto:
        if config.binance_pay_enabled():
            methods.append(_btn(
                f"💠 Binance Pay — {usdt(total_usdt)}", "binance", callback_data=f"pm:bnb:{product_id}"
            ))
        if config.blockchain_enabled():
            methods.append(_btn(
                f"⛓ Crypto — {usdt(total_usdt)}", "chain", callback_data=f"pm:chain:{product_id}"
            ))
    if wallet_available:
        bal = _format_usdt(await db.get_wallet_balance(user.id))
        methods.append(_btn(
            f"⚡ Wallet ({bal} USDT) — instant", "money", callback_data=f"pm:wallet:{product_id}"
        ))

    # No payment method available for this product/price -> don't show an empty chooser.
    if not methods:
        await _render_screen(
            update,
            f"{cemoji('warn', '⚠️')} <b>{render_name(p)}</b> can't be purchased right now — no payment "
            "method is available for this price. Please contact the seller.",
            InlineKeyboardMarkup([[_btn("⬅️ Back", "back", callback_data=f"view:{product_id}")]]),
        )
        return

    # Single method -> go directly (order created there).
    if len(methods) == 1:
        if not auto and config.binance_pay_enabled():
            await _create_and_show(update, context, product_id, "bnb", qty)
        elif not auto and config.blockchain_enabled():
            await _create_and_show(update, context, product_id, "chain", qty)
        elif wallet_available:
            await wallet_confirm_prompt(update, context, product_id, qty)
        return

    # Multiple methods -> the buyer picks one via a separate, stateless tap
    # later (pm:*/pay_wallet aren't part of this conversation), so qty (and
    # which product it's for) is stashed for them to recover — popped once
    # consumed, and guarded there against a stale/mismatched product_id.
    context.user_data["buy_pid"] = product_id
    context.user_data["buy_qty"] = qty
    qty_note = f" ×{qty}" if qty > 1 else ""
    rows = [[m] for m in methods]
    rows.append([_btn("⬅️ Back", "back", callback_data=f"view:{product_id}")])
    note = (
        "" if wallet_available else
        "This item is fulfilled manually — you'll receive it after we confirm your payment.\n"
    )
    await _render_screen(
        update,
        _pad(
            f"{cemoji('cart', '🛍')} <b>{render_name(p)}{qty_note}</b>\n"
            f"Amount: <b>{esc(usdt(total_usdt))}</b>\n\n{note}"
            f"Choose a payment method {cemoji('point_down', '👇')}",
            InlineKeyboardMarkup(rows),
        ),
        InlineKeyboardMarkup(rows),
    )


def _pop_qty_for(context: ContextTypes.DEFAULT_TYPE, product_id: int) -> int:
    """Recover the qty stashed by _offer_payment_methods for a pm:*/pay_wallet
    tap. Falls back to 1 (and leaves user_data untouched) if it's missing or
    was stashed for a different product — a stale screen from an earlier or
    abandoned purchase must never silently apply its qty to a different one."""
    if context.user_data.get("buy_pid") != product_id:
        return 1
    qty = context.user_data.pop("buy_qty", 1)
    context.user_data.pop("buy_pid", None)
    return qty


async def _create_order_for(user, p, qty: int = 1) -> int:
    usdt_price, _ = db.effective_price(p)
    return await db.create_order(
        user_id=user.id, username=user.username or "",
        product_id=p["id"], product_name=p["name"],
        amount_usdt=usdt_price * qty, qty=qty,
    )


async def _create_and_show(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, method: str, qty: int = 1
) -> None:
    p = await db.get_product(product_id)
    stock = await db.get_effective_stock(product_id, p["stock"]) if p else 0
    if not p or stock < qty:
        # Reached from either a button tap (pay_binance/pay_blockchain) or the
        # plain text message right after a typed quantity (single-method shortcut).
        if update.callback_query:
            await update.callback_query.answer("Out of stock.", show_alert=True)
        else:
            await update.message.reply_text(
                f"{cemoji('out_of_stock', '❌')} Sorry, that's no longer available in that quantity.",
                parse_mode=ParseMode.HTML,
            )
        return
    order_id = await _create_order_for(update.effective_user, p, qty)
    if method == "bnb":
        await _show_binance_pay_payment(update, context, order_id)
    else:
        await _show_blockchain_payment(update, context, order_id)


def _pay_text_header(order) -> list[str]:
    """Short header for Binance / crypto screens. The USDT amount (product price +
    the configured network-fee buffer) is wrapped in <code> so buyers can tap it
    to copy the exact number."""
    amt_usdt = order["amount_usdt"] if "amount_usdt" in order.keys() else 0.0
    lines = [
        f"{cemoji('star', '🌟')} <b>{esc(config.STORE_NAME)}</b>",
        "",
        f"{cemoji('receipt', '🧾')} <b>Order {esc(_order_no(order))}</b>{esc(qty_suffix(order))}",
        "",
    ]
    if amt_usdt > 0:
        total = amt_usdt + config.CRYPTO_FEE_USDT
        line = f"<b>Amount:</b> <code>{_fmt(total)} USDT</code>"
        if config.CRYPTO_FEE_USDT > 0:
            line += " <i>(incl. charges)</i>"
        lines.append(line)
    else:
        lines.append(f"<b>Amount:</b> <b>{esc(usdt(amt_usdt))}</b>")
    return lines


_PAID_FOOTER = (
    f"\n<blockquote>{cemoji('warn', '⚠️')} <b>Send this exact amount</b> — a wrong amount will not be refunded.\n"
    f"After paying, tap <b>{cemoji('check', '✅')} I've Paid</b> and send your TxID or a screenshot.</blockquote>"
)


async def _show_binance_pay_payment(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    order = await db.get_order(order_id)
    await db.set_order_method(order_id, "Binance Pay")
    lines = _pay_text_header(order) + [
        "",
        f"{cemoji('binance', '💠')} <b>Binance Pay</b>",
        f"<b>Pay ID:</b> <code>{esc(config.BINANCE_PAY_ID)}</code>",
    ]
    text = "\n".join(lines) + _PAID_FOOTER
    await _render_screen(update, text, await _payment_buttons(order_id))


async def _show_blockchain_payment(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int
) -> None:
    order = await db.get_order(order_id)
    await db.set_order_method(order_id, "Crypto")
    lines = _pay_text_header(order) + [
        "",
        f"{cemoji('chain', '⛓')} <b>Crypto ({esc(config.CRYPTO_NETWORK)})</b>",
        f"<b>Address:</b> <code>{esc(config.CRYPTO_ADDRESS)}</code>",
    ]
    text = "\n".join(lines) + _PAID_FOOTER
    await _render_screen(update, text, await _payment_buttons(order_id))


async def claim_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free product claim -> reserve the order and send it to admins for manual delivery."""
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != update.effective_user.id:
        await query.answer("Order not found.", show_alert=True)
        return
    if order["status"] != db.STATUS_CREATED:
        await query.answer("Already claimed.", show_alert=True)
        return
    # Reserve (pending review) and notify admins to deliver manually — same as a paid order.
    await db.set_order_status(order_id, db.STATUS_PENDING)
    order = await db.get_order(order_id)
    text = f"{cemoji('gift', '🎁')} <b>FREE claim</b>\n" + order_review_text(order)
    kb = review_keyboard(order_id)
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not notify admin %s: %s", admin_id, e)
    await _edit_or_replace(
        query,
        f"{cemoji('check', '✅')} Your free item is reserved — you'll receive it here shortly.",
        back_to_menu_kb(),
    )


async def pay_binance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if config.is_auto_mode():
        await query.answer("This isn't available right now.", show_alert=True)
        return
    await query.answer()
    product_id = int(query.data.split(":")[2])
    await _create_and_show(update, context, product_id, "bnb", _pop_qty_for(context, product_id))


async def pay_blockchain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if config.is_auto_mode():
        await query.answer("This isn't available right now.", show_alert=True)
        return
    await query.answer()
    product_id = int(query.data.split(":")[2])
    await _create_and_show(update, context, product_id, "chain", _pop_qty_for(context, product_id))


# --------------------------------------------------------------------------- #
# Customer: payment confirmation conversation
# --------------------------------------------------------------------------- #
async def paid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split(":", 1)[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != update.effective_user.id:
        await query.answer("Order not found.", show_alert=True)
        return ConversationHandler.END

    context.user_data["pending_order_id"] = order_id
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            f"{cemoji('point_right', '👉')} Thanks! For <b>Order {esc(_order_no(order))}</b>, please send your "
            "<b>Binance TxID</b> / transaction hash, "
            "or a <b>screenshot</b> of the successful payment."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return PAY_UTR


async def _submit_and_notify(update, context, order_id, utr, photo_file_id=None):
    await db.set_order_utr(order_id, utr)
    order = await db.get_order(order_id)

    await update.message.reply_text(
        f"{cemoji('check', '✅')} Got it! Order <b>{esc(_order_no(order))}</b> is now pending review. "
        "You'll receive your product here as soon as the payment is confirmed.",
        parse_mode=ParseMode.HTML,
    )

    text = order_review_text(order)
    kb = review_keyboard(order_id)
    for admin_id in config.ADMIN_IDS:
        try:
            if photo_file_id:
                await context.bot.send_photo(
                    admin_id, photo_file_id, caption=text,
                    parse_mode=ParseMode.HTML, reply_markup=kb,
                )
            else:
                await context.bot.send_message(
                    admin_id, text, parse_mode=ParseMode.HTML, reply_markup=kb
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not notify admin %s: %s", admin_id, e)


async def receive_utr_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data.get("pending_order_id")
    if order_id is None:
        return ConversationHandler.END
    utr = update.message.text.strip()
    await _submit_and_notify(update, context, order_id, utr)
    context.user_data.pop("pending_order_id", None)
    return ConversationHandler.END


async def receive_utr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data.get("pending_order_id")
    if order_id is None:
        return ConversationHandler.END
    file_id = update.message.photo[-1].file_id
    await _submit_and_notify(update, context, order_id, "(screenshot attached)", file_id)
    context.user_data.pop("pending_order_id", None)
    return ConversationHandler.END
