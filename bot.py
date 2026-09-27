"""Telegram shop bot: sells logins (digital goods) for USDT BEP20 and delivers them by itself.

Run:  python bot.py        Settings: .env (see .env.example)
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import html
import io
import logging
import random
import re
import sys
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    ReplyKeyboardMarkup,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, InvalidToken, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)

import config
import payments
from db import Database, Row, Settlement, Transfer

log = logging.getLogger("shop")

try:
    CFG = config.load()
except config.ConfigError as exc:
    sys.exit(f"Config error: {exc}")

DB = Database(CFG.db_file)
CHAIN = payments.Chain(CFG.rpc_urls, CFG.wallet_address)
# An order can be paid from the moment it is created until LATE_MINUTES after it expires.
PAYABLE_SECONDS = (CFG.payment_minutes + payments.LATE_MINUTES) * 60
SCANNER = payments.Scanner(DB, CHAIN, window=PAYABLE_SECONDS)
# Orders created closer together than this never share an amount, so a single transfer can never
# fit two of them (a transfer is matched against orders created up to PAYABLE_SECONDS before it).
RESERVE_SECONDS = PAYABLE_SECONDS + 2 * payments.CLOCK_SLACK

QTY_CHOICES = (1, 2, 3, 5, 10)
MAX_QTY = 100  # the most a buyer can type in as a custom quantity (never more than is in stock)
USERS_SHOWN = 20  # users listed on the admin's 👥 Users screen (all of them are in the file)
MAX_ORDERS_PER_HOUR = 10
EXPIRE_GRACE = 60  # seconds past the deadline before an order closes, for last-second payments
RPC_ALERT_AFTER = 8  # failed payment checks in a row (~2 min) before the admins are alerted
MAX_TEXT = 3900  # stay under Telegram's 4096-character message limit

esc = html.escape
# The menu kept at the bottom of the chat. Tapping one sends its label as a message; the same
# choices are also inline buttons on the shop message.
BTN_SHOP, BTN_ORDERS, BTN_HELP, BTN_ADMIN = "🛍 Shop", "📦 My orders", "❓ Help", "🛠 Admin"
SHOP_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton(BTN_SHOP, callback_data="home")]])
_rpc_failures = 0


# ---- small helpers ----------------------------------------------------------------------------


def usd(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def tx_link(tx_hash: str) -> str:
    return f'<a href="https://bscscan.com/tx/{esc(tx_hash)}">view transaction</a>'


def buyer_name(user: User) -> str:
    return f"@{user.username}" if user.username else user.full_name


def support_line() -> str:
    if CFG.support_username:
        return f"Need help? Message @{esc(CFG.support_username)}."
    return "Need help? Message the shop admin."


def command_body(text: str | None) -> str:
    """Everything after the leading /command (and optional @botname)."""
    return re.sub(r"^/\w+(?:@\w+)?", "", text or "", count=1).strip()


def int_arg(text: str) -> int | None:
    return int(text) if re.fullmatch(r"[0-9]{1,9}", text) else None


def parse_price(text: str) -> int | None:
    """'4.99' or '$4.99' -> 499 (cents); None if it is not a sensible price."""
    try:
        value = Decimal(text.strip().lstrip("$"))
    except InvalidOperation:
        return None
    if not value.is_finite() or not Decimal("0.01") <= value <= Decimal("100000"):
        return None
    return int((value * 100).to_integral_value(ROUND_HALF_UP))


async def send(bot, chat_id: int, text: str, **kwargs):
    """Send a message. A user who blocked the bot must never break the caller."""
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramError as exc:
        log.warning("Could not message %s: %s", chat_id, exc)
        return None


async def tell_admins(bot, text: str) -> None:
    for admin_id in CFG.admin_ids:
        await send(bot, admin_id, text)


async def edit(query, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.warning("Could not edit message: %s", exc)


async def close_invoice(bot, inv: Row, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Replace an order's payment message (amount, address, buttons) once the order has ended."""
    if not inv["message_id"]:
        return
    with contextlib.suppress(TelegramError):  # deleted by the user or too old to edit: fine
        await bot.edit_message_text(text, chat_id=inv["user_id"], message_id=inv["message_id"], reply_markup=markup)


# ---- screens ----------------------------------------------------------------------------------


async def shop_screen(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    products = await DB.products()
    rows = [
        [
            InlineKeyboardButton(
                f"{p['name']} · {usd(p['price_cents'])} · " + (f"{p['in_stock']} left" if p["in_stock"] else "sold out"),
                callback_data=f"p:{p['id']}",
            )
        ]
        for p in products
    ]
    rows.append([InlineKeyboardButton(BTN_ORDERS, callback_data="orders"), InlineKeyboardButton(BTN_HELP, callback_data="help")])
    if user_id in CFG.admin_ids:
        rows.append([InlineKeyboardButton(BTN_ADMIN, callback_data="a:home")])
    text = (
        f"🛍 <b>{esc(CFG.store_name)}</b>\n\n"
        "Pick a product. You pay with <b>USDT (BEP20)</b> and your login is delivered right here, "
        "automatically, as soon as the payment confirms."
    )
    if not products:
        text += "\n\n<i>Nothing for sale yet. Check back soon!</i>"
    return text, InlineKeyboardMarkup(rows)


def menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [[BTN_SHOP, BTN_ORDERS], [BTN_HELP]]
    if user_id in CFG.admin_ids:
        rows[1].append(BTN_ADMIN)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def product_screen(p: Row) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"🛍 <b>{esc(p['name'])}</b>", "", f"💵 Price: <b>{usd(p['price_cents'])}</b> each"]
    if CFG.fee_cents:
        lines.append(f"➕ Fee: {usd(CFG.fee_cents)} per order")
    lines.append(f"📦 In stock: <b>{p['in_stock']}</b>")
    choices = [q for q in QTY_CHOICES if q <= p["in_stock"]]
    rows = []
    if choices:
        lines += [
            "",
            "👇 <b>SELECT QUANTITY</b>",
            "Tap how many you want to buy, or <b>✏️ Enter quantity</b> for any other number.",
        ]
        buttons = [
            InlineKeyboardButton(f"🛒 Buy {q} · {usd(p['price_cents'] * q + CFG.fee_cents)}", callback_data=f"b:{p['id']}:{q}")
            for q in choices
        ]
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("✏️ Enter quantity", callback_data=f"q:{p['id']}")])
    else:
        lines += ["", "😕 <b>Sold out right now.</b> Check back later!"]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="home")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def quantity_prompt(p: Row) -> tuple[str, InlineKeyboardMarkup]:
    most = min(p["in_stock"], MAX_QTY)
    fee = f" (+ {usd(CFG.fee_cents)} fee per order)" if CFG.fee_cents else ""
    text = (
        "✏️ <b>ENTER QUANTITY</b>\n\n"
        f"How many <b>{esc(p['name'])}</b> do you want?\n"
        f"Type a number from <b>1</b> to <b>{most}</b> and send it.\n\n"
        f"💵 {usd(p['price_cents'])} each{fee}"
    )
    return text, InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"p:{p['id']}")]])


def invoice_screen(inv: Row) -> tuple[str, InlineKeyboardMarkup]:
    amount = payments.usdt(inv["amount_units"])
    minutes = max(1, -(-(inv["expires_at"] - int(time.time())) // 60))  # rounded up
    text = (
        f"🧾 <b>Order #{inv['id']}</b> · {esc(inv['product_name'])} × {inv['qty']}\n\n"
        "Send <b>exactly</b> this amount of <b>USDT</b> on <b>BNB Smart Chain (BEP20)</b>:\n\n"
        f"💰 <code>{amount}</code> USDT\n"
        f"📬 <code>{esc(CFG.wallet_address)}</code>\n\n"
        "⚠️ Send the exact amount, last digits included: they identify your order. Paying from an "
        "exchange? The amount that <i>arrives</i> must match, so add the withdrawal fee on top. "
        "Only USDT on BEP20; other coins or networks are lost.\n\n"
        f"⏱ Pay within <b>{minutes} min</b>. Stock goes to whoever pays first.\n"
        "✅ Your login is delivered here automatically, usually within a minute of paying."
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 Copy amount", copy_text=CopyTextButton(amount)),
                InlineKeyboardButton("📋 Copy address", copy_text=CopyTextButton(CFG.wallet_address)),
            ],
            [InlineKeyboardButton("🔄 I've paid, check now", callback_data=f"c:{inv['id']}")],
            [InlineKeyboardButton("✖️ Cancel order", callback_data=f"x:{inv['id']}")],
        ]
    )
    return text, markup


def help_text() -> str:
    return (
        "❓ <b>How to buy</b>\n\n"
        f"1. Tap {BTN_SHOP} and pick a product and a quantity.\n"
        "2. Send the exact USDT amount shown to the address shown, on <b>BNB Smart Chain (BEP20)</b>.\n"
        "3. Your login is delivered here automatically, usually within a minute.\n\n"
        "The last digits of the amount identify your payment, so always send it exactly. "
        f"Your past orders are in {BTN_ORDERS}.\n\n" + support_line()
    )


# ---- delivery & payment notifications ---------------------------------------------------------


async def deliver(bot, inv: Row, items: list[str], heading: str = "✅ <b>Payment received, thank you!</b>") -> None:
    paid = payments.usdt_raw(int(inv["paid_raw"])) if inv["paid_raw"] else payments.usdt(inv["amount_units"])
    head = (
        f"{heading}\n\n"
        f"🧾 Order #{inv['id']} · {esc(inv['product_name'])} × {inv['qty']}\n"
        f"💰 {paid} USDT · {tx_link(inv['tx_hash'])}"
    )
    logins = "\n".join(f"<code>{esc(item)}</code>" for item in items)
    label = "Your logins" if len(items) > 1 else "Your login"
    text = f"{head}\n\n🔑 <b>{label}:</b>\n{logins}\n\nSaved in {BTN_ORDERS} in case you need it again."
    if len(text) <= MAX_TEXT:
        await send(bot, inv["user_id"], text)
        return
    await send(bot, inv["user_id"], f"{head}\n\n🔑 Your logins are in the file below. Also saved in {BTN_ORDERS}.")
    try:
        await bot.send_document(inv["user_id"], document="\n".join(items).encode(), filename=f"order-{inv['id']}.txt")
    except TelegramError as exc:
        log.warning("Could not send the file for order #%s: %s", inv["id"], exc)


async def announce(bot, t: Transfer, s: Settlement) -> None:
    """Tell the buyer and the admins what a processed transfer did."""
    amount = payments.usdt_raw(t.value_raw)
    if s.note == "matched" and s.invoice is not None:
        inv = s.invoice
        order = f"order #{inv['id']} · {esc(inv['product_name'])} × {inv['qty']}"
        buyer = f"{esc(inv['buyer'])} (<code>{inv['user_id']}</code>)"
        restock = f"<code>/stock {inv['product_id']}</code>"
        if inv["status"] == "paid":
            await deliver(bot, inv, s.items)
            await close_invoice(bot, inv, f"✅ <b>Order #{inv['id']} is paid.</b> Your login is in the message below.")
            extra = f"\n⚠️ Now sold out. Add more with {restock}" if s.sold_out else ""
            await tell_admins(bot, f"💰 <b>Sale</b>: {order}\n👤 {buyer}\n💵 {amount} USDT · {tx_link(t.tx_hash)}{extra}")
        else:  # paid, but the stock ran out before the money arrived
            await send(
                bot,
                inv["user_id"],
                f"✅ <b>Payment received</b> for order #{inv['id']} ({esc(inv['product_name'])} × {inv['qty']}).\n\n"
                "😕 It sold out just before your payment arrived, so it can't be delivered yet. It will be sent "
                "here automatically as soon as it is restocked, no need to do anything.\n\n" + support_line(),
            )
            await close_invoice(bot, inv, f"✅ <b>Order #{inv['id']} is paid</b> and waiting for restock.")
            await tell_admins(
                bot,
                f"📦 <b>Paid but out of stock</b>: {order}\n👤 {buyer}\n💵 {amount} USDT · {tx_link(t.tx_hash)}\n"
                f"Add stock with {restock} and it is delivered automatically.",
            )
    elif s.note == "ambiguous":
        orders = ", ".join(f"#{i}" for i in s.candidate_ids)
        await tell_admins(
            bot,
            f"⚠️ <b>Payment needs a manual check</b>\n💵 {amount} USDT from <code>{t.from_address}</code> · "
            f"{tx_link(t.tx_hash)}\nThe amount fits several orders ({orders}), so nothing was delivered automatically.",
        )
    elif s.note == "unmatched":
        await tell_admins(
            bot,
            f"⚠️ <b>Unmatched payment</b>\n💵 {amount} USDT from <code>{t.from_address}</code> · {tx_link(t.tx_hash)}\n"
            "No order matches this amount (wrong amount, or paid far too late), so nothing was delivered. "
            "Sort it out with the buyer.",
        )


async def check_payments(bot, *, min_interval: float = 0.0) -> None:
    """Read the chain once, then tell everyone involved what happened."""
    global _rpc_failures
    result = await SCANNER.run(min_interval=min_interval)
    for t, s in result.settled:
        try:
            await announce(bot, t, s)
        except Exception:  # one failed message must not swallow the others
            log.exception("Announcing transfer %s failed", t.tx_hash)
    if result.skipped:
        return
    if result.error:
        _rpc_failures += 1
        if _rpc_failures == RPC_ALERT_AFTER:
            await tell_admins(
                bot,
                f"🚨 <b>Payment checking is failing</b>\n<code>{esc(result.error)}</code>\n\n"
                "No payment can be confirmed until this is fixed. Check <code>BSC_RPC_URLS</code> in .env.",
            )
        return
    if _rpc_failures >= RPC_ALERT_AFTER:
        await tell_admins(bot, "✅ Payment checking works again. Payments made meanwhile are being picked up now.")
    _rpc_failures = 0


async def payments_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await check_payments(context.bot)  # before expiring, so a payment made just in time still counts
    for inv in await DB.expire_invoices(grace=EXPIRE_GRACE):
        await close_invoice(
            context.bot,
            inv,
            f"⌛ <b>Order #{inv['id']} expired.</b>\n\n"
            "The payment time ran out. Already sent the money? Don't worry: "
            f"payments are still recognised for {payments.LATE_MINUTES // 60} more hours and delivered "
            "automatically while in stock.",
            SHOP_BUTTON,
        )


# ---- who uses the bot (shown to admins under 👥 Users) ----------------------------------------


async def describe_action(update: Update) -> str:
    """A short, human description of what a user just did."""
    if update.callback_query is not None:
        kind, _, rest = (update.callback_query.data or "").partition(":")
        if kind in ("p", "b", "q", "n"):
            pid, _, qty = rest.partition(":")
            product = await DB.product(int(pid)) if pid.isdigit() and len(pid) <= 9 else None
            name = product["name"] if product is not None else f"product #{pid}"
            return {
                "p": f"viewed {name}",
                "b": f"chose to buy {qty} × {name}",
                "q": f"entering a quantity for {name}",
                "n": f"tapped Buy now on {name}",
            }[kind]
        return {
            "home": "opened the shop",
            "orders": "opened My orders",
            "help": "opened Help",
            "c": f"checked payment of order #{rest}",
            "x": f"cancelled order #{rest}",
            "a": "used the admin panel",
        }.get(kind, "pressed an old button")
    msg = update.effective_message
    if msg is not None and msg.text:
        text = msg.text.strip()
        if text.startswith("/"):
            return f"sent {text.split()[0][:32]}"
        if text in (BTN_SHOP, BTN_ORDERS, BTN_HELP, BTN_ADMIN):
            return f"pressed {text}"
        return "typed a message"
    if msg is not None and msg.document:
        return "sent a file"
    return "used the bot"


async def track_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every handler: remember who is using the bot and what they did last."""
    user, chat = update.effective_user, update.effective_chat
    if user is None or user.is_bot or chat is None or chat.type != "private":
        return
    try:
        await DB.touch_user(user.id, user.username, user.full_name, await describe_action(update))
    except Exception:  # never let bookkeeping get in the way of serving the user
        log.exception("Could not record user %s", user.id)


# ---- stock announcements and admin broadcasts -------------------------------------------------

ANNOUNCE_HEADLINES = (
    "🔥 <b>{name}</b> is in stock!",
    "⚡ <b>Available now:</b> {name}",
    "🛍 <b>Ready for instant delivery:</b> {name}",
    "✅ <b>In stock:</b> {name}",
)
BROADCAST_LOCK = asyncio.Lock()  # one mass send at a time keeps the bot under Telegram's rate limit
_next_announcement = 0.0  # unix time of the next automatic announcement (0 = none scheduled)


def buy_now_markup(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Buy now", callback_data=f"n:{product_id}")]])


async def send_to_everyone(send_one) -> tuple[int, int]:
    """Call `send_one(user_id)` for every user who hasn't blocked the bot, about 20 a second.
    Returns (sent, failed). Users who blocked the bot are remembered and skipped from then on."""
    sent = failed = 0
    async with BROADCAST_LOCK:
        for user_id in await DB.recipients(exclude=CFG.admin_ids):
            for _ in range(3):
                try:
                    await send_one(user_id)
                    sent += 1
                    break
                except RetryAfter as exc:  # Telegram asked us to slow down
                    wait = exc.retry_after
                    await asyncio.sleep((wait.total_seconds() if hasattr(wait, "total_seconds") else float(wait)) + 1)
                except Forbidden:  # they blocked the bot or deleted their account
                    await DB.set_blocked(user_id)
                    failed += 1
                    break
                except TelegramError as exc:
                    log.warning("Could not reach %s: %s", user_id, exc)
                    failed += 1
                    break
            else:
                failed += 1
            await asyncio.sleep(0.05)
    return sent, failed


def announcement_text(p: Row) -> str:
    headline = random.choice(ANNOUNCE_HEADLINES).format(name=esc(p["name"]))
    return (
        f"{headline}\n\n"
        f"💵 <b>{usd(p['price_cents'])}</b> each · 📦 <b>{p['in_stock']}</b> left\n"
        "⚡ Pay with USDT (BEP20) and your login arrives here automatically."
    )


async def announce_product(bot) -> tuple[Row | None, int, int]:
    """Announce one random in-stock product to everyone (not the same one twice in a row)."""
    in_stock = [p for p in await DB.products() if p["in_stock"] > 0]
    if not in_stock:
        return None, 0, 0
    last = await DB.get_setting("announce_last")
    p = random.choice([x for x in in_stock if str(x["id"]) != last] or in_stock)
    text, markup = announcement_text(p), buy_now_markup(p["id"])
    sent, failed = await send_to_everyone(lambda user_id: bot.send_message(user_id, text, reply_markup=markup))
    await DB.set_setting("announce_last", str(p["id"]))
    await DB.set_setting("announce_last_at", str(int(time.time())))
    log.info("Announced %s to %d users (%d failed)", p["name"], sent, failed)
    return p, sent, failed


def schedule_announcement(job_queue) -> None:
    global _next_announcement
    low, high = CFG.announce_minutes
    delay = random.randint(low * 60, high * 60)
    _next_announcement = time.time() + delay
    job_queue.run_once(announce_job, when=delay, name="announce")


async def announce_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if await DB.get_setting("announce", "on") == "on":
            await announce_product(context.bot)
    finally:
        schedule_announcement(context.job_queue)  # a random gap every time


async def announce_status() -> str:
    if CFG.announce_minutes is None:
        return "📣 Auto announce: off (ANNOUNCE_MINUTES=0 in .env)"
    on = await DB.get_setting("announce", "on") == "on"
    line = f"📣 Auto announce: <b>{'ON' if on else 'OFF'}</b>"
    if on and _next_announcement:
        line += f" · next in ~{max(1, int(_next_announcement - time.time()) // 60)}m"
    last_at = await DB.get_setting("announce_last_at")
    return line + (f" · last {ago(int(last_at))}" if last_at else "")


async def cb_buy_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🛒 Buy now under an announcement or broadcast: open that product as a new message."""
    q = update.callback_query
    product = await DB.product(int(q.data.split(":")[1]))
    if product is None or product["in_stock"] < 1:
        await q.answer("Sorry, it's sold out right now." if product else "This product is no longer available.", show_alert=True)
        return
    await q.answer()
    text, markup = product_screen(product)
    await send(context.bot, q.from_user.id, text, reply_markup=markup)


# ---- buyer handlers ---------------------------------------------------------------------------

BUY_ERRORS = {
    "rate": "You opened too many orders in the last hour. Please try again later.",
    "gone": "This product is no longer available.",
    "stock": "Not enough stock for that quantity any more.",
    "busy": "Too many people are paying this exact price right now. Please try again in a few minutes.",
}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    await update.effective_message.reply_text(
        f"👋 Welcome to <b>{esc(CFG.store_name)}</b>!",
        reply_markup=menu_keyboard(update.effective_user.id),
    )
    text, markup = await shop_screen(update.effective_user.id)
    await update.effective_message.reply_text(text, reply_markup=markup)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any text: a bottom-menu button, the answer to a question the bot asked, or else the shop."""
    user = update.effective_user
    text = (update.effective_message.text or "").strip()
    is_admin = user.id in CFG.admin_ids
    pending = context.user_data.get("await")
    if text in (BTN_SHOP, BTN_ORDERS, BTN_HELP, BTN_ADMIN) or text.startswith("/"):
        context.user_data.pop("await", None)  # a menu tap or command abandons a pending question
    elif pending and pending[0] == "qty":
        await quantity_input(update, context, text, pending[1])
        return
    elif pending and is_admin:
        await admin_input(update, context, update.effective_message.text)
        return
    if text == BTN_SHOP:
        shop_text, markup = await shop_screen(update.effective_user.id)
        await update.effective_message.reply_text(shop_text, reply_markup=markup)
    elif text == BTN_ORDERS:
        await show_orders(context.bot, user.id)
    elif text == BTN_HELP:
        await cmd_help(update, context)
    elif text == BTN_ADMIN and is_admin:
        await cmd_admin(update, context)
    else:
        await cmd_start(update, context)


async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    await update.callback_query.answer()
    text, markup = await shop_screen(update.effective_user.id)
    await edit(update.callback_query, text, markup)


async def cb_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    q = update.callback_query
    product = await DB.product(int(q.data.split(":")[1]))
    if product is None:
        await q.answer("This product is no longer available.", show_alert=True)
        text, markup = await shop_screen(update.effective_user.id)
    else:
        await q.answer()
        text, markup = product_screen(product)
    await edit(q, text, markup)


async def open_order(context: ContextTypes.DEFAULT_TYPE, user: User, product_id: int, qty: int) -> tuple[str, Row | None]:
    outcome, inv = await DB.create_invoice(
        user_id=user.id,
        buyer=buyer_name(user),
        product_id=product_id,
        qty=qty,
        fee_cents=CFG.fee_cents,
        pay_seconds=CFG.payment_minutes * 60,
        reserve_seconds=RESERVE_SECONDS,
        max_per_hour=MAX_ORDERS_PER_HOUR,
        pick_amount=payments.pick_amount,
    )
    if outcome == "created":
        amount = payments.usdt(inv["amount_units"])
        log.info("Order #%s opened by %s: product %s x%s, %s USDT", inv["id"], user.id, product_id, qty, amount)
    return outcome, inv


async def show_invoice(context: ContextTypes.DEFAULT_TYPE, user_id: int, inv: Row, query=None) -> None:
    """Show the payment details, in place of the product message when there is one."""
    text, markup = invoice_screen(inv)
    message_id = None
    if query is not None and query.message is not None:
        try:
            await query.edit_message_text(text, reply_markup=markup)
            message_id = query.message.message_id
        except TelegramError:
            pass
    if message_id is None:  # never leave a buyer without the payment details
        msg = await send(context.bot, user_id, text, reply_markup=markup)
        message_id = msg.message_id if msg else None
    if message_id:
        await DB.set_invoice_message(inv["id"], message_id)


async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    q = update.callback_query
    _, product_id, qty = q.data.split(":")
    product_id, qty = int(product_id), int(qty)
    if not 1 <= qty <= MAX_QTY:
        await q.answer()
        return
    outcome, inv = await open_order(context, q.from_user, product_id, qty)
    if outcome == "created":
        await q.answer()
        await show_invoice(context, q.from_user.id, inv, q)
    elif outcome == "open":
        await q.answer("You already have an open order. Pay or cancel it first.", show_alert=True)
        await show_invoice(context, q.from_user.id, inv)
    else:
        await q.answer(BUY_ERRORS[outcome], show_alert=True)
        product = await DB.product(product_id)
        text, markup = product_screen(product) if product is not None else await shop_screen(update.effective_user.id)
        await edit(q, text, markup)


async def cb_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """✏️ Enter quantity: ask the buyer to type how many they want."""
    q = update.callback_query
    product = await DB.product(int(q.data.split(":")[1]))
    if product is None or product["in_stock"] < 1:
        await q.answer("Sold out right now." if product else "This product is no longer available.", show_alert=True)
        return
    context.user_data["await"] = ("qty", product["id"])
    await q.answer()
    text, markup = quantity_prompt(product)
    await edit(q, text, markup)


async def quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, product_id: int) -> None:
    """The number a buyer typed after ✏️ Enter quantity."""
    msg = update.effective_message
    product = await DB.product(product_id)
    if product is None or product["in_stock"] < 1:
        context.user_data.pop("await", None)
        await msg.reply_text("😕 Sorry, this product is sold out right now.", reply_markup=SHOP_BUTTON)
        return
    most = min(product["in_stock"], MAX_QTY)
    qty = int(text) if re.fullmatch(r"[0-9]{1,6}", text) else 0
    if not 1 <= qty <= most:
        _, back = quantity_prompt(product)
        await msg.reply_text(f"❌ Please send just a number from <b>1</b> to <b>{most}</b>.", reply_markup=back)
        return
    context.user_data.pop("await", None)
    outcome, inv = await open_order(context, update.effective_user, product_id, qty)
    if outcome in ("created", "open"):
        if outcome == "open":
            await msg.reply_text("You already have an open order. Pay or cancel it first 👇")
        await show_invoice(context, update.effective_user.id, inv)
    else:
        await msg.reply_text(BUY_ERRORS[outcome], reply_markup=product_screen(product)[1])


async def cb_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    invoice_id = int(q.data.split(":")[1])
    inv = await DB.invoice(invoice_id)
    if inv is None or inv["user_id"] != q.from_user.id:
        await q.answer("Order not found.", show_alert=True)
        return
    if inv["status"] == "pending":
        # Read the chain right now instead of waiting for the next scheduled check. The wait is
        # capped so the button always answers; a slow check still finishes and delivers by itself.
        task = context.application.create_task(check_payments(context.bot, min_interval=5))
        await asyncio.wait({task}, timeout=6)
        inv = await DB.invoice(invoice_id)
    elif inv["status"] == "paid":  # pressed on an order that is already paid: send it again
        await deliver(context.bot, inv, await DB.items_of(invoice_id), heading="🔑 <b>Your order</b>")

    if inv["status"] == "pending":
        left = max(0, inv["expires_at"] - int(time.time()))
        await q.answer(
            f"⏳ Not received yet ({left // 60}:{left % 60:02d} left). Wallets and exchanges can take a few "
            "minutes to send. You'll get a message here the moment it arrives.",
            show_alert=True,
        )
    elif inv["status"] == "paid":
        await q.answer("✅ Payment received! Your login is in the chat.", show_alert=True)
    elif inv["status"] == "backorder":
        await q.answer(
            "✅ Payment received! It's out of stock right now and will be delivered here automatically when restocked.",
            show_alert=True,
        )
    else:
        await q.answer(
            "This order is closed. If you already paid, the payment is still recognised and delivered automatically.",
            show_alert=True,
        )


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    invoice_id = int(q.data.split(":")[1])
    if not await DB.cancel_invoice(invoice_id, q.from_user.id):
        await q.answer("This order is already paid or closed.", show_alert=True)
        return
    await q.answer("Order cancelled.")
    await edit(
        q,
        f"✖️ <b>Order #{invoice_id} cancelled.</b>\n\n"
        "If you had already sent the payment, it is still recognised and delivered automatically.",
        SHOP_BUTTON,
    )


async def show_orders(bot, user_id: int) -> None:
    orders = await DB.orders(user_id)
    if not orders:
        await send(bot, user_id, "📦 No orders yet.", reply_markup=SHOP_BUTTON)
        return
    blocks = []
    for inv, items in orders:
        day = time.strftime("%Y-%m-%d", time.gmtime(inv["paid_at"]))
        body = (
            "\n".join(f"<code>{esc(i)}</code>" for i in items)
            if items
            else "<i>Paid, waiting for restock. It is delivered automatically.</i>"
        )
        blocks.append(f"<b>#{inv['id']}</b> · {esc(inv['product_name'])} × {inv['qty']} · {day}\n{body}")
    text = "📦 <b>Your latest orders</b>\n\n" + "\n\n".join(blocks)
    if len(text) <= MAX_TEXT:
        await send(bot, user_id, text, reply_markup=SHOP_BUTTON)
        return
    plain = "\n\n".join(f"#{inv['id']} {inv['product_name']} x{inv['qty']}\n" + "\n".join(items) for inv, items in orders)
    await send(bot, user_id, "📦 Your latest orders are in the file below.", reply_markup=SHOP_BUTTON)
    try:
        await bot.send_document(user_id, document=plain.encode(), filename="orders.txt")
    except TelegramError as exc:
        log.warning("Could not send the orders file to %s: %s", user_id, exc)


async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_orders(context.bot, update.effective_user.id)


async def cb_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await show_orders(context.bot, update.effective_user.id)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(help_text(), reply_markup=SHOP_BUTTON)


async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await send(context.bot, update.effective_user.id, help_text(), reply_markup=SHOP_BUTTON)


async def cb_outdated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buttons on old messages (e.g. from the previous bot) that no longer mean anything."""
    await update.callback_query.answer("This button is outdated. Here is the shop.")
    text, markup = await shop_screen(update.effective_user.id)
    await send(context.bot, update.effective_user.id, text, reply_markup=markup)


# ---- admin: buttons under 🛠 Admin, plus the same actions as slash commands ------------------
# Messages are limited to ADMIN_IDS by the handler filters; admin buttons check it themselves.

STOCK_USAGE = (
    "Put the logins below the command, one per line:\n"
    "<pre>/stock 1\nemail1:password1\nemail2:password2</pre>\n"
    "Or tap 🛠 Admin → the product → ➕ Add logins."
)
NEW_PRODUCT_PROMPT = (
    "➕ <b>New product</b>\n\nSend its name and price, separated by <code>|</code>:\n"
    "<code>Netflix Premium 1 Month | 4.99</code>"
)


def one_button(label: str, data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)]])


def confirm_markup(yes: str, no: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Yes", callback_data=yes), InlineKeyboardButton("⬅️ No", callback_data=no)]]
    )


def stock_prompt(p: Row) -> str:
    return (
        f"➕ <b>Add logins to {esc(p['name'])}</b>\n\n"
        "Send them in one message, one login per line:\n"
        "<pre>email1:password1\nemail2:password2</pre>\n"
        "Or send a .txt file with one login per line."
    )


def parse_product(text: str) -> tuple[str, int | None]:
    """'Netflix 1 Month | 4.99' -> ('Netflix 1 Month', 499); price None if it doesn't parse."""
    name, sep, price_text = text.rpartition("|")
    name = " ".join(name.split())
    price = parse_price(price_text) if sep else None
    return name, price if name and len(name) <= 64 else None


async def admin_home() -> tuple[str, InlineKeyboardMarkup]:
    products = await DB.products()
    s = await DB.summary()
    u = await DB.user_stats()
    text = (
        "🛠 <b>Admin</b>\n\n"
        f"🧾 Open orders: {s['open_orders']} · Waiting for stock: {s['backorders']}\n"
        f"💰 Last 24h: {s['sales']} sales · {payments.usdt(s['units'])} USDT\n"
        f"👥 Users: {u['total']} total · {u['active']} active in the last 24h\n"
        f"{await announce_status()}\n\n"
        + ("Tap a product to add logins, change its price or remove it." if products else "No products yet. Add one 👇")
    )
    rows = [
        [
            InlineKeyboardButton("➕ New product", callback_data="a:new"),
            InlineKeyboardButton("👥 Users", callback_data="a:users"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="a:bc"),
            InlineKeyboardButton("🎲 Announce now", callback_data="a:ann_now"),
        ],
    ]
    if CFG.announce_minutes is not None:
        on = await DB.get_setting("announce", "on") == "on"
        rows.append([InlineKeyboardButton(f"📣 Turn auto announce {'OFF' if on else 'ON'}", callback_data="a:ann_toggle")])
    rows += [
        [InlineKeyboardButton(f"{p['name']} · {usd(p['price_cents'])} · {p['in_stock']} left", callback_data=f"a:p:{p['id']}")]
        for p in products
    ]
    rows.append([InlineKeyboardButton(BTN_SHOP, callback_data="home")])
    return text, InlineKeyboardMarkup(rows)


def admin_product_screen(p: Row) -> tuple[str, InlineKeyboardMarkup]:
    pid = p["id"]
    text = (
        f"🛠 <b>{esc(p['name'])}</b> (#{pid})\n\n"
        f"💵 Price: {usd(p['price_cents'])}\n"
        f"📦 In stock: {p['in_stock']} · Sold: {p['sold']}"
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add logins", callback_data=f"a:stock:{pid}"),
                InlineKeyboardButton("💲 Change price", callback_data=f"a:price:{pid}"),
            ],
            [
                InlineKeyboardButton("🗑 Delete unsold", callback_data=f"a:clear:{pid}"),
                InlineKeyboardButton("❌ Remove", callback_data=f"a:del:{pid}"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="a:home")],
        ]
    )
    return text, markup


def ago(ts: int) -> str:
    seconds = max(0, int(time.time()) - ts)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


async def users_screen() -> tuple[str, InlineKeyboardMarkup]:
    """Who has used the bot, most recent first: when, how much, and what they did last."""
    stats = await DB.user_stats()
    head = f"👥 <b>Users</b>: {stats['total']} total · {stats['active']} active in the last 24h\n"
    blocks = []
    for n, u in enumerate(await DB.users(limit=USERS_SHOWN), 1):
        handle = f" @{esc(u['username'])}" if u["username"] else ""
        block = (
            f"\n{n}. <a href=\"tg://user?id={u['id']}\">{esc(u['name'])}</a>{handle} · <code>{u['id']}</code>\n"
            f"🕒 {ago(u['last_seen'])} · 👆 {u['clicks']} clicks · 🛒 {u['orders']} paid orders"
            + (" · 🚫 blocked the bot" if u["blocked"] else "")
        )
        if u["last_action"]:
            block += f"\n↳ {esc(u['last_action'])}"
        if len(head) + sum(map(len, blocks)) + len(block) > MAX_TEXT - 200:
            break
        blocks.append(block)
    text = head + ("".join(blocks) if blocks else "\nNobody has used the bot yet.")
    if stats["total"] > len(blocks):
        text += f"\n\n<i>Showing the latest {len(blocks)}. Tap 📄 for everyone.</i>"
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 All users (file)", callback_data="a:users_file")],
            [InlineKeyboardButton("⬅️ Back", callback_data="a:home")],
        ]
    )
    return text, markup


async def send_users_file(bot, chat_id: int) -> None:
    def utc(ts: int) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))

    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["user_id", "username", "name", "first_seen_utc", "last_seen_utc", "clicks", "paid_orders", "last_action"])
    for u in await DB.users():
        out.writerow(
            [u["id"], u["username"] or "", u["name"], utc(u["first_seen"]), utc(u["last_seen"]), u["clicks"], u["orders"],
             u["last_action"] or ""]
        )
    try:  # utf-8-sig so Excel shows non-English names correctly
        await bot.send_document(chat_id, document=buf.getvalue().encode("utf-8-sig"), filename="users.csv")
    except TelegramError as exc:
        log.warning("Could not send the users file: %s", exc)


async def reply_product_screen(update: Update, product_id: int) -> None:
    """After an admin action, show that product's buttons again so the next step is one tap."""
    p = await DB.product(product_id)
    if p is not None:
        text, markup = admin_product_screen(p)
        await update.effective_message.reply_text(text, reply_markup=markup)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    text, markup = await admin_home()
    await update.effective_message.reply_text(text, reply_markup=markup)


async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in CFG.admin_ids:
        await q.answer("Admins only.", show_alert=True)
        return
    _, action, *rest = q.data.split(":")
    context.user_data.pop("await", None)  # any admin button abandons a pending question
    if action in ("bc", "bc_p", "bc_send", "ann_now", "ann_toggle"):
        await admin_broadcast_button(q, context, action, int(rest[0]) if rest else 0)
        return
    if action == "users":
        await q.answer()
        text, markup = await users_screen()
        await edit(q, text, markup)
        return
    if action == "users_file":
        await q.answer("Sending the list…")
        await send_users_file(context.bot, q.from_user.id)
        return
    if action == "new":
        context.user_data["await"] = ("new", 0)
        await q.answer()
        await edit(q, NEW_PRODUCT_PROMPT, one_button("✖️ Cancel", "a:home"))
        return
    p = await DB.product(int(rest[0])) if rest else None
    if p is None:  # "home", or a product that was removed meanwhile
        await q.answer("That product no longer exists." if rest else None)
        text, markup = await admin_home()
        await edit(q, text, markup)
        return
    pid, name, back = p["id"], esc(p["name"]), f"a:p:{p['id']}"
    if action == "stock":
        context.user_data["await"] = ("stock", pid)
        await q.answer()
        await edit(q, stock_prompt(p), one_button("✖️ Cancel", back))
    elif action == "price":
        context.user_data["await"] = ("price", pid)
        await q.answer()
        await edit(
            q,
            f"💲 <b>New price for {name}</b>\n\nNow {usd(p['price_cents'])}. Send the new price, e.g. <code>5.99</code>",
            one_button("✖️ Cancel", back),
        )
    elif action == "clear":
        await q.answer()
        await edit(
            q, f"🗑 Delete all {p['in_stock']} unsold login(s) of <b>{name}</b>?", confirm_markup(f"a:clear_yes:{pid}", back)
        )
    elif action == "del":
        await q.answer()
        await edit(
            q,
            f"❌ Remove <b>{name}</b> from the shop? Its {p['in_stock']} unsold login(s) are deleted too.",
            confirm_markup(f"a:del_yes:{pid}", back),
        )
    elif action == "clear_yes":
        removed = await DB.clear_stock(pid)
        await q.answer(f"Deleted {removed} unsold login(s).")
        text, markup = admin_product_screen(await DB.product(pid))
        await edit(q, text, markup)
    elif action == "del_yes":
        await DB.delete_product(pid)
        await q.answer("Product removed.")
        text, markup = await admin_home()
        await edit(q, text, markup)
    else:  # "p": the product's own screen
        await q.answer()
        text, markup = admin_product_screen(p)
        await edit(q, text, markup)


async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """The admin's reply to the question an admin button asked: a new product, logins or a price."""
    kind, product_id = context.user_data["await"]
    msg = update.effective_message
    if kind == "broadcast":
        await broadcast_input(update, context)
        return
    if kind == "new":
        name, price = parse_product(text)
        if price is None:
            await msg.reply_text(
                "Send it like this: <code>Netflix Premium 1 Month | 4.99</code>", reply_markup=one_button("✖️ Cancel", "a:home")
            )
            return
        product_id = await DB.add_product(name, price)
        context.user_data["await"] = ("stock", product_id)  # straight on to its logins
        await msg.reply_text(
            f"✅ Added <b>{esc(name)}</b> at {usd(price)}.\n\n" + stock_prompt(await DB.product(product_id)),
            reply_markup=one_button("⏭ Add logins later", f"a:p:{product_id}"),
        )
    elif kind == "stock":
        context.user_data.pop("await", None)
        if await add_stock(update, context, product_id, text.splitlines()):
            await reply_product_screen(update, product_id)
    elif kind == "price":
        price = parse_price(text)
        if price is None:
            await msg.reply_text(
                "Send just the price, e.g. <code>5.99</code>", reply_markup=one_button("✖️ Cancel", f"a:p:{product_id}")
            )
            return
        context.user_data.pop("await", None)
        if await DB.set_price(product_id, price):
            await msg.reply_text(f"✅ New price: {usd(price)}.")
        await reply_product_screen(update, product_id)


BROADCAST_PROMPT = (
    "📢 <b>Broadcast</b>\n\n"
    "Send the message for all <b>{count}</b> users: text, or a photo / video with a caption. "
    "Formatting is kept exactly as you write it.\n\n"
    "Next you can attach a product, so a <b>🛒 Buy now</b> button appears under it."
)


async def admin_broadcast_button(q, context: ContextTypes.DEFAULT_TYPE, action: str, arg: int) -> None:
    """📢 Broadcast (write → attach a product → preview → send) and the 📣 announcement buttons."""
    admin_id = q.from_user.id
    if action == "bc":
        context.user_data["await"] = ("broadcast", 0)
        await q.answer()
        count = len(await DB.recipients(exclude=CFG.admin_ids))
        await edit(q, BROADCAST_PROMPT.format(count=count), one_button("✖️ Cancel", "a:home"))
    elif action == "bc_p":  # product picked (0 = none): show the admin exactly what users will get
        draft = context.user_data.get("broadcast")
        if not draft:
            await q.answer("Start again with 📢 Broadcast.", show_alert=True)
            return
        product = await DB.product(arg) if arg else None
        draft["product"] = product["id"] if product is not None else None
        await q.answer()
        markup = buy_now_markup(product["id"]) if product is not None else None
        try:
            await context.bot.copy_message(admin_id, draft["chat"], draft["msg"], reply_markup=markup)
        except TelegramError as exc:
            await send(context.bot, admin_id, f"Couldn't prepare that message ({esc(str(exc))}). Start again with 📢 Broadcast.")
            return
        count = len(await DB.recipients(exclude=CFG.admin_ids))
        await send(
            context.bot,
            admin_id,
            f"👆 <b>Preview.</b> Send it to <b>{count}</b> user(s)?",
            reply_markup=confirm_markup("a:bc_send", "a:home"),
        )
    elif action == "bc_send":
        draft = context.user_data.pop("broadcast", None)
        if not draft:
            await q.answer("Already sent, or nothing to send.", show_alert=True)
            return
        await q.answer("Sending…")
        await edit(q, "📢 <b>Sending…</b> You'll get a message when it's done.")
        context.application.create_task(run_broadcast(context.bot, admin_id, draft))
    elif action == "ann_now":
        await q.answer("Announcing a random product…")
        context.application.create_task(announce_now(context.bot, admin_id))
    else:  # ann_toggle
        on = await DB.get_setting("announce", "on") == "on"
        await DB.set_setting("announce", "off" if on else "on")
        await q.answer(f"Auto announce is now {'OFF' if on else 'ON'}.")
        text, markup = await admin_home()
        await edit(q, text, markup)


async def broadcast_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The admin sent the message to broadcast: keep it, then ask which product to attach."""
    msg = update.effective_message
    context.user_data.pop("await", None)
    context.user_data["broadcast"] = {"chat": msg.chat_id, "msg": msg.message_id, "product": None}
    rows = [
        [InlineKeyboardButton(f"🛒 {p['name']}", callback_data=f"a:bc_p:{p['id']}")]
        for p in await DB.products()
        if p["in_stock"] > 0
    ]
    rows.append([InlineKeyboardButton("➡️ No product, just the message", callback_data="a:bc_p:0")])
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data="a:home")])
    await msg.reply_text(
        "📎 <b>Attach a product?</b>\nIts <b>🛒 Buy now</b> button goes under your message.",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_admin_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo / video / GIF from an admin: only meaningful as a broadcast."""
    pending = context.user_data.get("await")
    if pending and pending[0] == "broadcast":
        await broadcast_input(update, context)


async def run_broadcast(bot, admin_id: int, draft: dict) -> None:
    markup = buy_now_markup(draft["product"]) if draft.get("product") else None
    sent, failed = await send_to_everyone(
        lambda user_id: bot.copy_message(user_id, draft["chat"], draft["msg"], reply_markup=markup)
    )
    note = f" {failed} couldn't be reached (usually: they blocked the bot)." if failed else ""
    log.info("Broadcast sent to %d users (%d failed)", sent, failed)
    text = f"📢 <b>Broadcast done.</b> Sent to {sent} user(s).{note}"
    await send(bot, admin_id, text, reply_markup=one_button(BTN_ADMIN, "a:home"))


async def announce_now(bot, admin_id: int) -> None:
    p, sent, failed = await announce_product(bot)
    if p is None:
        text = "📣 Nothing to announce: no product is in stock."
    else:
        note = f" {failed} couldn't be reached." if failed else ""
        text = f"📣 Announced <b>{esc(p['name'])}</b> to {sent} user(s).{note}"
    await send(bot, admin_id, text, reply_markup=one_button(BTN_ADMIN, "a:home"))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name, price = parse_product(command_body(update.effective_message.text))
    if price is None:
        await update.effective_message.reply_text("Usage: <code>/add Netflix Premium 1 Month | 4.99</code>")
        return
    product_id = await DB.add_product(name, price)
    await update.effective_message.reply_text(f"✅ Added product #{product_id} <b>{esc(name)}</b> at {usd(price)}.")
    await reply_product_screen(update, product_id)


async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    first, _, rest = command_body(update.effective_message.text).partition("\n")
    id_text, _, inline_item = first.strip().partition(" ")
    product_id = int_arg(id_text)
    if product_id is None:
        await update.effective_message.reply_text(STOCK_USAGE)
        return
    if await add_stock(update, context, product_id, [inline_item, *rest.splitlines()]):
        await reply_product_screen(update, product_id)


async def on_stock_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A .txt of logins: for the product the admin is adding logins to, or `/stock ID` as caption."""
    msg = update.effective_message
    pending = context.user_data.get("await")
    if pending and pending[0] == "broadcast":  # a file to broadcast, not logins
        await broadcast_input(update, context)
        return
    caption = re.match(r"^/stock(?:@\w+)?\s+([0-9]{1,9})", msg.caption or "")
    if caption:
        product_id = int(caption.group(1))
    elif pending and pending[0] == "stock":
        product_id = pending[1]
    else:
        await msg.reply_text("To add logins from a file: 🛠 Admin → the product → ➕ Add logins, then send the file.")
        return
    context.user_data.pop("await", None)
    if msg.document.file_size and msg.document.file_size > 2_000_000:
        await msg.reply_text("That file is too big (max 2 MB).")
        return
    data = bytes(await (await msg.document.get_file()).download_as_bytearray())
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        await msg.reply_text("The file must be plain UTF-8 text, one login per line.")
        return
    if await add_stock(update, context, product_id, text.splitlines()):
        await reply_product_screen(update, product_id)


async def add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, lines: list[str]) -> bool:
    msg = update.effective_message
    items = [line.strip() for line in lines if line.strip()]
    if not items:
        await msg.reply_text(STOCK_USAGE)
        return False
    result = await DB.add_stock(product_id, items)
    if result is None:
        await msg.reply_text(f"There is no product #{product_id}. Open 🛠 Admin to see your products.")
        return False
    added, skipped = result
    note = f" Skipped {skipped} duplicate(s)." if skipped else ""
    await msg.reply_text(f"✅ Added {added} login(s).{note}")
    delivered = await DB.fulfil_backorders(product_id)
    for inv, got in delivered:
        await deliver(context.bot, inv, got, heading="✅ <b>Back in stock: here is your order!</b>")
    if delivered:
        await msg.reply_text(f"📦 Delivered {len(delivered)} paid order(s) that were waiting for this stock.")
    return True


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    product_id = int_arg(args[0]) if len(args) == 2 else None
    price = parse_price(args[1]) if product_id is not None else None
    if price is None:
        await update.effective_message.reply_text("Usage: <code>/price 1 4.99</code>")
        return
    ok = await DB.set_price(product_id, price)
    await update.effective_message.reply_text(
        f"✅ Product #{product_id} now costs {usd(price)}." if ok else f"There is no product #{product_id}."
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    product_id = int_arg(args[0]) if len(args) == 1 else None
    if product_id is None:
        await update.effective_message.reply_text("Usage: <code>/clear 1</code> (deletes its unsold logins)")
        return
    removed = await DB.clear_stock(product_id)
    await update.effective_message.reply_text(f"🗑 Deleted {removed} unsold login(s) from product #{product_id}.")


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    product_id = int_arg(args[0]) if len(args) == 1 else None
    if product_id is None:
        await update.effective_message.reply_text("Usage: <code>/del 1</code>")
        return
    removed = await DB.delete_product(product_id)
    if removed is None:
        await update.effective_message.reply_text(f"There is no product #{product_id}.")
        return
    await update.effective_message.reply_text(
        f"🗑 Product #{product_id} removed from the shop ({removed} unsold login(s) deleted). "
        "Orders already opened for it can still complete."
    )


# ---- startup ----------------------------------------------------------------------------------

USER_COMMANDS = [BotCommand("start", "Shop"), BotCommand("orders", "My orders"), BotCommand("help", "How to buy")]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("admin", "Admin panel"),
    BotCommand("add", "Add a product: Name | price"),
    BotCommand("stock", "Add logins to a product"),
    BotCommand("price", "Change a price"),
    BotCommand("clear", "Delete unsold logins"),
    BotCommand("del", "Remove a product"),
]


async def on_startup(app: Application) -> None:
    await DB.open()
    await app.bot.set_my_commands(USER_COMMANDS)
    for admin_id in CFG.admin_ids:
        try:
            await app.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id))
        except TelegramError as exc:
            log.warning("Admin commands not set for %s (has this admin started the bot?): %s", admin_id, exc)
    app.job_queue.run_repeating(payments_job, interval=payments.POLL_SECONDS, first=2, name="payments")
    if CFG.announce_minutes is not None:
        schedule_announcement(app.job_queue)
    log.info("@%s is running. Watching %s for USDT (BEP20).", app.bot.username, CFG.wallet_address)


async def on_shutdown(app: Application) -> None:
    await CHAIN.close()
    await DB.close()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error while handling an update", exc_info=context.error)


class _RedactToken(logging.Formatter):
    """Keeps the bot token out of every log line, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record).replace(CFG.bot_token, "<BOT_TOKEN>")


def main() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_RedactToken("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # httpx logs every request URL at INFO, and Telegram API URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    builder = (
        ApplicationBuilder()
        .token(CFG.bot_token)
        .defaults(Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)))
        .concurrent_updates(True)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
    )
    if CFG.proxy:
        builder = builder.proxy(CFG.proxy).get_updates_proxy(CFG.proxy)
    app = builder.build()

    private = filters.ChatType.PRIVATE
    admin = private & filters.User(user_id=CFG.admin_ids)
    app.add_handler(TypeHandler(Update, track_user), group=-1)  # runs first, for every update
    app.add_handlers(
        [
            CommandHandler("start", cmd_start, filters=private),
            CommandHandler("orders", cmd_orders, filters=private),
            CommandHandler("help", cmd_help, filters=private),
            CommandHandler("admin", cmd_admin, filters=admin),
            CommandHandler("add", cmd_add, filters=admin),
            CommandHandler("stock", cmd_stock, filters=admin),
            CommandHandler("price", cmd_price, filters=admin),
            CommandHandler("clear", cmd_clear, filters=admin),
            CommandHandler("del", cmd_del, filters=admin),
            MessageHandler(admin & filters.Document.ALL, on_stock_file),
            MessageHandler(admin & (filters.PHOTO | filters.VIDEO | filters.ANIMATION), on_admin_media),
            MessageHandler(private & filters.TEXT, on_text),  # menu buttons, admin answers, else the shop
            CallbackQueryHandler(cb_home, pattern=r"^home$"),
            CallbackQueryHandler(cb_product, pattern=r"^p:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_buy, pattern=r"^b:[0-9]{1,9}:[0-9]{1,3}$"),
            CallbackQueryHandler(cb_quantity, pattern=r"^q:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_buy_now, pattern=r"^n:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_check, pattern=r"^c:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_cancel, pattern=r"^x:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_orders, pattern=r"^orders$"),
            CallbackQueryHandler(cb_help, pattern=r"^help$"),
            CallbackQueryHandler(
                cb_admin,
                pattern=r"^a:(home|new|users|users_file|bc|bc_send|ann_now|ann_toggle"
                r"|(p|stock|price|clear|del|clear_yes|del_yes|bc_p):[0-9]{1,9})$",
            ),
            CallbackQueryHandler(cb_outdated),
        ]
    )
    app.add_error_handler(on_error)
    try:
        app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
    except InvalidToken:
        sys.exit("Telegram rejected BOT_TOKEN. Get a valid token from @BotFather and put it in .env.")


if __name__ == "__main__":
    main()
