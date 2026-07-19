"""Wallet top-ups (auto-confirmed BSC / Binance Pay) and wallet purchases.

A buyer tops up an internal balance; a background job (app.services.crypto_watch,
wired in via PTB's job_queue in app.main) detects the incoming transfer and
credits it with zero admin involvement. Buying then debits the balance
instantly and auto-delivers the next unused code from that product's key pool
— no admin approval step at all on the happy path.
"""
import logging
from decimal import Decimal, InvalidOperation

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app import config, db
from app.formatting import _format_usdt, _order_no, cemoji, esc, render_name, usdt
from app.handlers.announcements import _announcement_text, _broadcast
from app.handlers.payments import _create_order_for, _pop_qty_for
from app.keyboards import _btn, back_to_menu_kb, cancel_kb
from app.render import _edit_or_replace, _render, _send
from app.services import crypto_watch, qr as qr_utils
from app.states import TOPUP_AMOUNT, TOPUP_CHECK

logger = logging.getLogger(__name__)


# Rail -> the matching CUSTOM_EMOJI_IDS key, for the rail-choice buttons below.
_RAIL_EMOJI_KEY = {db.RAIL_BSC: "chain", db.RAIL_BINANCE_PAY: "binance"}


def _topup_rail_choices() -> list[tuple[str, str]]:
    # Plain-glyph labels; _topup_amount wraps each in _btn() with _RAIL_EMOJI_KEY,
    # which swaps the leading glyph for the configured premium icon.
    choices = []
    if config.blockchain_enabled():
        choices.append((db.RAIL_BSC, f"⛓ Crypto ({config.CRYPTO_NETWORK})"))
    if config.binance_pay_autoconfirm_enabled():
        choices.append((db.RAIL_BINANCE_PAY, "💠 Binance Pay"))
    return choices


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main-menu 'Balance' button: current wallet balance + a way to add funds.

    Guarded by is_auto_mode() (not just by hiding the entry-point button) so a
    stale Balance button from a screen rendered before an admin's mode flip
    fails safely instead of revealing wallet details while in Manual mode."""
    if not config.is_auto_mode():
        await _render(update, f"{cemoji('warn', '⚠️')} This isn't available right now.", back_to_menu_kb())
        return
    balance = _format_usdt(await db.get_wallet_balance(update.effective_user.id))
    text = (
        f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
        f"{cemoji('money', '💰')} <b>Your wallet</b>\n"
        f"<blockquote>Balance: <b>{esc(balance)} USDT</b></blockquote>"
    )
    if config.wallet_topup_enabled():
        text += "\n<i>Top up once, then buy instantly with zero waiting.</i>"
        kb = InlineKeyboardMarkup([
            [_btn("➕ Top up", "topup", callback_data="topup")],
            [_btn("⬅️ Menu", "menu", callback_data="menu:home")],
        ])
    else:
        text += "\n\n<i>Top-ups aren't enabled yet — contact support to add funds.</i>"
        kb = back_to_menu_kb()
    await _render(update, text, kb)


async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not (config.is_auto_mode() and config.wallet_topup_enabled()):
        await _send(update, f"{cemoji('warn', '⚠️')} This isn't available right now.")
        return ConversationHandler.END
    balance = _format_usdt(await db.get_wallet_balance(update.effective_user.id))
    await _send(
        update,
        f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
        f"{cemoji('money', '💰')} <b>Wallet top-up</b>\n"
        f"<blockquote>Balance: <b>{esc(balance)} USDT</b></blockquote>\n"
        "How much USDT would you like to add? Send a number, e.g. <code>10</code>.",
        reply_markup=cancel_kb(),
    )
    return TOPUP_AMOUNT


async def topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().replace(",", "")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} Please send a positive number, e.g. 10",
            parse_mode=ParseMode.HTML,
        )
        return TOPUP_AMOUNT
    base_micro = int(amount * 1_000_000)
    choices = _topup_rail_choices()
    if len(choices) == 1:
        return await _create_topup(update, context, choices[0][0], base_micro)
    context.user_data["topup_base_micro"] = base_micro
    kb = InlineKeyboardMarkup(
        [[_btn(label, _RAIL_EMOJI_KEY.get(rail), callback_data=f"topup:rail:{rail}")] for rail, label in choices]
    )
    await update.message.reply_text(
        f"{cemoji('point_down', '👇')} Choose how you'll pay:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    return TOPUP_AMOUNT


async def topup_rail_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    rail = query.data.split(":")[2]
    base_micro = context.user_data.get("topup_base_micro", 0)
    return await _create_topup(update, context, rail, base_micro, via_query=query)


async def _create_topup(update, context, rail: str, base_micro: int, via_query=None) -> int:
    tagged = await db.create_tagged_deposit(update.effective_user.id, rail, base_micro, config.DEPOSIT_EXPIRY_MINUTES)
    context.user_data["topup_rail"] = rail
    amount_str = _format_usdt(tagged)
    kb = InlineKeyboardMarkup([[_btn("🔍 Check my payment", "search", callback_data="topup:check")]])
    brand = f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
    if rail == db.RAIL_BSC:
        text = (
            brand +
            f"{cemoji('chain', '⛓')} <b>Top-up request</b> ({esc(config.CRYPTO_NETWORK)})\n\n"
            f"<b>Amount:</b> <code>{esc(amount_str)} USDT</code> <i>(send this exact figure)</i>\n"
            f"<b>Address:</b> <code>{esc(config.CRYPTO_ADDRESS)}</code>\n"
            f"<blockquote>{cemoji('bolt', '⚡')} Auto-credited in ~1 min, no need to message us · "
            f"Expires in <b>{config.DEPOSIT_EXPIRY_MINUTES} min</b></blockquote>"
        )
        qr = qr_utils.generate_qr_png(config.CRYPTO_ADDRESS)
        target = via_query.message if via_query else update.message
        await target.reply_photo(qr, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        text = (
            brand +
            f"{cemoji('binance', '💠')} <b>Top-up request</b>\n\n"
            f"<b>Amount:</b> <code>{esc(amount_str)} USDT</code> <i>(send this exact figure)</i>\n"
            f"<b>Pay ID:</b> <code>{esc(config.BINANCE_PAY_ID)}</code>\n"
            f"<blockquote>{cemoji('bolt', '⚡')} Auto-credited in ~1 min, no need to message us · "
            f"Expires in <b>{config.DEPOSIT_EXPIRY_MINUTES} min</b></blockquote>"
        )
        target = via_query.message if via_query else update.message
        await target.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    return TOPUP_CHECK


async def topup_check_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        f"{cemoji('search', '🔍')} Paste the transaction hash / Binance Pay transaction ID:",
        parse_mode=ParseMode.HTML,
    )
    return TOPUP_CHECK


async def topup_check_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    rail = context.user_data.get("topup_rail")
    ref = update.message.text.strip()
    if rail == db.RAIL_BINANCE_PAY:
        result = await crypto_watch.lookup_binance_pay_txn(ref)
    else:
        result = await crypto_watch.lookup_bsc_tx(ref)
    await update.message.reply_text(result, parse_mode=ParseMode.HTML)
    context.user_data.pop("topup_rail", None)
    context.user_data.pop("topup_base_micro", None)
    return ConversationHandler.END


async def pay_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_auto_mode():
        await query.answer("This isn't available right now.", show_alert=True)
        return
    await query.answer()
    product_id = int(query.data.split(":")[2])
    await wallet_confirm_prompt(update, context, product_id, _pop_qty_for(context, product_id))


async def wallet_confirm_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, qty: int
) -> None:
    """Show a Confirm/Cancel screen before the instant, no-undo wallet debit +
    auto-delivery — stashes qty the same way the multi-method chooser does so
    the Confirm tap (a separate, stateless callback) can recover it."""
    p = await db.get_product(product_id)
    if not p:
        return
    context.user_data["buy_pid"] = product_id
    context.user_data["buy_qty"] = qty
    usdt_price, _, _ = db.effective_price(p)
    total = usdt_price * qty
    qty_note = f" ×{qty}" if qty > 1 else ""
    bal = _format_usdt(await db.get_wallet_balance(update.effective_user.id))
    text = (
        f"{cemoji('bolt', '⚡')} <b>Confirm your purchase</b>\n\n"
        f"<b>{render_name(p)}{qty_note}</b>\n"
        f"Amount to deduct: <b>{esc(usdt(total))}</b> (balance: {esc(bal)} USDT)\n\n"
        f"<blockquote>{cemoji('gift', '🎁')} Your code(s) are delivered instantly on confirm — "
        f"this can't be undone.</blockquote>"
    )
    kb = InlineKeyboardMarkup([
        [_btn(f"✅ Confirm & Pay — {usdt(total)}", "check", callback_data=f"pm:wconf:{product_id}")],
        [_btn("✖️ Cancel", "cancel_x", callback_data=f"view:{product_id}")],
    ])
    if update.callback_query:
        await _edit_or_replace(update.callback_query, text, kb)
    else:
        await _send(update, text, kb)


async def pay_wallet_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split(":")[2])
    await _pay_from_wallet(update, context, product_id, _pop_qty_for(context, product_id))


async def _pay_from_wallet(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, qty: int = 1
) -> None:
    """Instant, admin-free purchase: debit the wallet and auto-deliver qty keys."""
    query = update.callback_query
    user = update.effective_user
    p = await db.get_product(product_id)
    stock = await db.get_effective_stock(product_id, p["stock"]) if p else 0
    if not p or not p["active"] or stock < qty:
        if query:
            await query.answer("Out of stock.", show_alert=True)
        return

    async def _reply(msg: str, kb=back_to_menu_kb()) -> None:
        if query:
            await _edit_or_replace(query, msg, kb)
        else:
            await _send(update, msg, kb)

    if await db.count_unused_keys(product_id) < qty:
        await _reply(f"{cemoji('warn', '⚠️')} This product doesn't have {qty} delivery codes left. Contact support.")
        return
    usdt_price, _, _ = db.effective_price(p)
    price_micro = int(Decimal(str(usdt_price)) * 1_000_000) * qty
    if not await db.debit_wallet(user.id, price_micro, reason="purchase", ref=str(product_id)):
        balance_micro = await db.get_wallet_balance(user.id)
        bal, need, short = (
            _format_usdt(balance_micro), _format_usdt(price_micro), _format_usdt(price_micro - balance_micro)
        )
        await _reply(
            f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
            f"{cemoji('warn', '⚠️')} <b>Insufficient wallet balance</b>\n"
            f"<blockquote>Balance: <b>{esc(bal)} USDT</b>\n"
            f"Needed: <b>{esc(need)} USDT</b>\n"
            f"Short by: <b>{esc(short)} USDT</b></blockquote>\n\n"
            f"{cemoji('money', '💰')} Top up now for instant, auto-delivered purchases — no waiting.",
            kb=InlineKeyboardMarkup([
                [_btn("➕ Top up now", "topup", callback_data="topup")],
                [_btn("⬅️ Menu", "menu", callback_data="menu:home")],
            ]),
        )
        return

    order_id = await _create_order_for(user, p, qty)
    await db.set_order_method(order_id, "Wallet")
    codes = await db.pop_unused_keys(product_id, order_id, qty)
    if codes is None:
        await db.credit_wallet(user.id, price_micro, reason="refund_stockout", ref=str(order_id))  # lost the race for the last key(s): refund
        await _reply(f"{cemoji('warn', '⚠️')} Just sold out — refunded to your wallet.")
        return

    await db.set_order_status(order_id, db.STATUS_APPROVED)
    before_stock = p["stock"]
    # decrement_stock isn't the authoritative gate for a keyed product — the key
    # pool claimed above already is — so a mismatch here is logged, not fatal:
    # the buyer already paid and already has their code(s) by this point.
    if not await db.decrement_stock(product_id, qty):
        logger.warning(
            "decrement_stock(%s, %d) reported insufficient stock after a successful %d-key claim",
            product_id, qty, len(codes),
        )
    order = await db.get_order(order_id)
    codes_block = "\n".join(f"<code>{esc(c)}</code>" for c in codes)
    qty_note = f" ×{qty}" if qty > 1 else ""
    # Explicit empty keyboard (not None) on this one: it's the buyer's only copy
    # of their code(s), and every inline button in this bot navigates by editing
    # the message it's attached to (see render.py) — a "⬅️ Menu" button here
    # would let one tap permanently erase the delivered code(s). Telegram leaves
    # a message's existing keyboard untouched when reply_markup is omitted/None,
    # so clearing the previous screen's buttons (e.g. the payment-method
    # chooser, or "Buy now") needs an explicit empty markup, not just None. The
    # persistent reply-keyboard buttons below the text box still work for
    # navigation without touching this message.
    await _reply(
        f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
        f"{cemoji('party', '🎉')} <b>Order {esc(_order_no(order))} confirmed!</b>\n"
        f"Here's your <b>{render_name(p)}{qty_note}</b>:\n\n{codes_block}\n\n"
        f"<blockquote>{cemoji('pray', '🙏')} Thank you for shopping with us — enjoy!</blockquote>",
        kb=InlineKeyboardMarkup([]),
    )
    after = await db.get_product(product_id)
    if after and after["stock"] == 0 and before_stock is not None and before_stock > 0:
        await _broadcast(context, _announcement_text("out", after))
