"""Telegram shop bot: sells logins (digital goods) for USDT BEP20 and delivers them by itself.

Run:  python bot.py        Settings: .env (see .env.example)
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
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
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, InvalidToken, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
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
MAX_ORDERS_PER_HOUR = 10
EXPIRE_GRACE = 60  # seconds past the deadline before an order closes, for last-second payments
RPC_ALERT_AFTER = 8  # failed payment checks in a row (~2 min) before the admins are alerted
MAX_TEXT = 3900  # stay under Telegram's 4096-character message limit

esc = html.escape
SHOP_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Shop", callback_data="home")]])
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


async def send_long(bot, chat_id: int, text: str) -> None:
    """Send text that may exceed one message, split at line breaks."""
    chunk = ""
    for line in text.split("\n"):
        if chunk and len(chunk) + len(line) + 1 > MAX_TEXT:
            await send(bot, chat_id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await send(bot, chat_id, chunk)


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


async def shop_screen() -> tuple[str, InlineKeyboardMarkup]:
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
    rows.append(
        [InlineKeyboardButton("📦 My orders", callback_data="orders"), InlineKeyboardButton("❓ Help", callback_data="help")]
    )
    text = (
        f"👋 Welcome to <b>{esc(CFG.store_name)}</b>!\n\n"
        "Pick a product. You pay with <b>USDT (BEP20)</b> and your login is delivered right here, "
        "automatically, as soon as the payment confirms."
    )
    if not products:
        text += "\n\n<i>Nothing for sale yet. Check back soon!</i>"
    return text, InlineKeyboardMarkup(rows)


def product_screen(p: Row) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"<b>{esc(p['name'])}</b>", "", f"💵 Price: <b>{usd(p['price_cents'])}</b> each"]
    if CFG.fee_cents:
        lines.append(f"➕ Fee: {usd(CFG.fee_cents)} per order")
    lines.append(f"📦 In stock: {p['in_stock']}")
    buttons = [
        InlineKeyboardButton(f"{q} · {usd(p['price_cents'] * q + CFG.fee_cents)}", callback_data=f"b:{p['id']}:{q}")
        for q in QTY_CHOICES
        if q <= p["in_stock"]
    ]
    lines += ["", "How many would you like?" if buttons else "😕 Sold out right now. Check back later!"]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="home")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


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
        f"⏱ Pay within <b>{minutes} min</b>. Your item is reserved until then.\n"
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
        "1. Tap /start and pick a product and a quantity.\n"
        "2. Send the exact USDT amount shown to the address shown, on <b>BNB Smart Chain (BEP20)</b>.\n"
        "3. Your login is delivered here automatically, usually within a minute.\n\n"
        "The last digits of the amount identify your payment, so always send it exactly. "
        "Your past orders are in /orders.\n\n" + support_line()
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
    text = f"{head}\n\n🔑 <b>{label}:</b>\n{logins}\n\nSaved in /orders in case you need it again."
    if len(text) <= MAX_TEXT:
        await send(bot, inv["user_id"], text)
        return
    await send(bot, inv["user_id"], f"{head}\n\n🔑 Your logins are in the file below. Also saved in /orders.")
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
            "The payment time ran out and the item went back on sale. Already sent the money? Don't worry: "
            f"payments are still recognised for {payments.LATE_MINUTES // 60} more hours and delivered "
            "automatically while in stock.",
            SHOP_BUTTON,
        )


# ---- buyer handlers ---------------------------------------------------------------------------

BUY_ERRORS = {
    "rate": "You opened too many orders in the last hour. Please try again later.",
    "gone": "This product is no longer available.",
    "stock": "Not enough stock for that quantity any more.",
    "busy": "Too many people are paying this exact price right now. Please try again in a few minutes.",
}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, markup = await shop_screen()
    await update.effective_message.reply_text(text, reply_markup=markup)


async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    text, markup = await shop_screen()
    await edit(update.callback_query, text, markup)


async def cb_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    product = await DB.product(int(q.data.split(":")[1]))
    if product is None:
        await q.answer("This product is no longer available.", show_alert=True)
        text, markup = await shop_screen()
    else:
        await q.answer()
        text, markup = product_screen(product)
    await edit(q, text, markup)


async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    _, product_id, qty = q.data.split(":")
    product_id, qty = int(product_id), int(qty)
    if qty not in QTY_CHOICES:
        await q.answer()
        return
    outcome, inv = await DB.create_invoice(
        user_id=q.from_user.id,
        buyer=buyer_name(q.from_user),
        product_id=product_id,
        qty=qty,
        fee_cents=CFG.fee_cents,
        pay_seconds=CFG.payment_minutes * 60,
        reserve_seconds=RESERVE_SECONDS,
        max_per_hour=MAX_ORDERS_PER_HOUR,
        pick_amount=payments.pick_amount,
    )
    if outcome in ("created", "open"):
        if outcome == "open":
            await q.answer("You already have an open order. Pay or cancel it first.", show_alert=True)
        else:
            await q.answer()
            log.info("Order #%s opened by %s: %s USDT", inv["id"], q.from_user.id, payments.usdt(inv["amount_units"]))
        text, markup = invoice_screen(inv)
        message_id = None
        if outcome == "created" and q.message is not None:
            try:
                await q.edit_message_text(text, reply_markup=markup)
                message_id = q.message.message_id
            except TelegramError:
                pass
        if message_id is None:  # never leave a buyer without the payment details
            msg = await send(context.bot, q.from_user.id, text, reply_markup=markup)
            message_id = msg.message_id if msg else None
        if message_id:
            await DB.set_invoice_message(inv["id"], message_id)
        return
    await q.answer(BUY_ERRORS[outcome], show_alert=True)
    product = await DB.product(product_id)
    text, markup = product_screen(product) if product is not None else await shop_screen()
    await edit(q, text, markup)


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
        await send(bot, user_id, "📦 No orders yet. Tap /start to shop.")
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
        await send(bot, user_id, text)
        return
    plain = "\n\n".join(f"#{inv['id']} {inv['product_name']} x{inv['qty']}\n" + "\n".join(items) for inv, items in orders)
    await send(bot, user_id, "📦 Your latest orders are in the file below.")
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
    text, markup = await shop_screen()
    await send(context.bot, update.effective_user.id, text, reply_markup=markup)


# ---- admin handlers (only reachable by ADMIN_IDS, enforced by the handler filters) ------------

ADMIN_HELP = (
    "<b>Commands</b>\n"
    "/add Name | price: new product\n"
    "/stock ID, then one login per line below it (or a .txt file with caption <code>/stock ID</code>)\n"
    "/price ID price: change a price\n"
    "/clear ID: delete a product's unsold logins\n"
    "/del ID: remove a product"
)
STOCK_USAGE = (
    "Put the logins below the command, one per line:\n"
    "<pre>/stock 1\nemail1:password1\nemail2:password2</pre>\n"
    "Or send a .txt file (one login per line) with the caption <code>/stock 1</code>."
)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    products = await DB.products()
    s = await DB.summary()
    lines = ["🛠 <b>Admin</b>", ""]
    lines += [
        f"#{p['id']} {esc(p['name'])}: {usd(p['price_cents'])} · {p['in_stock']} in stock · {p['sold']} sold"
        for p in products
    ] or ["No products yet."]
    lines += [
        "",
        f"Open orders: {s['open_orders']} · Paid, waiting for stock: {s['backorders']}",
        f"Last 24h: {s['sales']} sales · {payments.usdt(s['units'])} USDT",
        "",
        ADMIN_HELP,
    ]
    await send_long(context.bot, update.effective_chat.id, "\n".join(lines))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name, sep, price_text = command_body(update.effective_message.text).rpartition("|")
    name = " ".join(name.split())
    price = parse_price(price_text) if sep else None
    if not name or len(name) > 64 or price is None:
        await update.effective_message.reply_text("Usage: <code>/add Netflix Premium 1 Month | 4.99</code>")
        return
    product_id = await DB.add_product(name, price)
    await update.effective_message.reply_text(
        f"✅ Added product #{product_id} <b>{esc(name)}</b> at {usd(price)}.\n\n"
        f"Now add its logins, one per line:\n<pre>/stock {product_id}\nemail1:password1\nemail2:password2</pre>"
    )


async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    first, _, rest = command_body(update.effective_message.text).partition("\n")
    id_text, _, inline_item = first.strip().partition(" ")
    product_id = int_arg(id_text)
    if product_id is None:
        await update.effective_message.reply_text(STOCK_USAGE)
        return
    await add_stock(update, context, product_id, [inline_item, *rest.splitlines()])


async def on_stock_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    product_id = int_arg(command_body(msg.caption).split()[0])
    if product_id is None:
        await msg.reply_text(STOCK_USAGE)
        return
    if msg.document.file_size and msg.document.file_size > 2_000_000:
        await msg.reply_text("That file is too big (max 2 MB).")
        return
    data = bytes(await (await msg.document.get_file()).download_as_bytearray())
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        await msg.reply_text("The file must be plain UTF-8 text, one login per line.")
        return
    await add_stock(update, context, product_id, text.splitlines())


async def add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, lines: list[str]) -> None:
    msg = update.effective_message
    items = [line.strip() for line in lines if line.strip()]
    if not items:
        await msg.reply_text(STOCK_USAGE)
        return
    result = await DB.add_stock(product_id, items)
    if result is None:
        await msg.reply_text(f"There is no product #{product_id}. See /admin.")
        return
    added, skipped = result
    note = f" Skipped {skipped} duplicate(s)." if skipped else ""
    await msg.reply_text(f"✅ Added {added} login(s) to product #{product_id}.{note}")
    delivered = await DB.fulfil_backorders(product_id)
    for inv, got in delivered:
        await deliver(context.bot, inv, got, heading="✅ <b>Back in stock: here is your order!</b>")
    if delivered:
        await msg.reply_text(f"📦 Delivered {len(delivered)} paid order(s) that were waiting for this stock.")


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
    BotCommand("admin", "Products, stock and sales"),
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
            MessageHandler(admin & filters.Document.ALL & filters.CaptionRegex(r"^/stock(@\w+)?\s+[0-9]+"), on_stock_file),
            MessageHandler(private & filters.TEXT, cmd_start),  # anything else shows the shop
            CallbackQueryHandler(cb_home, pattern=r"^home$"),
            CallbackQueryHandler(cb_product, pattern=r"^p:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_buy, pattern=r"^b:[0-9]{1,9}:[0-9]{1,2}$"),
            CallbackQueryHandler(cb_check, pattern=r"^c:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_cancel, pattern=r"^x:[0-9]{1,9}$"),
            CallbackQueryHandler(cb_orders, pattern=r"^orders$"),
            CallbackQueryHandler(cb_help, pattern=r"^help$"),
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
