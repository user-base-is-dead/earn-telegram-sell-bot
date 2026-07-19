"""Admin: broadcast composer.

Compose any number of text messages + photos (edit any of them — the latest
version is used because we re-copy by message id), optionally attach a product
(adds a 🛒 Buy now button on the last message), preview, then send to everyone
who has used the bot. We use bot.copy_message so the admin's exact formatting,
photos and captions are preserved.
"""
import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app import config, db
from app.formatting import cemoji, render_name
from app.handlers.announcements import _bulk_deliver
from app.keyboards import _btn, back_to_menu_kb, buy_now_btn
from app.render import _edit_admin_msg, _edit_or_replace, _send
from app.states import BC_COMPOSE, BC_PRODUCT, BC_REVIEW

logger = logging.getLogger(__name__)


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not config.is_admin(update.effective_user.id):
        await _send(update, f"{cemoji('block', '🚫')} Not authorized.")
        return ConversationHandler.END
    context.user_data["bc_msg_ids"] = []
    context.user_data["bc_from_chat"] = update.effective_chat.id
    context.user_data["bc_product"] = None
    context.user_data["bc_preview_ids"] = []
    kb = InlineKeyboardMarkup([[
        _btn("✅ Done", "check", callback_data="bc:done"),
        _btn("✖️ Abort", "cancel_x", callback_data="bc:abort"),
    ]])
    await _send(
        update,
        f"{cemoji('announce', '📢')} <b>Broadcast composer</b>\n\n"
        "Send any number of <b>text messages</b> and <b>photos</b> now. "
        "You can edit any of them — the latest version is used. "
        f"Tap <b>{cemoji('check', '✅')} Done</b> when finished, or "
        f"<b>{cemoji('cancel_x', '✖️')} Abort</b> to cancel.",
        kb,
    )
    return BC_COMPOSE


async def bc_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Remember each composed message id (dedup so edits don't double-add)."""
    m = update.effective_message
    if m:
        ids = context.user_data.setdefault("bc_msg_ids", [])
        if m.message_id not in ids:
            ids.append(m.message_id)
    return BC_COMPOSE


async def _bc_product_kb() -> InlineKeyboardMarkup:
    rows = [
        [_btn(f"🛒 {p['name'][:58]}", "buy", callback_data=f"bc:pick:{p['id']}")]
        for p in await db.list_products(only_active=True, include_out_of_stock=False)
    ]
    rows.append([_btn("⏭ Skip (no product)", "skip", callback_data="bc:skip")])
    return InlineKeyboardMarkup(rows)


async def bc_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.user_data.get("bc_msg_ids"):
        if update.callback_query:
            await update.callback_query.answer("✍️ Send something to broadcast first.", show_alert=True)
        else:
            await _send(update, f"{cemoji('pencil', '✍️')} Send something to broadcast first.")
        return BC_COMPOSE
    await _send(
        update,
        f"{cemoji('buy', '🛒')} Attach a product? Buyers will get a <b>Buy now</b> button to its payment. Or skip.",
        await _bc_product_kb(),
    )
    return BC_PRODUCT


async def bc_abort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in ("bc_msg_ids", "bc_from_chat", "bc_product", "bc_preview_ids"):
        context.user_data.pop(k, None)
    q = update.callback_query
    if q:
        await q.answer()
        await _edit_or_replace(q, f"{cemoji('block', '🚫')} Broadcast cancelled.", back_to_menu_kb())
    else:
        await _send(update, f"{cemoji('block', '🚫')} Broadcast cancelled.", back_to_menu_kb())
    return ConversationHandler.END


async def bc_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":")[2])
    p = await db.get_product(pid)
    if not p:
        await q.answer("⚠️ Product not found.", show_alert=True)
        return BC_PRODUCT
    context.user_data["bc_product"] = pid
    kb = InlineKeyboardMarkup([
        [_btn("✅ Continue", "check", callback_data="bc:review")],
        [
            _btn("🔄 Re-choose", "refresh", callback_data="bc:rechoose"),
            _btn("⏭ Skip", "skip", callback_data="bc:skip"),
        ],
    ])
    await _edit_or_replace(q, f"{cemoji('check', '✅')} Selected: <b>{render_name(p)}</b>\n\nContinue to review?", kb)
    return BC_PRODUCT


async def bc_rechoose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["bc_product"] = None
    await _edit_or_replace(q, f"{cemoji('buy', '🛒')} Choose a product, or skip:", await _bc_product_kb())
    return BC_PRODUCT


async def bc_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["bc_product"] = None
    return await bc_review(update, context)


async def _replay(bot, to_chat: int, from_chat: int, msg_ids: list, product_id) -> list:
    """Copy the composed messages (in order) to `to_chat`; the 🛒 Buy now button
    rides on the last message. Returns the new message ids."""
    buy_markup = None
    if product_id:
        prod = await db.get_product(product_id)
        if prod and prod["active"]:
            stock = await db.get_effective_stock(product_id, prod["stock"])
            if stock == db.UNLIMITED_STOCK or stock > 0:
                buy_markup = InlineKeyboardMarkup([[buy_now_btn(prod)]])
    out = []
    last = len(msg_ids) - 1
    for i, mid in enumerate(msg_ids):
        markup = buy_markup if i == last else None
        res = await bot.copy_message(
            chat_id=to_chat, from_chat_id=from_chat, message_id=mid, reply_markup=markup
        )
        out.append(res.message_id)
    return out


async def bc_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer()
    chat_id = update.effective_chat.id
    from_chat = context.user_data.get("bc_from_chat", chat_id)
    msg_ids = context.user_data.get("bc_msg_ids", [])
    product_id = context.user_data.get("bc_product")
    try:
        preview_ids = await _replay(context.bot, chat_id, from_chat, msg_ids, product_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Broadcast preview failed: %s", e)
        await _send(update, f"{cemoji('warn', '⚠️')} Couldn't build the preview. Run /broadcast again.")
        return ConversationHandler.END
    context.user_data["bc_preview_ids"] = preview_ids
    n = len([uid for uid in await db.all_recipient_ids() if not config.is_admin(uid)])
    kb = InlineKeyboardMarkup([[
        _btn("✅ Send", "check", callback_data="bc:send"),
        _btn("✖️ Abort", "cancel_x", callback_data="bc:abort"),
    ]])
    await context.bot.send_message(
        chat_id,
        f"{cemoji('point_up', '👆')} This is exactly how it will be sent to <b>{n}</b> user(s).",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )
    return BC_REVIEW


async def bc_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    msg_ids = context.user_data.get("bc_msg_ids", [])
    from_chat = context.user_data.get("bc_from_chat", q.message.chat_id)
    product_id = context.user_data.get("bc_product")
    if not msg_ids:
        await _edit_admin_msg(q, f"{cemoji('warn', '⚠️')} Nothing to send.")
        return ConversationHandler.END
    await _edit_admin_msg(q, f"{cemoji('megaphone', '📣')} Sending broadcast…")
    recipients = [uid for uid in await db.all_recipient_ids() if not config.is_admin(uid)]

    async def send_one(uid: int) -> None:
        await _replay(context.bot, uid, from_chat, msg_ids, product_id)

    sent, failed = await _bulk_deliver(recipients, send_one)
    # Delete the admin's preview messages; keep only the confirmation.
    for mid in context.user_data.get("bc_preview_ids", []):
        try:
            await context.bot.delete_message(q.message.chat_id, mid)
        except Exception:  # noqa: BLE001
            pass
    tail = f" ({failed} couldn't be reached)" if failed else ""
    await _edit_admin_msg(q, f"{cemoji('check', '✅')} Broadcast sent to <b>{sent}</b> user(s){tail}.")
    for k in ("bc_msg_ids", "bc_from_chat", "bc_product", "bc_preview_ids"):
        context.user_data.pop(k, None)
    return ConversationHandler.END
