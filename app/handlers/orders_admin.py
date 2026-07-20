"""Admin: order listings, buyer's own orders, users, and earnings."""
from datetime import datetime

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app import config, db
from app.formatting import IST, _format_usdt, _order_no, cemoji, esc, order_amount_str, qty_suffix, usdt
from app.keyboards import _btn, refresh_menu_kb
from app.render import _render, _send


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.is_admin(update.effective_user.id):
        await _render(update, f"{cemoji('block', '🚫')} Not authorized.")
        return
    await _render(
        update,
        f"{cemoji('receipt', '🧾')} <b>Orders</b> — choose a view:",
        InlineKeyboardMarkup([
            [_btn("📋 Active orders", "clipboard", callback_data="orders:active")],
            [_btn("🗑 Abandoned carts", "trash", callback_data="orders:abandoned")],
            [_btn("⬅️ Menu", "menu", callback_data="menu:home")],
        ]),
    )


async def admin_orders_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    await query.answer()
    orders = await db.list_orders(limit=30, include_created=False)
    if not orders:
        await _render(update, f"{cemoji('clipboard', '📋')} No active orders yet.", refresh_menu_kb("orders:active"))
        return
    icons = {
        db.STATUS_PENDING: cemoji('clock', '🕒'),
        db.STATUS_APPROVED: cemoji('check', '✅'),
        db.STATUS_REJECTED: cemoji('reject', '❌'),
    }

    def _line(o) -> str:
        icon = icons.get(o["status"], "•")
        uname = f"@{o['username']}" if o["username"] else f"id:{o['user_id']}"
        try:
            dt = datetime.fromisoformat(o["created_at"]).astimezone(IST).strftime("%d%m%y %H:%M")
        except Exception:
            dt = o["created_at"][:16]
        return (
            f"{icon} <b>{esc(_order_no(o))}</b> · {esc(o['product_name'])}{esc(qty_suffix(o))} · "
            f"{esc(order_amount_str(o))} · {esc(uname)} · {dt}"
        )

    pending = [o for o in orders if o["status"] == db.STATUS_PENDING]
    auto_approved = [o for o in orders if o["status"] != db.STATUS_PENDING and o["method"] == "Wallet"]
    reviewed = [o for o in orders if o["status"] != db.STATUS_PENDING and o["method"] != "Wallet"]

    lines = [f"{cemoji('clipboard', '📋')} <b>Active orders</b>"]
    for heading, group, empty_text in (
        (f"{cemoji('clock', '🕒')} Needs review", pending, "Nothing waiting."),
        (f"{cemoji('bolt', '⚡')} Auto-approved (wallet)", auto_approved, "None yet."),
        (f"{cemoji('check', '✅')} Reviewed by you", reviewed, "None yet."),
    ):
        lines.append(f"\n<b>{heading}</b>")
        if group:
            lines.extend(_line(o) for o in group)
        else:
            lines.append(empty_text)

    await _render(update, "\n".join(lines), refresh_menu_kb("orders:active"))


async def admin_orders_abandoned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    await query.answer()
    orders = await db.list_abandoned_orders(limit=50)
    if not orders:
        await _render(update, f"{cemoji('trash', '🗑')} No abandoned carts.", refresh_menu_kb("orders:abandoned"))
        return
    lines = [f"{cemoji('trash', '🗑')} <b>Abandoned carts</b> ({len(orders)})\n"]
    for o in orders:
        uname = f"@{o['username']}" if o["username"] else f"id:{o['user_id']}"
        try:
            dt = datetime.fromisoformat(o["created_at"]).astimezone(IST).strftime("%d%m%y %H:%M")
        except Exception:
            dt = o["created_at"][:16]
        lines.append(
            f"• <b>{esc(_order_no(o))}</b> · {esc(o['product_name'])}{esc(qty_suffix(o))} · "
            f"{esc(order_amount_str(o))} · {esc(uname)} · {dt}"
        )
    await _render(update, "\n".join(lines), refresh_menu_kb("orders:abandoned"))


async def show_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A buyer's own orders (filtered to their user id)."""
    orders = await db.list_orders(
        user_id=update.effective_user.id, include_created=False, limit=20
    )
    if not orders:
        await _render(
            update,
            f"{cemoji('receipt', '🧾')} <b>My orders</b>\n\nYou have no orders yet.\n"
            f"Tap {cemoji('cart', '🛍')} <b>Browse products</b> to buy something.",
            refresh_menu_kb("myorders"),
        )
        return
    icons = {
        db.STATUS_PENDING: cemoji('clock', '🕒'),
        db.STATUS_APPROVED: cemoji('check', '✅'),
        db.STATUS_REJECTED: cemoji('reject', '❌'),
    }
    labels = {
        db.STATUS_PENDING: "awaiting confirmation",
        db.STATUS_APPROVED: "delivered",
        db.STATUS_REJECTED: "rejected",
    }
    lines = [f"{cemoji('receipt', '🧾')} <b>My orders</b>\n"]
    for o in orders:
        icon = icons.get(o["status"], "•")
        lines.append(
            f"{icon} <b>{esc(_order_no(o))}</b> · {esc(o['product_name'])}{esc(qty_suffix(o))} · "
            f"{esc(order_amount_str(o))} · {esc(labels.get(o['status'], o['status']))}"
        )
        reason = o["reason"] if "reason" in o.keys() else ""
        if o["status"] == db.STATUS_REJECTED and reason:
            lines.append(f"    ↳ <i>{esc(reason)}</i>")
    await _render(update, "\n".join(lines), refresh_menu_kb("myorders"))


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main-menu 'Profile' button: a buyer's own account summary."""
    user = update.effective_user
    u = await db.get_user(user.id)
    try:
        started = datetime.fromisoformat(u["started_at"]).astimezone(IST).strftime("%d %b %Y")
    except Exception:  # noqa: BLE001
        started = "just now"
    purchases = await db.count_user_orders(user.id, db.STATUS_APPROVED)
    balance = _format_usdt(await db.get_wallet_balance(user.id))
    uname = f"@{user.username}" if user.username else f"id:{user.id}"
    text = (
        f"{cemoji('person', '👤')} <b>{esc(user.first_name or '')}</b> {esc(uname)}\n\n"
        f"{cemoji('id', '🆔')} ID: <code>{user.id}</code>\n"
        f"{cemoji('calendar', '📅')} Member since: <b>{esc(started)}</b>\n"
        f"{cemoji('receipt', '🧾')} Purchases: <b>{purchases}</b>\n"
        f"{cemoji('money', '💰')} Wallet balance: <b>{esc(balance)} USDT</b>"
    )
    await _render(update, text, refresh_menu_kb("menu:profile"))


async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.is_admin(update.effective_user.id):
        await _send(update, f"{cemoji('block', '🚫')} Not authorized.")
        return
    users = await db.all_users()
    if not users:
        await _render(update, f"{cemoji('people', '👥')} No users tracked yet.",
                      refresh_menu_kb("menu:users"))
        return
    users = sorted(users, key=lambda u: u["started_at"] or "", reverse=True)

    # Build tappable button list (paginated like products)
    page_size = 15
    q = update.callback_query
    page = 0
    if q and ":" in q.data:
        try:
            page = int(q.data.split(":")[-1])
        except (ValueError, IndexError):
            page = 0

    total = len(users)
    start = page * page_size
    page_users = users[start: start + page_size]

    rows = []
    for u in page_users:
        uname = f"@{u['username']}" if u["username"] else f"id:{u['user_id']}"
        name  = (u["first_name"] or "").strip()
        label = f"{name} {uname}".strip()

        try:
            dt = datetime.fromisoformat(u["started_at"]).astimezone(IST).strftime("%d/%m/%y %H:%M")
        except Exception:
            dt = ""

        dt_suffix = f" · {dt}" if dt else ""
        max_prefix_len = 45 - len(dt_suffix)
        if len(label) > max_prefix_len:
            label = label[:max_prefix_len - 1].rstrip() + "…"

        label = f"👤 {label}{dt_suffix}"
        rows.append([_btn(label, "person", callback_data=f"user_detail:{u['user_id']}")])

    nav = []
    if page > 0:
        nav.append(_btn("⬅️ Prev", "prev", callback_data=f"menu:users:{page - 1}"))
    if start + page_size < total:
        nav.append(_btn("Next ➡️", "next", callback_data=f"menu:users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        _btn("🔄 Refresh", "refresh", callback_data=f"menu:users:{page}"),
        _btn("⬅️ Menu", "menu", callback_data="menu:home"),
    ])

    total_uses = sum((u["clicks"] if "clicks" in u.keys() else 0) for u in users)
    header = (
        f"{cemoji('people', '👥')} <b>Users</b> — <b>{total}</b> total · <b>{total_uses}</b> uses\n"
        "Tap a user to see their details."
    )
    await _render(update, header, InlineKeyboardMarkup(rows))


async def show_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a single user's basic info (admin)."""
    query = update.callback_query
    if not config.is_admin(update.effective_user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return
    uid = int(query.data.split(":")[1])
    u = None
    for row in await db.all_users():
        if row["user_id"] == uid:
            u = row
            break
    if not u:
        await _render(update, f"{cemoji('warn', '⚠️')} User not found.", refresh_menu_kb("menu:users"))
        return

    uname = f"@{u['username']}" if u["username"] else f"id:{uid}"
    clicks = u["clicks"] if "clicks" in u.keys() else 0
    try:
        started = datetime.fromisoformat(u["started_at"]).astimezone(IST).strftime("%d %b %Y")
    except Exception:  # noqa: BLE001
        started = (u["started_at"] or "")[:10]

    text = (
        f"{cemoji('person', '👤')} <b>{esc(u['first_name'] or '')}</b> {esc(uname)}\n\n"
        f"{cemoji('id', '🆔')} ID: <code>{uid}</code>\n"
        f"{cemoji('mouse', '🖱')} Uses: <b>{clicks}</b>\n"
        f"{cemoji('calendar', '📅')} First seen: <b>{esc(started)}</b>"
    )
    kb = InlineKeyboardMarkup(
        [[_btn("⬅️ Back to users", "back", callback_data="menu:users")]]
    )
    await _render(update, text, kb)


async def show_earnings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.is_admin(update.effective_user.id):
        await _send(update, f"{cemoji('block', '🚫')} Not authorized.")
        return
    s = await db.earnings_summary()
    text = (
        f"{cemoji('chart', '📈')} <b>Earnings</b>\n\n"
        f"Approved orders: <b>{s['count']}</b>\n"
        f"Total: <b>{esc(usdt(s['usdt']))}</b>"
    )
    await _render(update, text, refresh_menu_kb("menu:earnings"))
