"""Main menu, help, navigation routing, and admin command-hint bootstrapping."""
from telegram import BotCommandScopeChat, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from app import config, db
from app.formatting import cemoji, esc
from app.keyboards import (
    BTN_BROWSE, BTN_EARNINGS, BTN_HELP, BTN_MYORDERS, BTN_ORDERS, BTN_PRODUCTS,
    BTN_SUPPORT, BTN_USERS, _btn, main_menu_keyboard,
)
from app.render import _render

# Slash-command hints shown in Telegram's "/" menu.
CUSTOMER_CMDS = [
    ("start", "Open the store"),
    ("myorders", "View my orders"),
    ("help", "How to buy / admin guide"),
]
ADMIN_CMDS = CUSTOMER_CMDS + [
    ("products", "List / manage products"),
    ("orders", "View recent orders"),
    ("users", "View all users"),
    ("addproduct", "Add a new product"),
    ("broadcast", "Compose a broadcast"),
    ("mode", "Toggle auto/manual payment mode"),
    ("cleanorderdatabase", "Delete successful orders"),
    ("cleanabandonedorders", "Delete abandoned carts"),
    ("cleandatabase", "Delete all orders + products"),
]


async def _ensure_admin_commands(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Set the admin '/' command hints for an admin's own chat, once per bot run.

    Called when an admin opens the menu, so the admin commands appear as soon as
    they interact — no bot restart needed (the startup pass only covers admins
    whose chat already existed)."""
    if not config.is_admin(user_id):
        return
    done = context.bot_data.setdefault("admin_cmds_set", set())
    if user_id in done:
        return
    try:
        await context.bot.set_my_commands(
            ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=user_id)
        )
        done.add(user_id)
    except Exception as err:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("Could not set admin commands for %s: %s", user_id, err)


async def refresh_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fixcmds — force-set the admin '/' commands for this chat and report the
    result right in Telegram, so we can tell a failing API call apart from a
    stale client-side command cache."""
    user = update.effective_user
    if not config.is_admin(user.id):
        await update.message.reply_text(
            f"{cemoji('block', '🚫')} Not authorized.", parse_mode=ParseMode.HTML
        )
        return
    try:
        await context.bot.set_my_commands(
            ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=user.id)
        )
        context.bot_data.setdefault("admin_cmds_set", set()).add(user.id)
        await update.message.reply_text(
            f"{cemoji('check', '✅')} <b>Admin commands set</b> for this chat.\n\n"
            "If the <b>/</b> menu still shows only 3 commands, it's Telegram's "
            "cache — fully close &amp; reopen Telegram (or check on the mobile app).",
            parse_mode=ParseMode.HTML,
        )
    except Exception as err:  # noqa: BLE001
        await update.message.reply_text(
            f"{cemoji('warn', '⚠️')} <b>Failed to set commands:</b>\n<code>{esc(err)}</code>",
            parse_mode=ParseMode.HTML)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message: bool = False) -> None:
    """Welcome screen shown on /start and the ⬅️ Menu button.

    Pass force_new_message=True to always send a fresh message even from a
    callback-query context, instead of editing that message in place — used
    right after force-join verification, so the success confirmation and this
    menu land as two separate messages rather than one screen overwriting the
    other."""
    user = update.effective_user
    await _ensure_admin_commands(context, user.id)
    text = (
        f"{cemoji('star', '🌟')} <b>{esc(config.STORE_NAME)}</b> {cemoji('star', '🌟')}\n"
        "<i>Premium digital goods, delivered instantly.</i>\n\n"
        f"{cemoji('wave', '👋')} Welcome, <b>{esc(user.first_name)}</b>!\n\n"
        "<blockquote>"
        f"{cemoji('cart', '🛒')} <b>Browse</b> a curated catalog, available <b>24/7</b>\n"
        f"{cemoji('bolt', '⚡')} <b>Instant delivery</b> the moment payment is confirmed\n"
        f"{cemoji('key', '🔑')} <b>No hassle</b> — just tap, pay, and receive"
        "</blockquote>\n\n"
        f"{cemoji('point_down_start', '👇')} Pick an option below to get started"
    )
    if update.callback_query and not force_new_message:
        # ⬅️ Menu inline button: edit in place.
        await _render(update, text, main_menu_keyboard(user.id), pad=False)
    else:
        # /start, or force_new_message: send a fresh message + the full inline menu.
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(user.id),
        )


async def support_not_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main-menu 'Contact support' tap when no SUPPORT_USERNAME is configured."""
    await update.callback_query.answer(
        "Customer support isn't set up yet.", show_alert=True
    )


def _help_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    support = config.support_url()
    if support and not is_admin:
        rows.append([_btn("💬 Customer support", "support", url=support)])
    rows.append([_btn("⬅️ Menu", "menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


_MODE_INFO = {
    db.STORE_MODE_AUTO: (
        "AUTO",
        "Buyers can only pay via wallet balance. Purchases deliver instantly with no review.",
    ),
    db.STORE_MODE_MANUAL: (
        "MANUAL",
        "Buyers pay via Binance Pay / Crypto and you review + deliver each order.",
    ),
}


def _mode_text(mode: str) -> str:
    label, desc = _MODE_INFO[mode]
    return f"{cemoji('bolt', '⚡')} <b>Current mode: {esc(label)}</b>\n{esc(desc)}"


def _mode_keyboard(mode: str) -> InlineKeyboardMarkup:
    other = db.STORE_MODE_MANUAL if mode == db.STORE_MODE_AUTO else db.STORE_MODE_AUTO
    other_label = _MODE_INFO[other][0]
    return InlineKeyboardMarkup(
        [[_btn(f"Switch to {other_label}", None, callback_data=f"mode:set:{other}")]]
    )


async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mode — admin-only. Shows the current store mode with one button to flip it."""
    if not config.is_admin(update.effective_user.id):
        await update.message.reply_text(
            f"{cemoji('block', '🚫')} Not authorized.", parse_mode=ParseMode.HTML
        )
        return
    mode = config.STORE_MODE
    await update.message.reply_text(
        _mode_text(mode), parse_mode=ParseMode.HTML, reply_markup=_mode_keyboard(mode)
    )


async def mode_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button from /mode — flips the store mode and edits the message in place."""
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    await query.answer()
    new_mode = query.data.split(":", 2)[2]
    await db.set_store_mode(new_mode)
    await query.edit_message_text(
        _mode_text(new_mode), parse_mode=ParseMode.HTML, reply_markup=_mode_keyboard(new_mode)
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    auto = config.is_auto_mode()
    # Only advertise the payment methods actually enabled in config — never
    # hardcode "Binance Pay / Crypto", or a buyer is told about options they
    # can't use (e.g. Binance Pay while only a crypto address is set).
    _enabled = []
    if config.binance_pay_enabled():
        _enabled.append("Binance Pay")
    if config.blockchain_enabled():
        _enabled.append("Crypto")
    pay_methods = " / ".join(_enabled) or "Crypto"
    lines = [
        f"{cemoji('star', '🌟')} <b>{esc(config.STORE_NAME)}</b>\n",
        f"{cemoji('info', 'ℹ️')} <b>How to buy</b>",
    ]
    if auto:
        lines += [
            "<i>Pay from your wallet balance — every purchase delivers instantly.</i>",
            (
                "<blockquote>"
                f"{cemoji('step1', '1️⃣')} Tap <b>{cemoji('cart', '🛍')} Browse products</b>\n"
                f"{cemoji('step2', '2️⃣')} Top up your wallet once — auto-credited in ~1 min\n"
                f"{cemoji('step3', '3️⃣')} Pick a product, then <b>{cemoji('cart', '🛒')} Buy now</b> "
                "and pay from your balance\n"
                f"{cemoji('step4', '4️⃣')} Your product is delivered <b>instantly</b> — no waiting, "
                "no review"
                "</blockquote>"
            ),
            "",
        ]
    else:
        lines += [
            "<i>No account needed — pick whichever payment method suits you.</i>",
            (
                "<blockquote>"
                f"{cemoji('step1', '1️⃣')} Tap <b>{cemoji('cart', '🛍')} Browse products</b>\n"
                f"{cemoji('step2', '2️⃣')} Pick a product, then <b>{cemoji('cart', '🛒')} Buy now</b>\n"
                f"{cemoji('step3', '3️⃣')} Choose {pay_methods}\n"
                f"{cemoji('step4', '4️⃣')} Pay the <b>exact amount</b>, tap "
                f"<b>{cemoji('check', '✅')} I've Paid</b>, and send your UTR / TxID / screenshot\n"
                f"{cemoji('step5', '5️⃣')} Your product is delivered here once we review your payment"
                "</blockquote>"
            ),
            "",
        ]
    lines += [
        f"{cemoji('receipt', '🧾')} <b>My orders</b> — track your orders &amp; status\n"
        f"{cemoji('chat', '💬')} <b>Customer support</b> — message us directly",
        "",
        "<i>Commands also work:</i> /start /myorders /help",
    ]
    if config.is_admin(update.effective_user.id):
        lines += [
            "",
            f"{cemoji('star', '🌟')} <b>Admin tools</b>",
            (
                "<blockquote>"
                f"{cemoji('plus', '➕')} <b>Add product</b> — add something to sell\n"
                f"{cemoji('restock', '📦')} <b>Products</b> — list / manage everything with stock\n"
                f"{cemoji('receipt', '🧾')} <b>All orders</b> — recent orders &amp; status\n"
                f"{cemoji('people', '👥')} <b>Users</b> — view everyone who used the bot\n"
                f"{cemoji('bolt', '⚡')} /mode — toggle auto (wallet-only) / manual payment mode"
                "</blockquote>"
            ),
            "",
            f"{cemoji('warn', '⚠️')} <b>Cleanup</b> <i>(destructive — use with care)</i>",
            (
                "<blockquote>"
                f"{cemoji('broom', '🧹')} /cleanorderdatabase — delete successful orders\n"
                f"{cemoji('broom', '🧹')} /cleanabandonedorders — delete abandoned carts\n"
                f"{cemoji('broom', '🧹')} /cleandatabase — delete <b>all</b> orders + products"
                "</blockquote>"
            ),
            "",
            "<i>Commands also work:</i> /addproduct /products /orders /users /mode",
        ]
    await _render(
        update, "\n".join(lines),
        _help_keyboard(config.is_admin(update.effective_user.id)),
        pad=False,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        f"{cemoji('cancel_x', '✖️')} <b>Cancelled.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[_btn("⬅️ Menu", "menu", callback_data="menu:home")]]),
    )
    return ConversationHandler.END


async def cancel_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fallback: leave any active flow and show the main menu.

    Lets /start and the ⬅️ Menu button always get the user un-stuck.
    """
    context.user_data.clear()
    await show_main_menu(update, context)
    return ConversationHandler.END


def _ender(handler):
    """Wrap a navigation handler as a conversation fallback: clear the active
    flow's state, run the navigation, and END the conversation.

    This stops a half-finished flow (e.g. add-product left mid-way) from
    capturing input that was meant for another flow (e.g. editing a product).
    """
    async def _fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.clear()
        await handler(update, context)
        return ConversationHandler.END

    return _fallback


async def _nav_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a persistent reply-keyboard button tap to the right screen."""
    from app.handlers.catalog import show_catalog
    from app.handlers.orders_admin import admin_orders, show_earnings, show_my_orders, show_users
    from app.handlers.products_admin import admin_products

    text = (update.message.text or "").strip()
    if text == BTN_BROWSE:
        await show_catalog(update, context)
    elif text == BTN_MYORDERS:
        await show_my_orders(update, context)
    elif text == BTN_PRODUCTS:
        await admin_products(update, context)
    elif text == BTN_ORDERS:
        await admin_orders(update, context)
    elif text == BTN_USERS:
        await show_users(update, context)
    elif text == BTN_EARNINGS:
        await show_earnings(update, context)
    elif text == BTN_SUPPORT:
        support = config.support_url()
        if support:
            await update.message.reply_text(
                f"{cemoji('chat', '💬')} <b>Customer support</b> — tap below to chat with us.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[_btn("💬 Chat with us", "chat", url=support)]]
                ),
            )
        else:
            await update.message.reply_text(
                f"{cemoji('chat', '💬')} Customer support isn't set up yet.",
                parse_mode=ParseMode.HTML,
            )
    else:  # BTN_HELP
        await help_cmd(update, context)


async def emoji_id_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin utility: forward/send a message containing a custom emoji and get its
    custom_emoji_id back, for pasting into CUSTOM_EMOJI_IDS. Only ever matches
    messages that actually carry a custom_emoji entity (see the filters.Entity
    registration in build_application()), so it never intercepts normal text."""
    msg = update.effective_message
    # parse_entities/parse_caption_entities (not the raw .entities offsets) are used
    # because entity offsets are UTF-16 code units, not Python string indices —
    # slicing by hand would misalign on emoji outside the Basic Multilingual Plane.
    found = {**msg.parse_entities(["custom_emoji"]), **msg.parse_caption_entities(["custom_emoji"])}
    if not found:
        return
    lines = [f"{char} → <code>{esc(entity.custom_emoji_id)}</code>" for entity, char in found.items()]
    await msg.reply_text(
        f"{cemoji('search', '🔎')} <b>Custom emoji ID(s) found:</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )
