"""Every keyboard builder, button-label constant, and nav filter."""
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import filters

from app import config, db
from app.formatting import truncate_safe, usdt

# --- Persistent reply keyboard (stays below the text box until replaced) ------ #
BTN_BROWSE = "🛍 Browse products"
BTN_MYORDERS = "🧾 My orders"
BTN_HELP = "ℹ️ Help"
BTN_ADD = "➕ Add product"
BTN_PRODUCTS = "📦 Products"
BTN_ORDERS = "🧾 All orders"
BTN_USERS = "👥 Users"
BTN_BROADCAST = "📢 Broadcast"
BTN_SUPPORT = "💬 Customer support"
BTN_TOPUP = "💰 Top up"
BTN_EARNINGS = "📈 Earnings"

# Pure-navigation buttons (tap -> a screen, handled by _nav_router).
NAV_NAV = filters.Text([BTN_BROWSE, BTN_MYORDERS, BTN_HELP, BTN_PRODUCTS, BTN_ORDERS, BTN_USERS, BTN_SUPPORT, BTN_EARNINGS])
# All reply-keyboard buttons. Excluded from conversation text input so a tap is
# never mistaken for a typed name / price / UTR. (Add product & Broadcast start
# their own conversations via dedicated entry points.)
NAV_FILTER = NAV_NAV | filters.Text([BTN_ADD, BTN_BROADCAST, BTN_TOPUP])


def _btn(text: str, key: str = None, **kwargs) -> InlineKeyboardButton:
    """InlineKeyboardButton with an optional premium custom-emoji icon.

    `key` looks up config.CUSTOM_EMOJI_IDS; unset/unknown keys just mean no
    icon (Telegram's own default), so every existing button keeps working
    unchanged until real IDs are configured — see app/handlers/menu.py's
    emoji_id_lookup for how to get them.
    """
    icon = config.CUSTOM_EMOJI_IDS.get(key) if key else None
    if icon:
        # icon_custom_emoji_id already renders an icon before the label, so drop
        # the hardcoded Unicode emoji prefix to avoid showing both.
        text = re.sub(r"^\S+\s+", "", text, count=1)
    return InlineKeyboardButton(text, icon_custom_emoji_id=icon, **kwargs)


def refresh_menu_kb(refresh_cb: str) -> InlineKeyboardMarkup:
    """A '🔄 Refresh' (re-renders the current view) + '⬅️ Menu' row."""
    return InlineKeyboardMarkup(
        [
            [
                _btn("🔄 Refresh", "refresh", callback_data=refresh_cb),
                _btn("⬅️ Menu", "menu", callback_data="menu:home"),
            ]
        ]
    )


def _support_btn() -> InlineKeyboardButton:
    """Opens the configured support chat directly, or — if none is set — tells
    the buyer so via an alert (see menu.support_not_set) instead of hiding the
    button, so the main menu always has the same five customer buttons."""
    support = config.support_url()
    if support:
        return _btn("💬 Contact support", "support", url=support)
    return _btn("💬 Contact support", "support", callback_data="menu:support")


def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Role-aware menu. Customers get a fixed 5-button layout: Buy up top, then
    Balance/Profile and My orders/Support in two rows. Admins get manage tools
    appended below that."""
    rows = [[_btn("🛍 Buy products", "browse", callback_data="catalog")]]
    if config.is_auto_mode():
        rows.append([
            _btn("💰 Balance", "topup", callback_data="menu:balance"),
            _btn("🙂 Profile", "profile", callback_data="menu:profile"),
        ])
    else:
        rows.append([_btn("🙂 Profile", "profile", callback_data="menu:profile")])
    rows.append([
        _btn("🧾 My orders", "myorders", callback_data="myorders"),
        _support_btn(),
    ])
    if config.is_admin(user_id):
        rows += [
            [_btn("➕ Add product", "add", callback_data="menu:add")],
            [
                _btn("📦 Products", "products", callback_data="menu:products"),
                _btn("📊 Stock", "restock", callback_data="menu:stock"),
            ],
            [
                _btn("🧾 All orders", "all_orders", callback_data="menu:orders"),
            ],
            [_btn("👥 Users", "users", callback_data="menu:users")],
            [_btn("📢 Broadcast", "broadcast", callback_data="menu:broadcast")],
            [_btn("📈 Earnings", "earnings", callback_data="menu:earnings")],
        ]
    rows.append([_btn("ℹ️ Help", "help", callback_data="menu:help")])
    return InlineKeyboardMarkup(rows)


def main_reply_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    """The always-visible menu under the text box. Buttons send their label text,
    which build_application() routes to the right screen."""
    rows = [
        [KeyboardButton(BTN_BROWSE)],
        [KeyboardButton(BTN_MYORDERS), KeyboardButton(BTN_HELP)],
    ]
    if config.is_auto_mode() and config.wallet_topup_enabled():
        rows.append([KeyboardButton(BTN_TOPUP)])
    if is_admin:
        rows.append([KeyboardButton(BTN_ADD)])
        rows.append([KeyboardButton(BTN_PRODUCTS), KeyboardButton(BTN_ORDERS)])
        rows.append([KeyboardButton(BTN_USERS), KeyboardButton(BTN_BROADCAST)])
        rows.append([KeyboardButton(BTN_EARNINGS)])
    rows.append([KeyboardButton(BTN_SUPPORT)])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Choose a quick menu below",
    )


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("⬅️ Menu", "menu", callback_data="menu:home")]]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    """A single '✖️ Cancel' button for any in-progress conversation prompt.

    Reuses the menu:home callback_data — app/main.py's common_fallbacks already
    matches that pattern for every ConversationHandler, so this needs zero new
    wiring and works everywhere /cancel would have.
    """
    return InlineKeyboardMarkup([[_btn("✖️ Cancel", "cancel_x", callback_data="menu:home")]])


def wizard_nav_kb(show_back: bool = True) -> InlineKeyboardMarkup:
    """Back (optional) + Cancel row for a multi-step wizard prompt."""
    row = []
    if show_back:
        row.append(_btn("⬅️ Back", "back", callback_data="apb"))
    row.append(_btn("✖️ Cancel", "cancel_x", callback_data="menu:home"))
    return InlineKeyboardMarkup([row])


# --- Catalog button styling --------------------------------------------- #
LOW_STOCK_THRESHOLD = 3   # at/below this (and still in stock) -> "Only N left"
CATALOG_NAME_MAX = 40     # trim long names so the stock badge stays visible
# 40 + icon (~3) + longest suffix " · deactivated" (15) = ~58, under Telegram's
# 64-char button-text limit.
CATALOG_PRICED_NAME_MAX = 22  # shorter budget for the buyer catalog list, which
# also packs a "| 📦 stock | price" tail onto the same 64-char button limit.


def _is_out_of_stock(stock: int) -> bool:
    return stock != db.UNLIMITED_STOCK and stock <= 0


def _stock_suffix(stock: int) -> str:
    """Plain-text availability suffix for a catalog button (no icon)."""
    if stock == db.UNLIMITED_STOCK:
        return ""
    if stock <= 0:
        return " · Sold out"
    if stock <= LOW_STOCK_THRESHOLD:
        return f" · Only {stock} left"
    return f" · {stock} in stock"


def _stock_badge(stock: int) -> str:
    """Compact '📦 N' stock badge for the buyer catalog list's pipe-separated
    label (see _catalog_label) — same thresholds as _stock_suffix, just
    shorter so it fits alongside the price on the same button."""
    if stock == db.UNLIMITED_STOCK:
        return "📦 ∞"
    if stock <= 0:
        return "📦 Sold out"
    if stock <= LOW_STOCK_THRESHOLD:
        return f"📦 {stock} left"
    return f"📦 {stock}"


def _catalog_label(p, stock: int) -> str:
    """Product button label: its own icon (icon_char, or the global default) —
    or ❌ when out of stock — + name + stock badge + price, pipe-separated so
    a buyer can compare products without opening each one."""
    if _is_out_of_stock(stock):
        icon_char = "❌"
    else:
        icon_char = (p["icon_char"] if "icon_char" in p.keys() else None) or "🏷"
    name = truncate_safe((p["name"] or "").strip(), CATALOG_PRICED_NAME_MAX)
    usdt_price, _ = db.effective_price(p)
    return f"{icon_char} {name} | {usdt(usdt_price)} | {_stock_badge(stock)}"


def _admin_row_label(p, stock: int) -> tuple[str, bool]:
    """Product row label + out-of-stock flag for the admin Products/Stock
    lists: icon (❌ when out of stock, else the product's own/default icon)
    + name + status suffix (deactivated / ∞ / stock count)."""
    out_of_stock = p["active"] and _is_out_of_stock(stock)
    icon_char = "❌" if out_of_stock else (p["icon_char"] if "icon_char" in p.keys() else None) or "🏷"
    name = truncate_safe((p["name"] or "").strip(), CATALOG_NAME_MAX)
    if not p["active"]:
        suffix = " · deactivated"
    elif stock == db.UNLIMITED_STOCK:
        suffix = " · ∞"
    else:
        suffix = _stock_suffix(stock)
    return f"{icon_char} {name}{suffix}", out_of_stock


def _product_icon_btn(label: str, p, callback_data: str, out_of_stock: bool = False) -> InlineKeyboardButton:
    """Like _btn, but the icon is per-product: p['icon_emoji_id'] (a Premium
    custom/animated emoji the admin sent while editing) takes priority, else
    the global 'product' key from CUSTOM_EMOJI_IDS. When `out_of_stock` is
    True, the product's own icon is swapped for the 'out_of_stock' key instead
    (falls back to no icon, leaving the ❌ already in `label`'s text). Either
    way the leading plain-emoji character already in `label` is stripped when
    a custom-emoji icon is actually used, so it isn't shown twice."""
    if out_of_stock:
        icon = config.CUSTOM_EMOJI_IDS.get("out_of_stock")
    else:
        icon = (p["icon_emoji_id"] if "icon_emoji_id" in p.keys() else None) or config.CUSTOM_EMOJI_IDS.get("product")
    if icon:
        label = re.sub(r"^\S+\s+", "", label, count=1)
    return InlineKeyboardButton(label, icon_custom_emoji_id=icon, callback_data=callback_data)


def buy_now_btn(p, callback_data: str = None) -> InlineKeyboardButton:
    """'Buy now' button showing the product's own icon (see _product_icon_btn)
    + its name, instead of a generic label."""
    name = truncate_safe((p["name"] or "").strip(), CATALOG_NAME_MAX)
    return _product_icon_btn(f"🛒 Buy {name}", p, callback_data or f"buy:{p['id']}")


def _name_sort_key(p) -> str:
    """Case-insensitive product name — used to sort lists A→Z."""
    return (p["name"] or "").strip().lower()


async def catalog_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    # Show sold-out products too (marked); only admin-deactivated ones are hidden.
    all_products = await db.list_products(only_active=True, include_out_of_stock=True)
    # Keyed ("automatic") products' real availability is their unused-key count,
    # not the manually-typed stock field — fetched once for the whole page
    # instead of once per product (see app.db.products.effective_stock).
    key_counts = await db.get_key_pool_counts()
    stocks = {p["id"]: db.effective_stock(p["stock"], key_counts.get(p["id"])) for p in all_products}
    # Buyable items first, then A→Z by name within each group (sold-out sinks to
    # the bottom but is still alphabetical down there).
    all_products = sorted(
        all_products,
        key=lambda p: (
            0 if (stocks[p["id"]] == db.UNLIMITED_STOCK or stocks[p["id"]] > 0) else 1,
            _name_sort_key(p),
        ),
    )
    page_size = 15
    total = len(all_products)
    start = page * page_size
    products = all_products[start:start + page_size]

    rows = []
    for p in products:
        stock = stocks[p["id"]]
        label = _catalog_label(p, stock)
        rows.append(
            [_product_icon_btn(label, p, f"view:{p['id']}", _is_out_of_stock(stock))]
        )

    # Pagination row
    nav = []
    if page > 0:
        nav.append(_btn("⬅️ Prev", "prev", callback_data=f"catalog:{page - 1}"))
    if start + page_size < total:
        nav.append(_btn("Next ➡️", "next", callback_data=f"catalog:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append(
        [
            _btn("🔄 Refresh", "refresh", callback_data=f"catalog:{page}"),
            _btn("⬅️ Menu", "menu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def product_detail_keyboard(p, in_stock: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if in_stock:
        rows.append([buy_now_btn(p)])
    else:
        rows.append([_btn("🔴 Out of stock", "out_of_stock", callback_data=f"view:{p['id']}")])
    rows.append([_btn("⬅️ Back to catalog", "back", callback_data="catalog")])
    return InlineKeyboardMarkup(rows)


def review_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _btn("✅ Approve", "approve", callback_data=f"approve:{order_id}"),
                _btn("❌ Reject", "reject", callback_data=f"reject:{order_id}"),
            ]
        ]
    )


# Preset rejection reasons shown when an admin taps Reject.
REJECT_PRESETS = {
    "stock": "Out of stock — your payment will be fully refunded shortly.",
    "nopay": "Payment could not be verified / was not received.",
}


def reason_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_btn("📦 Out of stock", "out_of_stock", callback_data="rjr:stock")],
            [_btn("💸 Payment not received", "nopay", callback_data="rjr:nopay")],
            [_btn("✍️ Other (type it)", "other", callback_data="rjr:other")],
            [_btn("✖️ Cancel", "cancel", callback_data="rjr:cancel")],
        ]
    )


def manage_keyboard(p) -> InlineKeyboardMarkup:
    toggle_text = "❌ Deactivate" if p["active"] else "✅ Activate"
    toggle_key = "deactivate" if p["active"] else "activate"
    pid = p["id"]
    _, is_discounted = db.effective_price(p)
    discount_btn = (
        _btn("❌ End discount", "cancel", callback_data=f"enddiscount:{pid}") if is_discounted
        else _btn("🏷️ Discount", "sale", callback_data=f"discount:{pid}")
    )
    rows = [
        [_btn("💰 Price (USDT)", "price", callback_data=f"edit:price:{pid}")],
        [
            _btn("📦 Stock", "stock", callback_data=f"edit:stock:{pid}"),
            _btn("🔤 Name", "name", callback_data=f"edit:name:{pid}"),
        ],
        [_btn("📝 Description", "description", callback_data=f"edit:description:{pid}")],
        [_btn("🏷 Icon", "icon", callback_data=f"edit:icon:{pid}")],
        [_btn("🔑 Delivery codes", "keys", callback_data=f"keys:{pid}")],
        [discount_btn],
        [_btn(toggle_text, toggle_key, callback_data=f"toggle:{pid}")],
        [_btn("🗑 Delete", "delete", callback_data=f"del:{pid}")],
        [_btn("⬅️ Products", "products", callback_data="menu:products")],
    ]
    return InlineKeyboardMarkup(rows)
