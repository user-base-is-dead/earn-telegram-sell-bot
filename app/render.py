"""The only message-rendering primitives — every handler goes through these."""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

from app.formatting import _pad

logger = logging.getLogger(__name__)


async def _send(update: Update, text: str, reply_markup=None) -> None:
    """Reply correctly whether the update came from a command or a button tap."""
    text = _pad(text, reply_markup)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )


async def _edit_or_replace(q, text: str, reply_markup=None, pad: bool = True) -> None:
    """Edit a callback message to `text`. If it isn't text-editable (e.g. it's a
    photo), delete it and send a fresh text message so nothing stacks up.
    The caller is responsible for answering the callback query. Pass pad=False
    to skip the invisible width-spacer line (e.g. for a screen that doesn't
    need the extra forced button-menu width)."""
    if pad:
        text = _pad(text, reply_markup)
    try:
        await q.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return  # already up to date (e.g. a Refresh with no changes)
        try:
            await q.message.delete()
        except Exception:  # noqa: BLE001
            pass
        await q.message.chat.send_message(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )


async def _render(update: Update, text: str, reply_markup=None, pad: bool = True) -> None:
    """Navigation rendering.

    • Button tap -> edit the SAME message in place (nothing piles up).
    • Slash cmd  -> send a fresh message.

    Pass pad=False to skip the invisible width-spacer line.
    """
    q = update.callback_query
    if q is None:
        text = _pad(text, reply_markup) if pad else text
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
        return
    await q.answer()
    await _edit_or_replace(q, text, reply_markup, pad=pad)


async def _edit_admin_msg(query, text: str) -> None:
    """Edit an admin's notification message (photo caption or plain text) in place."""
    try:
        if query.message.photo:
            await query.edit_message_caption(caption=text, parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not edit admin message: %s", e)


async def _mark_notification(context, chat_id: int, message_id: int, text: str) -> None:
    """Edit a stored (chat_id, message_id) admin notification to its final state
    and drop its buttons — used when the original callback query is long gone."""
    try:
        await context.bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id, caption=text,
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text,
                parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            pass
