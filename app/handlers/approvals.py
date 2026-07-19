"""Admin: approve / reject a pending order + manual delivery."""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from app import config, db
from app.formatting import _order_no, cemoji, esc, qty_suffix
from app.handlers.announcements import _announcement_text, _broadcast
from app.handlers.payments import order_review_text
from app.keyboards import REJECT_PRESETS, cancel_kb, reason_keyboard, review_keyboard
from app.render import _edit_admin_msg, _mark_notification
from app.states import APPROVE_DELIVER, REJECT_REASON

logger = logging.getLogger(__name__)


async def approve_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin tapped Approve. Ask what to send, then deliver it manually."""
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    order_id = int(query.data.split(":", 1)[1])
    order = await db.get_order(order_id)
    if not order:
        await _edit_admin_msg(query, f"{cemoji('warn', '⚠️')} Order not found.")
        return ConversationHandler.END
    if order["status"] != db.STATUS_PENDING:
        await _edit_admin_msg(
            query,
            f"{cemoji('info', 'ℹ️')} Order {esc(_order_no(order))} already <b>{esc(order['status'])}</b>.",
        )
        return ConversationHandler.END

    # If another approval is already in progress, warn and do not overwrite it.
    prev_id = context.user_data.get("approve_order_id")
    if prev_id is not None and prev_id != order_id:
        prev_order = await db.get_order(prev_id)
        prev_ref = _order_no(prev_order) if prev_order else f"#{prev_id}"
        await context.bot.send_message(
            update.effective_chat.id,
            f"{cemoji('warn', '⚠️')} You are already approving Order <b>{esc(prev_ref)}</b>.\n\n"
            "Cancel that first, then tap Approve again on this order.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb(),
        )
        return ConversationHandler.END

    msg_ref = (query.message.chat_id, query.message.message_id)
    context.user_data["approve_order_id"] = order_id
    context.user_data["approve_msg"] = msg_ref
    await context.bot.send_message(
        update.effective_chat.id,
        f"{cemoji('check', '✅')} Approving Order <b>{esc(_order_no(order))}</b>.\n"
        "Now send the <b>content to deliver</b> to the buyer — a message, or a "
        "<b>file / photo</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_kb(),
    )
    return APPROVE_DELIVER


async def approve_deliver_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data.get("approve_order_id")
    msg_ref = context.user_data.get("approve_msg")
    if order_id is None:
        return ConversationHandler.END
    await _finalize_approve(update, context, order_id, msg_ref, text=update.message.text)
    return ConversationHandler.END


async def approve_deliver_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data.get("approve_order_id")
    msg_ref = context.user_data.get("approve_msg")
    if order_id is None:
        return ConversationHandler.END
    m = update.message
    if m.photo:
        await _finalize_approve(
            update, context, order_id, msg_ref, photo=m.photo[-1].file_id, caption=m.caption
        )
    elif m.document:
        await _finalize_approve(
            update, context, order_id, msg_ref, document=m.document.file_id, caption=m.caption
        )
    return ConversationHandler.END


async def _finalize_approve(
    update, context, order_id, msg_ref, text=None, photo=None, document=None,
    caption=None, via_query=None,
) -> None:
    context.user_data.pop("approve_order_id", None)
    context.user_data.pop("approve_msg", None)
    order = await db.get_order(order_id)
    if not order or order["status"] != db.STATUS_PENDING:
        if update.message:
            await update.message.reply_text(
                f"{cemoji('info', 'ℹ️')} That order was already processed.",
                parse_mode=ParseMode.HTML,
            )
        return

    # Remember stock BEFORE this approval so we can tell whether THIS order is the
    # one that sells the product out (a >0 -> 0 transition). decrement_stock()
    # clamps at 0, so without this every later approval of an already sold-out
    # product would still see stock == 0 and re-announce it.
    before = await db.get_product(order["product_id"])
    before_stock = before["stock"] if before else None

    # Compare-and-swap: only proceed if the order is still PENDING. Guards
    # against two admins approving (or an approve racing a reject on) the
    # same order between the check above and this write.
    if not await db.set_order_status(order_id, db.STATUS_APPROVED, expected_status=db.STATUS_PENDING):
        if update.message:
            await update.message.reply_text(
                f"{cemoji('info', 'ℹ️')} That order was already processed.",
                parse_mode=ParseMode.HTML,
            )
        return
    qty = order["qty"] if "qty" in order.keys() else 1
    if not await db.decrement_stock(order["product_id"], qty):
        # Not enough stock left to actually cover this order (e.g. two manual
        # orders queued for the last few units) — undo the approval instead of
        # delivering something that oversells the product.
        await db.set_order_status(order_id, db.STATUS_PENDING)
        if update.message:
            await update.message.reply_text(
                f"{cemoji('warn', '⚠️')} Not enough stock left to cover this order (needs {qty}). "
                "Approval cancelled — the order is back in the pending queue.",
                parse_mode=ParseMode.HTML,
            )
        return

    buyer = order["user_id"]
    header = (
        f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
        f"{cemoji('party', '🎉')} <b>Order {esc(_order_no(order))} confirmed!</b>\n"
        f"Here's your <b>{esc(order['product_name'])}{qty_suffix(order)}</b>:"
    )
    extra = f"\n\n{esc(caption)}" if caption else ""
    thanks = f"\n\n<blockquote>{cemoji('pray', '🙏')} Thank you for shopping with us — enjoy!</blockquote>"
    try:
        if photo:
            await context.bot.send_photo(buyer, photo, caption=header + extra, parse_mode=ParseMode.HTML)
        elif document:
            await context.bot.send_document(buyer, document, caption=header + extra, parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(
                buyer, f"{header}\n\n{esc(text)}{thanks}",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not deliver to buyer %s: %s", buyer, e)
        if update.message:
            await update.message.reply_text(f"{cemoji('warn', '⚠️')} Approved, but could not message the buyer.",
                                            parse_mode=ParseMode.HTML)

    final = order_review_text(order) + f"\n\n{cemoji('check', '✅')} <b>APPROVED &amp; delivered.</b>"
    if via_query is not None:
        await _edit_admin_msg(via_query, final)
    elif msg_ref:
        await _mark_notification(context, msg_ref[0], msg_ref[1], final)

    if update.message:
        await update.message.reply_text(f"{cemoji('check', '✅')} Delivered to the buyer.",
                                        parse_mode=ParseMode.HTML)

    # Announce "Sold out" only once — when THIS approval brought a tracked product
    # from in-stock down to 0. Later approvals of an already sold-out product
    # (before_stock == 0) must NOT re-broadcast it.
    after = await db.get_product(order["product_id"])
    if (
        after
        and after["stock"] == 0
        and before_stock is not None
        and before_stock > 0
    ):
        await _broadcast(context, _announcement_text("out", after))


async def reject_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin tapped Reject -> show a reason picker (presets + custom)."""
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    order_id = int(query.data.split(":", 1)[1])
    order = await db.get_order(order_id)
    if not order:
        await _edit_admin_msg(query, f"{cemoji('warn', '⚠️')} Order not found.")
        return ConversationHandler.END
    if order["status"] != db.STATUS_PENDING:
        await _edit_admin_msg(
            query,
            f"{cemoji('info', 'ℹ️')} Order {esc(_order_no(order))} already <b>{esc(order['status'])}</b>.",
        )
        return ConversationHandler.END

    # If another rejection is already in progress, warn and do not overwrite it
    # (mirrors the same guard in approve_start).
    prev_id = context.user_data.get("reject_order_id")
    if prev_id is not None and prev_id != order_id:
        prev_order = await db.get_order(prev_id)
        prev_ref = _order_no(prev_order) if prev_order else f"#{prev_id}"
        await context.bot.send_message(
            update.effective_chat.id,
            f"{cemoji('warn', '⚠️')} You are already rejecting Order <b>{esc(prev_ref)}</b>.\n\n"
            "Cancel that first, then tap Reject again on this order.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb(),
        )
        return ConversationHandler.END

    context.user_data["reject_order_id"] = order_id
    context.user_data["reject_msg"] = (query.message.chat_id, query.message.message_id)
    # Swap the Approve/Reject buttons on the notification for a reason picker.
    try:
        await query.edit_message_reply_markup(reply_markup=reason_keyboard())
    except BadRequest:
        pass
    return REJECT_REASON


async def reject_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]

    if choice == "cancel":
        order_id = context.user_data.pop("reject_order_id", None)
        context.user_data.pop("reject_msg", None)
        if order_id is not None:
            try:  # restore the Approve/Reject buttons
                await query.edit_message_reply_markup(
                    reply_markup=review_keyboard(order_id)
                )
            except BadRequest:
                pass
        return ConversationHandler.END

    if choice == "other":
        await context.bot.send_message(
            update.effective_chat.id,
            f"{cemoji('pencil', '✍️')} Send the rejection reason as a message (the buyer will see it).",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb(),
        )
        return REJECT_REASON  # keep waiting — now for typed text

    await _finalize_reject(
        update, context, REJECT_PRESETS.get(choice, "Payment not verified.")
    )
    return ConversationHandler.END


async def reject_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _finalize_reject(update, context, update.message.text.strip())
    return ConversationHandler.END


async def _finalize_reject(
    update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str
) -> None:
    order_id = context.user_data.pop("reject_order_id", None)
    msg_ref = context.user_data.pop("reject_msg", None)
    if order_id is None:
        return
    order = await db.get_order(order_id)
    if not order or order["status"] != db.STATUS_PENDING:
        if update.message:
            await update.message.reply_text(
                f"{cemoji('info', 'ℹ️')} That order was already processed.",
                parse_mode=ParseMode.HTML,
            )
        return

    # Compare-and-swap, same guard as approval: don't reject an order another
    # admin action already claimed between the check above and this write.
    if not await db.reject_order(order_id, reason, expected_status=db.STATUS_PENDING):
        if update.message:
            await update.message.reply_text(
                f"{cemoji('info', 'ℹ️')} That order was already processed.",
                parse_mode=ParseMode.HTML,
            )
        return
    order = await db.get_order(order_id)

    # Tell the buyer, with the reason.
    try:
        await context.bot.send_message(
            order["user_id"],
            f"{cemoji('reject', '❌')} Your <b>Order {esc(_order_no(order))}</b> was not approved.\n\n"
            f"<b>Reason:</b> {esc(reason)}\n\n"
            "If a refund is due, the seller will process it.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not notify buyer %s: %s", order["user_id"], e)

    # Mark the original admin notification as rejected (with the reason).
    final = order_review_text(order) + f"\n\n{cemoji('reject', '❌')} <b>REJECTED.</b>\nReason: {esc(reason)}"
    if msg_ref:
        await _mark_notification(context, msg_ref[0], msg_ref[1], final)

    # Confirm to the admin who typed a custom reason.
    if update.message:
        await update.message.reply_text(
            f"{cemoji('reject', '❌')} Rejected. Reason sent to the buyer:\n{esc(reason)}",
            parse_mode=ParseMode.HTML,
        )
