"""Broadcast delivery primitives + stock/price announcement flow.

Stock / price events notify nobody on their own. Instead the acting admin gets
an Approve / Deny card; on Approve the message is broadcast to everyone who has
used the bot. The full text is built when the event happens (so it can include
before/after details like old → new price) and stashed in bot_data under a
short id carried in the callback_data; on Approve it is sent verbatim.
"""
import asyncio
import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter
from telegram.ext import ContextTypes

from app import config, db
from app.formatting import cemoji, esc, render_name, usdt
from app.keyboards import _btn, buy_now_btn
from app.render import _edit_admin_msg

logger = logging.getLogger(__name__)


async def _track_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record every (non-bot) user who interacts, so they can be broadcast to."""
    user = update.effective_user
    if user and not user.is_bot:
        try:
            await db.record_user(user.id, user.first_name or "", user.username or "")
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not record user %s: %s", user.id, e)


async def expire_offers(context) -> None:
    """Background job: revert any product whose discount window has passed.
    No broadcast on expiry — only the start of a sale is announced."""
    cleared = await db.clear_expired_offers()
    if cleared:
        logger.info("Expired %d discount(s): %s", len(cleared), [r["id"] for r in cleared])


def _is_restock(old_stock, new_stock) -> bool:
    """True when a stock edit makes a product MORE available (a restock)."""
    if old_stock is None or old_stock == new_stock:
        return False
    if old_stock == db.UNLIMITED_STOCK:
        return False  # was already unlimited — can't become more available
    if new_stock == db.UNLIMITED_STOCK:
        return True   # finite -> unlimited
    return new_stock > old_stock


def _announcement_text(kind: str, product) -> str:
    name = render_name(product)
    price = esc(usdt(product["price"]))
    if kind == "new":
        return (
            f"{cemoji('new', '🆕')} <b>New in the store!</b>\n\n"
            f"<b>{name}</b> — {price}\n\n"
            f"Tap {cemoji('cart', '🛍')} Browse products to grab it."
        )
    if kind == "restock":
        stock = product["stock"]
        avail = "now available" if stock == db.UNLIMITED_STOCK else f"{stock} in stock"
        return (
            f"{cemoji('restock', '📦')} <b>Back in stock!</b>\n\n"
            f"<b>{name}</b> is {avail} again — {price}.\n\n"
            f"Tap {cemoji('cart', '🛍')} Browse products before it's gone."
        )
    return f"{cemoji('warn', '⚠️')} <b>Sold out</b>\n\n<b>{name}</b> is now out of stock."


def _price_change_text(name, old_usdt, new_usdt, increased: bool) -> str:
    """Crystal-clear price-change announcement: product name + old → new price
    (USDT), with emojis. `name` must already be HTML-safe — pass render_name(p),
    not a raw string."""
    arrow = cemoji('up', '🔺') if increased else cemoji('down', '🔻')
    return (
        f"{cemoji('bell', '🔔')} <b>Price updated!</b> {arrow}\n\n"
        f"{cemoji('restock', '📦')} <b>{name}</b>\n\n"
        f"{cemoji('money', '💰')} Old price: <s>{esc(usdt(old_usdt))}</s>\n"
        f"{cemoji('new', '🆕')} New price: <b>{esc(usdt(new_usdt))}</b>\n\n"
        f"{cemoji('cart', '🛍')} Tap Browse products to check it out!"
    )


def _sale_text(name: str, old_usdt, offer_usdt, duration_label: str) -> str:
    """Flash-sale announcement: struck-through old price -> discounted price,
    with the human-readable duration (e.g. '24h', '3d'). `name` must already
    be HTML-safe — pass render_name(p), not a raw string."""
    return (
        f"{cemoji('sale', '🔥')} <b>Flash Sale!</b> {cemoji('sale', '🔥')}\n\n"
        f"<b>{name}</b>\n"
        f"{cemoji('money', '💰')} Was: <s>{esc(usdt(old_usdt))}</s>\n"
        f"{cemoji('new', '🎉')} Now: <b>{esc(usdt(offer_usdt))}</b>\n\n"
        f"{cemoji('bell', '⏰')} Ends in <b>{esc(duration_label)}</b> — grab it before it's gone!\n\n"
        f"{cemoji('cart', '🛍')} Tap Browse products to buy now."
    )


def _stash_announcement(context, text: str, product_id: int = None) -> int:
    """Store a ready-to-send announcement (+ optional product for a Buy now button);
    return its short id for the callback."""
    store = context.bot_data.setdefault("pending_ann", {})
    ann_id = context.bot_data.get("ann_seq", 0) + 1
    context.bot_data["ann_seq"] = ann_id
    store[ann_id] = {"text": text, "pid": product_id}
    return ann_id


async def _offer_announcement(
    context, admin_chat_id: int, label: str, text: str, product_id: int = None
) -> None:
    """Ask one admin whether to broadcast `text`. It is stashed and sent verbatim
    on Approve (with a Buy now button when product_id is given)."""
    if not text:
        return
    ann_id = _stash_announcement(context, text, product_id)
    kb = InlineKeyboardMarkup(
        [[
            _btn("✅ Approve & send", "check", callback_data=f"ann:ok:{ann_id}"),
            _btn("❌ Deny", "reject", callback_data=f"ann:no:{ann_id}"),
        ]]
    )
    try:
        await context.bot.send_message(
            admin_chat_id,
            f"{cemoji('megaphone', '📣')} <b>Announcement — {esc(label)}</b>\n"
            "Broadcast this to everyone who uses the bot?\n\n"
            f"——————\n{text}\n——————",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not offer announcement to %s: %s", admin_chat_id, e)


# --- Bulk delivery for broadcasts & announcements ----------------------- #
# Telegram allows a bot ~30 messages/sec to different users. We send each wave
# concurrently and pause between waves to stay safely under that, instead of the
# old strictly one-at-a-time loop (which made big broadcasts slow).
BROADCAST_BATCH = 20       # users delivered concurrently per wave
BROADCAST_PAUSE = 1.0      # seconds between waves  ->  ~BROADCAST_BATCH msgs/sec


async def _bulk_deliver(recipients: list, send_one) -> tuple[int, int]:
    """Deliver to many users quickly but safely.

    ``send_one(uid)`` is an async callable that delivers to ONE user. Each wave
    is sent concurrently (``asyncio.gather``) and we pause between waves to
    respect Telegram's rate limit. Flood control (``RetryAfter``) is honoured —
    we wait the requested time and retry that user once. Returns (sent, failed).
    """
    sent = failed = 0

    async def _deliver_one(uid: int) -> None:
        nonlocal sent, failed
        try:
            await send_one(uid)
            sent += 1
        except RetryAfter as exc:
            # Flood control: back off the requested time, then retry once.
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await send_one(uid)
                sent += 1
            except Exception as exc2:  # noqa: BLE001
                failed += 1
                logger.debug("Broadcast retry to %s failed: %s", uid, exc2)
        except Exception as exc:  # noqa: BLE001  (blocked the bot, deactivated, etc.)
            failed += 1
            logger.debug("Broadcast to %s failed: %s", uid, exc)

    for i in range(0, len(recipients), BROADCAST_BATCH):
        wave = recipients[i:i + BROADCAST_BATCH]
        await asyncio.gather(*(_deliver_one(uid) for uid in wave))
        if i + BROADCAST_BATCH < len(recipients):
            await asyncio.sleep(BROADCAST_PAUSE)
    return sent, failed


async def _broadcast(context, text: str, reply_markup=None) -> tuple[int, int]:
    """Send `text` (with optional buttons) to every recipient except admins."""
    recipients = [uid for uid in await db.all_recipient_ids() if not config.is_admin(uid)]

    async def send_one(uid: int) -> None:
        await context.bot.send_message(
            uid, text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )

    return await _bulk_deliver(recipients, send_one)


async def announce_decide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the admin's Approve / Deny tap on an announcement card."""
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    _, decision, ann_id = query.data.split(":")
    await query.answer()
    entry = context.bot_data.get("pending_ann", {}).pop(int(ann_id), None)
    if decision == "no":
        await _edit_admin_msg(query, f"{cemoji('block', '🚫')} Announcement discarded — nothing was sent.")
        return
    if entry is None:
        await _edit_admin_msg(
            query, f"{cemoji('warn', '⚠️')} This announcement expired (the bot may have restarted). Please trigger it again."
        )
        return
    text = entry["text"]
    pid = entry.get("pid")
    # Attach a Buy now button when the product is still buyable.
    markup = None
    if pid:
        prod = await db.get_product(pid)
        if prod and prod["active"]:
            stock = await db.get_effective_stock(pid, prod["stock"])
            if stock == db.UNLIMITED_STOCK or stock > 0:
                markup = InlineKeyboardMarkup([[buy_now_btn(prod)]])
    await _edit_admin_msg(query, f"{cemoji('megaphone', '📣')} Sending announcement…")
    sent, failed = await _broadcast(context, text, markup)
    tail = f" ({failed} couldn't be reached)" if failed else ""
    await _edit_admin_msg(query, f"{cemoji('check', '✅')} Announcement sent to <b>{sent}</b> user(s){tail}.")
