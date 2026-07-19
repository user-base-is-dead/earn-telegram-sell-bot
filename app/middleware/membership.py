import logging
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ApplicationHandlerStop

from app import config
from app.formatting import cemoji, esc
from app.keyboards import _btn
from app.render import _edit_or_replace, _render
from app.handlers.menu import show_main_menu

logger = logging.getLogger(__name__)

async def check_user_membership(bot, user_id: int) -> bool:
    """Check if the user is a member/owner/admin of ALL required channels."""
    try:
        for channel in config.REQUIRED_CHANNELS:
            m = await bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
            if m.status not in ["creator", "administrator", "member"]:
                return False
        return True
    except Exception as e:
        logger.warning("Error checking membership for user %s: %s", user_id, e)
    return False

async def send_force_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the welcome screen prompting the user to join the channels."""
    text = (
        f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
        f"{cemoji('announce', '📢')} <b>One quick step</b>\n"
        "<i>Join our channel to unlock the store</i>\n"
        "<blockquote>Join below, then tap <b>I've Joined</b> — takes 5 seconds.</blockquote>"
    )
    keyboard = InlineKeyboardMarkup(
        [[_btn(f"📢 Join Channel {i}" if len(config.REQUIRED_CHANNELS) > 1 else "📢 Join Channel",
               "announce", url=f"https://t.me/{c}")]
         for i, c in enumerate(config.REQUIRED_CHANNELS, start=1)]
        + [[_btn("✅ I've Joined", "check", callback_data="verify_join")]]
    )
    await _render(update, text, keyboard)

async def force_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Middleware handler that forces non-admin users to join the channels."""
    if not config.REQUIRED_CHANNELS:
        return

    if not (update.message or update.callback_query):
        return

    user = update.effective_user
    if not user:
        return

    # Admins bypass the check
    if config.is_admin(user.id):
        return

    # Check if this is the callback query for "I Joined" verification:
    if update.callback_query and update.callback_query.data == "verify_join":
        if await check_user_membership(context.bot, user.id):
            await update.callback_query.answer()
            success_text = (
                f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
                f"{cemoji('check', '✅')} <b>Verified! Welcome aboard</b> {cemoji('party', '🎉')}"
            )
            await _edit_or_replace(update.callback_query, success_text)  # 1st message: join-gate -> success
            await show_main_menu(update, context, force_new_message=True)  # 2nd message: the menu, fresh
            raise ApplicationHandlerStop()
        else:
            await update.callback_query.answer("⚠️ Not quite — make sure you've joined, then try again.", show_alert=True)
            raise ApplicationHandlerStop()

    # Verify membership for any other update
    if await check_user_membership(context.bot, user.id):
        return  # Let the update pass to regular handlers

    # If not a member, block and send force join message
    await send_force_join_message(update, context)
    raise ApplicationHandlerStop()
