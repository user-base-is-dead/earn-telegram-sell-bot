"""Composition root: builds the Application, wires every handler, and runs it."""
import asyncio
import logging
import warnings
from datetime import time as dt_time

from telegram import Update
from telegram.error import BadRequest, InvalidToken, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from app import config, db
from app.handlers import announcements, approvals, broadcast, catalog, clean, menu, orders_admin, payments, products_admin, topup
from app.keyboards import BTN_ADD, BTN_BROADCAST, BTN_TOPUP, NAV_FILTER, NAV_NAV
from app.services import crypto_watch
from app.states import (
    ADD_DESC, ADD_ICON, ADD_NAME, ADD_PRICE, ADD_PRICE_INR, ADD_STOCK, APPROVE_DELIVER,
    BC_COMPOSE, BC_PRODUCT, BC_REVIEW, BUY_QTY, CLEAN_CONFIRM, DISCOUNT_DURATION,
    DISCOUNT_PRICE, DISCOUNT_PRICE_INR, EDIT_PRICE_INR, EDIT_VALUE, KEYS_INPUT,
    PAY_UTR, REJECT_REASON, TOPUP_AMOUNT, TOPUP_CHECK,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx logs the full API URL (which contains the bot token) on every request.
# Keep it at WARNING so the token never lands in logs and the console stays clean.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Several ConversationHandlers below intentionally mix CallbackQueryHandler entry
# points with MessageHandler states (tap a button, then type/upload something), so
# per_message must stay False to track the conversation per user+chat. Silence
# PTB's benign "CallbackQueryHandler will not be tracked for every message" notice
# so startup output stays clean. Genuine PTB warnings are still shown.
warnings.filterwarnings(
    "ignore",
    message=r"If 'per_message=False'",
    category=PTBUserWarning,
)


# --------------------------------------------------------------------------- #
# Error handler & bootstrap
# --------------------------------------------------------------------------- #
# Show the DNS/proxy guidance only once so transient blips don't spam the log.
_network_hint_shown = False


async def on_error(update: object, context) -> None:
    err = context.error
    msg = str(err).lower()
    # Benign: a stale button tap (query expired) or a no-op edit — nothing we can do.
    if any(s in msg for s in ("query is too old", "query id is invalid", "message is not modified")):
        logger.debug("Ignoring benign Telegram error: %s", err)
        return
    # Transient connectivity (DNS hiccup, dropped connection, timeout). The poller
    # retries these on its own, so log a quiet WARNING instead of a scary ERROR.
    # NOTE: BadRequest subclasses NetworkError in PTB, so exclude it — those are
    # real bugs we still want at ERROR level.
    transient_markers = (
        "name resolution", "temporary failure", "try again",
        "timed out", "timeout", "connection", "connecterror",
        "readerror", "read error", "network", "httpx",
    )
    is_transient = (
        isinstance(err, NetworkError) and not isinstance(err, BadRequest)
    ) or any(s in msg for s in transient_markers)
    if is_transient:
        logger.warning("Transient network issue reaching Telegram (auto-retrying): %s", err)
        global _network_hint_shown
        if not _network_hint_shown and ("name resolution" in msg or "temporary failure" in msg):
            _network_hint_shown = True
            logger.warning(
                "↳ Repeated DNS 'Temporary failure in name resolution' means the HOST "
                "can't resolve api.telegram.org reliably. Fix on the server: set stable "
                "DNS (1.1.1.1 / 8.8.8.8), or set TELEGRAM_PROXY in .env if Telegram is "
                "blocked there."
            )
        return
    logger.error("Update %s caused error: %s", update, err)


async def _post_init(app: Application) -> None:
    """Register the slash-command hints shown in Telegram's '/' menu.

    Customers see only the public commands. Each admin additionally sees the
    admin commands, scoped to their own private chat so regular users never see
    them. Admins whose chat doesn't exist yet get theirs set on their next /start
    via menu._ensure_admin_commands.
    """
    await db.init_pool()

    # Default scope: what every user sees.
    await app.bot.set_my_commands(menu.CUSTOMER_CMDS)

    # Per-admin scope: customer + admin commands, visible only in that admin's chat.
    from telegram import BotCommandScopeChat
    for admin_id in config.ADMIN_IDS:
        try:
            await app.bot.set_my_commands(
                menu.ADMIN_CMDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except BadRequest as err:
            # Most likely the admin hasn't started the bot yet, so Telegram has no
            # chat for them. They'll get the admin menu on their next /start.
            logger.warning("Could not set admin commands for %s: %s", admin_id, err)


async def _post_shutdown(app: Application) -> None:
    await db.close_pool()


def build_application() -> Application:
    # Generous timeouts so a slow link doesn't time out instantly, plus an optional
    # proxy (set TELEGRAM_PROXY in .env) for networks that block api.telegram.org.
    builder = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .connect_timeout(config.CONNECT_TIMEOUT)
        .read_timeout(20.0)
        .write_timeout(20.0)
        .pool_timeout(20.0)
        # Big enough to run a whole broadcast wave concurrently (default is 1),
        # with headroom for normal handler replies happening at the same time.
        .connection_pool_size(announcements.BROADCAST_BATCH + 8)
        .get_updates_connect_timeout(config.CONNECT_TIMEOUT)
        .get_updates_read_timeout(30.0)
        .get_updates_write_timeout(20.0)
        .get_updates_pool_timeout(20.0)
    )
    if config.TELEGRAM_PROXY:
        builder = builder.proxy(config.TELEGRAM_PROXY).get_updates_proxy(config.TELEGRAM_PROXY)
        logger.info("Routing Telegram API through proxy: %s", config.TELEGRAM_PROXY)
    app = builder.build()

    common_fallbacks = [
        CommandHandler("cancel", menu.cancel),
        CommandHandler("start", menu.cancel_to_menu),
        CommandHandler("products", menu._ender(products_admin.admin_products)),
        CommandHandler("orders", menu._ender(orders_admin.admin_orders)),
        CommandHandler("myorders", menu._ender(orders_admin.show_my_orders)),
        CallbackQueryHandler(menu.cancel_to_menu, pattern=r"^menu:home$"),
        CallbackQueryHandler(menu._ender(products_admin.admin_products), pattern=r"^menu:products$"),
        CallbackQueryHandler(menu._ender(orders_admin.admin_orders), pattern=r"^menu:orders$"),
        CallbackQueryHandler(menu._ender(orders_admin.show_my_orders), pattern=r"^myorders$"),
        CallbackQueryHandler(menu._ender(catalog.show_catalog), pattern=r"^catalog$"),
        CallbackQueryHandler(menu._ender(products_admin.manage_product), pattern=r"^manage:\d+$"),
        MessageHandler(NAV_NAV, menu._ender(menu._nav_router)),
        MessageHandler(filters.Text([BTN_ADD]), menu._ender(products_admin._add_hint)),
    ]

    payment_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(payments.paid_start, pattern=r"^paid:\d+$")],
        states={
            PAY_UTR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, payments.receive_utr_text),
                MessageHandler(filters.PHOTO, payments.receive_utr_photo),
            ]
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    buy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(payments.buy_product, pattern=r"^buy:\d+$")],
        states={
            BUY_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, payments.buy_qty_received)],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    addproduct_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addproduct", products_admin.addproduct_start),
            CallbackQueryHandler(products_admin.addproduct_start, pattern=r"^menu:add$"),
            MessageHandler(filters.Text([BTN_ADD]), products_admin.addproduct_start),
        ],
        states={
            ADD_NAME: [
                CommandHandler("back", products_admin.add_back),
                CallbackQueryHandler(products_admin.add_back, pattern=r"^apb$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.add_name),
            ],
            ADD_DESC: [
                CommandHandler("back", products_admin.add_back),
                CallbackQueryHandler(products_admin.add_back, pattern=r"^apb$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.add_desc),
            ],
            ADD_PRICE: [
                CommandHandler("back", products_admin.add_back),
                CallbackQueryHandler(products_admin.add_back, pattern=r"^apb$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.add_price),
            ],
            ADD_PRICE_INR: [
                CommandHandler("back", products_admin.add_back),
                CallbackQueryHandler(products_admin.add_back, pattern=r"^apb$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.add_price_inr),
            ],
            ADD_STOCK: [
                CommandHandler("back", products_admin.add_back),
                CallbackQueryHandler(products_admin.add_back, pattern=r"^apb$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.add_stock),
            ],
            ADD_ICON: [
                CommandHandler("back", products_admin.add_back),
                CallbackQueryHandler(products_admin.add_back, pattern=r"^apb$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.add_icon),
            ],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(approvals.reject_start, pattern=r"^reject:\d+$")],
        states={
            REJECT_REASON: [
                CallbackQueryHandler(
                    approvals.reject_choice, pattern=r"^rjr:(stock|nopay|other|cancel)$"
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, approvals.reject_text),
            ],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(products_admin.edit_start, pattern=r"^edit:[a-z_]+:\d+$")],
        states={
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.edit_value)],
            EDIT_PRICE_INR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.edit_price_inr_value)],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    topup_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(topup.topup_start, pattern=r"^topup$"),
            MessageHandler(filters.Text([BTN_TOPUP]), topup.topup_start),
        ],
        states={
            TOPUP_AMOUNT: [
                CallbackQueryHandler(topup.topup_rail_choice, pattern=r"^topup:rail:(bsc|binance_pay)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, topup.topup_amount),
            ],
            TOPUP_CHECK: [
                CallbackQueryHandler(topup.topup_check_prompt, pattern=r"^topup:check$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, topup.topup_check_submit),
            ],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    keys_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(products_admin.keys_start, pattern=r"^keys:\d+$")],
        states={
            KEYS_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.keys_submit)],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    discount_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(products_admin.discount_start, pattern=r"^discount:\d+$")],
        states={
            DISCOUNT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.discount_price)],
            DISCOUNT_PRICE_INR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.discount_price_inr)],
            DISCOUNT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, products_admin.discount_duration)],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    approve_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(approvals.approve_start, pattern=r"^approve:\d+$")],
        states={
            APPROVE_DELIVER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, approvals.approve_deliver_text),
                MessageHandler(filters.PHOTO | filters.Document.ALL, approvals.approve_deliver_media),
            ],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    clean_conv = ConversationHandler(
        entry_points=[
            CommandHandler("cleandatabase", clean.cleandatabase_start),
            CommandHandler("cleanorderdatabase", clean.cleanorderdatabase_start),
            CommandHandler("cleanabandonedorders", clean.cleanabandonedorders_start),
        ],
        states={
            CLEAN_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~NAV_FILTER, clean.clean_confirm)
            ],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    broadcast_conv = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast", broadcast.broadcast_start),
            CallbackQueryHandler(broadcast.broadcast_start, pattern=r"^menu:broadcast$"),
            MessageHandler(filters.Text([BTN_BROADCAST]), broadcast.broadcast_start),
        ],
        states={
            BC_COMPOSE: [
                CallbackQueryHandler(broadcast.bc_done, pattern=r"^bc:done$"),
                CallbackQueryHandler(broadcast.bc_abort, pattern=r"^bc:abort$"),
                CommandHandler("done", broadcast.bc_done),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO) & ~filters.COMMAND & ~NAV_FILTER, broadcast.bc_collect
                ),
            ],
            BC_PRODUCT: [
                CallbackQueryHandler(broadcast.bc_pick, pattern=r"^bc:pick:\d+$"),
                CallbackQueryHandler(broadcast.bc_rechoose, pattern=r"^bc:rechoose$"),
                CallbackQueryHandler(broadcast.bc_skip, pattern=r"^bc:skip$"),
                CallbackQueryHandler(broadcast.bc_review, pattern=r"^bc:review$"),
                MessageHandler(filters.Text(["-"]), broadcast.bc_skip),
            ],
            BC_REVIEW: [
                CallbackQueryHandler(broadcast.bc_send, pattern=r"^bc:send$"),
                CallbackQueryHandler(broadcast.bc_abort, pattern=r"^bc:abort$"),
            ],
        },
        fallbacks=common_fallbacks,
        allow_reentry=True,
    )

    from app.middleware import membership
    app.add_handler(TypeHandler(Update, membership.force_join_check), group=-8)

    app.add_handler(CommandHandler("start", menu.show_main_menu))
    app.add_handler(CommandHandler("fixcmds", menu.refresh_commands))
    app.add_handler(CommandHandler("help", menu.help_cmd))
    app.add_handler(CommandHandler("products", products_admin.admin_products))
    app.add_handler(CommandHandler("orders", orders_admin.admin_orders))
    app.add_handler(CommandHandler("users", orders_admin.show_users))
    app.add_handler(CommandHandler("myorders", orders_admin.show_my_orders))
    app.add_handler(CommandHandler("mode", menu.mode_cmd))

    app.add_handler(payment_conv)
    app.add_handler(buy_conv)
    app.add_handler(addproduct_conv)
    app.add_handler(reject_conv)
    app.add_handler(edit_conv)
    app.add_handler(topup_conv)
    app.add_handler(keys_conv)
    app.add_handler(discount_conv)
    app.add_handler(approve_conv)
    app.add_handler(clean_conv)
    app.add_handler(broadcast_conv)

    # Persistent reply-keyboard buttons (handled here when no flow is active;
    # while a flow is active, the conversation fallbacks above take over).
    app.add_handler(MessageHandler(NAV_NAV, menu._nav_router))

    # Menu buttons
    app.add_handler(CallbackQueryHandler(menu.show_main_menu, pattern=r"^menu:home$"))
    app.add_handler(CallbackQueryHandler(products_admin.admin_products, pattern=r"^menu:products(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(orders_admin.admin_orders, pattern=r"^menu:orders$"))
    app.add_handler(CallbackQueryHandler(orders_admin.show_users, pattern=r"^menu:users(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(orders_admin.show_earnings, pattern=r"^menu:earnings$"))
    app.add_handler(CallbackQueryHandler(orders_admin.admin_orders_active, pattern=r"^orders:active$"))
    app.add_handler(CallbackQueryHandler(orders_admin.admin_orders_abandoned, pattern=r"^orders:abandoned$"))
    app.add_handler(CallbackQueryHandler(orders_admin.show_my_orders, pattern=r"^myorders$"))
    app.add_handler(CallbackQueryHandler(menu.help_cmd, pattern=r"^menu:help$"))
    app.add_handler(CallbackQueryHandler(orders_admin.show_profile, pattern=r"^menu:profile$"))
    app.add_handler(CallbackQueryHandler(topup.show_balance, pattern=r"^menu:balance$"))
    app.add_handler(CallbackQueryHandler(menu.mode_set, pattern=r"^mode:set:(auto|manual)$"))
    app.add_handler(CallbackQueryHandler(menu.support_not_set, pattern=r"^menu:support$"))

    # Admin: manage products
    app.add_handler(CallbackQueryHandler(products_admin.manage_product, pattern=r"^manage:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.toggle_active, pattern=r"^toggle:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.delete_confirm, pattern=r"^del:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.delete_do, pattern=r"^delok:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.enddiscount, pattern=r"^enddiscount:\d+$"))

    # Admin: Stock tab (browse, quick action sheet, select mode, bulk actions)
    app.add_handler(CallbackQueryHandler(products_admin.stock_list, pattern=r"^menu:stock(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_row_tap, pattern=r"^stock:row:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_clear_confirm, pattern=r"^stock:clear:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_clear_do, pattern=r"^stock:clearok:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_select_mode, pattern=r"^stock:sel:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_toggle, pattern=r"^stock:tog:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(products_admin.stock_cancel_select, pattern=r"^stock:cancelsel:\d+$"))
    app.add_handler(
        CallbackQueryHandler(products_admin.stock_bulk_confirm, pattern=r"^stock:bulk:(deactivate|reactivate|clearcodes):\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(products_admin.stock_bulk_apply, pattern=r"^stock:bulkok:(deactivate|reactivate|clearcodes):\d+$")
    )

    # Catalog & purchase flow (buy: is handled by buy_conv above, not registered
    # here — it needs to prompt for a quantity before either continuing or ending)
    app.add_handler(CallbackQueryHandler(catalog.show_catalog, pattern=r"^catalog(:\d+)?$"))
    app.add_handler(CallbackQueryHandler(catalog.view_product, pattern=r"^view:\d+$"))
    app.add_handler(CallbackQueryHandler(payments.pay_upi, pattern=r"^pm:upi:\d+$"))
    app.add_handler(CallbackQueryHandler(payments.pay_binance, pattern=r"^pm:bnb:\d+$"))
    app.add_handler(CallbackQueryHandler(payments.pay_blockchain, pattern=r"^pm:chain:\d+$"))
    app.add_handler(CallbackQueryHandler(topup.pay_wallet, pattern=r"^pm:wallet:\d+$"))
    app.add_handler(CallbackQueryHandler(topup.pay_wallet_confirm, pattern=r"^pm:wconf:\d+$"))
    app.add_handler(CallbackQueryHandler(payments.claim_free, pattern=r"^claim:\d+$"))

    # Auto-confirmed wallet top-up watchers (app.services.crypto_watch) — background
    # jobs, not update handlers. BSC is skipped if no address is configured; Binance
    # Pay is skipped if no read-only API key is configured (checked inside each poll).
    if config.blockchain_enabled():
        app.job_queue.run_repeating(crypto_watch.poll_bsc, interval=20, first=10)
    if config.binance_pay_autoconfirm_enabled():
        app.job_queue.run_repeating(crypto_watch.poll_binance_pay, interval=25, first=15)
    # Bridges the admin panel's manual deposit-resolve action (a plain DB write, no
    # way to message Telegram itself) back to a buyer DM. A `deposits` row can only
    # exist if wallet top-ups are enabled in the first place, so gate this the same
    # way — no point polling every 15s on a deployment with top-ups off entirely.
    if config.wallet_topup_enabled():
        app.job_queue.run_repeating(crypto_watch.poll_manual_credits, interval=15, first=12)
        # Same bridge, opposite outcome: the admin panel's "Reject" action on a
        # pending deposit also can't message Telegram itself.
        app.job_queue.run_repeating(crypto_watch.poll_manual_rejections, interval=15, first=13)

    # Discount expiry — reverts any product past its offer_until window.
    # Unconditional (unlike the wallet-watcher jobs above): discounts don't
    # depend on any payment rail being configured.
    app.job_queue.run_repeating(announcements.expire_offers, interval=60, first=30)

    # Once-daily ops summary DM — independent of whether wallet top-ups are
    # enabled at all, since it also reports order sales.
    app.job_queue.run_daily(
        crypto_watch.send_daily_digest,
        time=dt_time(hour=config.DAILY_DIGEST_HOUR_UTC, minute=0),
    )

    # Stock announcements: admin Approve / Deny -> broadcast to all users.
    app.add_handler(
        CallbackQueryHandler(announcements.announce_decide, pattern=r"^ann:(ok|no):\d+$")
    )

    # Record every user who interacts (its own group so it never consumes the
    # update) — this builds the audience for broadcast announcements.
    app.add_handler(TypeHandler(Update, announcements._track_user), group=-9)

    app.add_handler(CallbackQueryHandler(orders_admin.show_user_detail, pattern=r"^user_detail:\d+$"))

    # Admin utility: forward/send a message containing a custom emoji to get its
    # custom_emoji_id back (for CUSTOM_EMOJI_IDS in .env). filters.Entity only
    # matches messages that actually carry that entity type, so this can never
    # intercept normal admin text used by other conversations.
    app.add_handler(
        MessageHandler(filters.Entity("custom_emoji") & filters.User(config.ADMIN_IDS), menu.emoji_id_lookup)
    )

    app.add_error_handler(on_error)
    return app


async def _connect_with_retry(app: Application) -> None:
    """Reach Telegram before polling starts, retrying transient failures.

    ``run_polling()`` calls ``get_me()`` during initialization; a brief network
    hiccup there would otherwise crash the bot with a raw ``TimedOut`` traceback.
    We initialize the bot ourselves first (``Bot.initialize()`` performs the same
    ``get_me()`` and is idempotent), retrying with exponential backoff. On success
    ``run_polling()`` reuses the already-initialized bot, so nothing is repeated.
    """
    attempts = max(1, config.STARTUP_RETRIES)
    delay = 3.0
    for attempt in range(1, attempts + 1):
        try:
            await app.bot.initialize()  # performs get_me() under the hood
            logger.info("Connected to Telegram as @%s.", app.bot.username)
            return
        except InvalidToken:
            raise SystemExit(
                "BOT_TOKEN was rejected by Telegram. Check the token in .env "
                "(get a fresh one from @BotFather)."
            )
        except NetworkError as err:
            if attempt >= attempts:
                raise SystemExit(
                    f"Could not reach Telegram (api.telegram.org) after {attempts} "
                    f"attempt(s): {err}\n"
                    "Your internet works but Telegram looks unreachable — many ISPs "
                    "block it. Try one of:\n"
                    "  1) turn on a VPN and run again,\n"
                    "  2) use a mobile-data hotspot, or\n"
                    "  3) set TELEGRAM_PROXY=socks5://host:port (or http://host:port) "
                    "in .env\n"
                    '     (socks proxies also need:  pip install "python-telegram-bot[socks]").'
                )
            logger.warning(
                "Telegram unreachable (%s) — attempt %d/%d. Retrying in %.0fs...",
                type(err).__name__,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


def main() -> None:
    config.validate()
    app = build_application()
    # Python 3.14 no longer auto-creates an event loop in asyncio.get_event_loop(),
    # which python-telegram-bot 21.x's run_polling() relies on. Create one explicitly
    # (and reuse it for the pre-flight connection check) so the bot works on
    # Python 3.12 through 3.14.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Confirm we can actually reach Telegram before entering run_polling(), retrying
    # transient network failures with backoff instead of crashing on startup.
    loop.run_until_complete(_connect_with_retry(app))
    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
