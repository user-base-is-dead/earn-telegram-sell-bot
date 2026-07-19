"""Admin: /cleandatabase (orders + products) and /cleanorderdatabase (orders only).
Each is confirmed by typing an exact phrase. No backup is taken, so that typed
confirmation is the only safety net."""
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app import config, db
from app.formatting import cemoji, esc
from app.render import _send
from app.states import CLEAN_CONFIRM

CLEAN_PHRASES = {
    "orders": "DELETE ORDERS",
    "abandoned": "DELETE ABANDONED",
    "all": "DELETE EVERYTHING",
}


async def _clean_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, scope: str
) -> int:
    if not config.is_admin(update.effective_user.id):
        await _send(update, f"{cemoji('block', '🚫')} Not authorized.")
        return ConversationHandler.END
    context.user_data["clean_scope"] = scope
    phrase = CLEAN_PHRASES[scope]
    if scope == "all":
        title, what = "Clean database", "ALL orders AND ALL products"
    elif scope == "abandoned":
        title, what = "Clean abandoned orders", "all <b>abandoned</b> orders (unpaid carts)"
    else:
        title, what = "Clean order database", "all <b>successful</b> orders (approved / rejected / pending) — products are kept, abandoned carts are kept"
    await _send(
        update,
        f"{cemoji('broom', '🧹')} <b>{title}</b>\n\n"
        f"{cemoji('warn', '⚠️')} This permanently deletes <b>{what}</b>. There is <b>no backup</b> — "
        "it cannot be undone.\n\n"
        f"To confirm, type this exactly:\n<code>{esc(phrase)}</code>\n\n"
        "Anything else cancels.",
    )
    return CLEAN_CONFIRM


async def cleandatabase_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _clean_prompt(update, context, "all")


async def cleanorderdatabase_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _clean_prompt(update, context, "orders")


async def cleanabandonedorders_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _clean_prompt(update, context, "abandoned")


async def clean_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = context.user_data.pop("clean_scope", None)
    if not choice:
        return ConversationHandler.END
    if update.message.text.strip() != CLEAN_PHRASES.get(choice):
        await update.message.reply_text(
            f"{cemoji('reject', '❌')} Text didn't match — nothing was deleted.", parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END
    if choice == "abandoned":
        n = await db.clear_abandoned_orders()
        msg = f"{cemoji('check', '✅')} Done. Deleted <b>{n}</b> abandoned order(s)."
    elif choice == "all":
        counts = await db.clear_data(wipe_products=True)
        msg = (
            f"{cemoji('check', '✅')} Done. Deleted <b>{counts['orders']}</b> order(s) and "
            f"<b>{counts['products']}</b> product(s).\n"
            "Numbering reset — the next order and product will be #1."
        )
    else:  # "orders" = successful only
        n = await db.clear_successful_orders()
        msg = f"{cemoji('check', '✅')} Done. Deleted <b>{n}</b> successful order(s). Abandoned carts untouched."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    return ConversationHandler.END
