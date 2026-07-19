"""Text/number formatting helpers shared by every handler module."""
import html
from datetime import timezone, timedelta
from decimal import Decimal

from telegram import InlineKeyboardMarkup

from app import config

# Order / user timestamps are stored in UTC and shown to admins in IST.
IST = timezone(timedelta(hours=5, minutes=30))

# Invisible spacer made of Braille-blank characters (U+2800). Telegram has no
# "button width" setting, but a single-button row stretches to the widest line in
# the message — so appending this wide, invisible line gives a button menu a
# consistent MINIMUM width. Longer button labels stretch it further on their own.
# Tune the multiplier to make the menus wider/narrower.
WIDTH_PAD = "⠀" * 60


def _fmt(amount: float) -> str:
    """Format a number, dropping unnecessary decimal zeros (8.00→8, 8.50→8.5)."""
    s = f"{amount:,.2f}"
    return s.rstrip("0").rstrip(".")


def money(amount: float) -> str:
    return "" if amount == 0 else f"₹{_fmt(amount)}"


def usdt(amount: float) -> str:
    return "Free" if amount == 0 else f"{_fmt(amount)} USDT"


def price_both(usdt_amount: float, inr_amount: float = 0.0) -> str:
    """Render a product's price in both currencies, e.g. '9.99 USDT (₹920.00)'.

    Both prices are set manually per product; the INR part is shown only when an
    INR price has actually been set (> 0).
    """
    if usdt_amount == 0 and (not inr_amount or inr_amount == 0):
        return "Free"
    if inr_amount and inr_amount > 0:
        return f"{usdt(usdt_amount)} / {money(inr_amount)}"
    return usdt(usdt_amount)


def order_amount_str(order) -> str:
    amt_usdt = order["amount_usdt"] if "amount_usdt" in order.keys() else 0.0
    inr = money(order["amount"])
    usdt_str = usdt(amt_usdt) if amt_usdt > 0 else ""
    if inr and usdt_str:
        return f"{usdt_str} / {inr}"
    return inr or usdt_str or "Free"


def qty_suffix(order) -> str:
    """' ×3' for a multi-unit order, '' for the common qty=1 case (old rows
    with no qty column read back the default of 1 the same way)."""
    qty = order["qty"] if "qty" in order.keys() else 1
    return f" ×{qty}" if qty > 1 else ""


def esc(text: object) -> str:
    return html.escape(str(text))


def render_name(p) -> str:
    """A product's name for HTML message text, preserving any custom/Premium
    emoji the admin typed (see name_html — captured via Message.text_html at
    add/edit time). Falls back to the plain escaped name for older rows or
    names with no custom emoji. Button labels can't use this — Telegram only
    supports one fixed icon per button, never emoji from arbitrary text."""
    html_name = p["name_html"] if "name_html" in p.keys() else None
    return html_name if html_name else esc(p["name"])


def product_icon(p) -> str:
    """The product's icon for message text: its own emoji if the admin set one
    (icon_char, +icon_emoji_id for a Premium/animated one), else the global
    "product" default from CUSTOM_EMOJI_IDS."""
    char = p["icon_char"] if "icon_char" in p.keys() else None
    if not char:
        return cemoji("product", "🏷")
    eid = p["icon_emoji_id"] if "icon_emoji_id" in p.keys() else None
    return f'<tg-emoji emoji-id="{eid}">{char}</tg-emoji>' if eid else char


def render_name_with_icon(p) -> str:
    """render_name, prefixed with the product's icon (see product_icon)."""
    return f"{product_icon(p)} {render_name(p)}"


def render_desc(p) -> str:
    """Same as render_name, for a product's description."""
    html_desc = p["description_html"] if "description_html" in p.keys() else None
    return html_desc if html_desc else esc(p["description"])


# Codepoints that only make sense attached to a preceding character. Plain
# str[:n] slicing counts codepoints, not rendered glyphs, so a cut landing here
# leaves a broken/orphaned emoji fragment (e.g. a lone flag-letter box) instead
# of nothing — this is what admins hit when a product name with an emoji got
# mangled by truncation in a button label.
_TRAILING_INVALID = "‍︎️⃣"  # ZWJ, variation selectors, keycap combiner
_SKIN_TONES = "\U0001F3FB\U0001F3FC\U0001F3FD\U0001F3FE\U0001F3FF"


def truncate_safe(name: str, max_len: int) -> str:
    """Truncate to at most `max_len` chars + an ellipsis without splitting a
    multi-codepoint emoji (flags, ZWJ sequences, skin-tone modifiers, keycaps).
    ponytail: covers the common cases (one ZWJ join, one flag pair, one skin
    tone); an emoji made of a longer modifier chain could still lose its tail —
    reach for a real grapheme-cluster lib (e.g. `regex` module's \\X) if that
    ever shows up in practice."""
    if len(name) <= max_len:
        return name
    cut = name[:max_len - 1].rstrip()
    while cut and (cut[-1] in _TRAILING_INVALID or cut[-1] in _SKIN_TONES):
        cut = cut[:-1].rstrip()
    tail_regional = 0
    for ch in reversed(cut):
        if "\U0001F1E6" <= ch <= "\U0001F1FF":
            tail_regional += 1
        else:
            break
    if tail_regional % 2:
        cut = cut[:-1].rstrip()
    return f"{cut}…" if cut else name[:max_len]


def _order_no(order) -> str:
    """Public order number: the unique ref (e.g. #260618-K7P2), or #id for
    older orders created before refs existed."""
    ref = order["ref"] if "ref" in order.keys() else ""
    return f"#{ref}" if ref else f"#{order['id']}"


def _pad(text: str, reply_markup=None) -> str:
    """Append the invisible width spacer when a message carries an INLINE keyboard,
    so every inline button menu shares the same comfortable minimum width."""
    if isinstance(reply_markup, InlineKeyboardMarkup):
        return f"{text}\n{WIDTH_PAD}"
    return text


def _format_usdt(amount_micro: int) -> str:
    """Format an integer micro-USDT amount (see app.db.wallet) as a plain number."""
    return str((Decimal(amount_micro) / Decimal(1_000_000)).normalize())


def cemoji(key: str, fallback: str) -> str:
    """A premium/animated custom emoji inline in HTML message text (requires the
    bot owner's Telegram Premium, same as keyboards._btn() — see CUSTOM_EMOJI_IDS
    in .env). Degrades to the plain `fallback` emoji when `key` isn't configured,
    so callers never need a separate code path for either case."""
    eid = config.CUSTOM_EMOJI_IDS.get(key)
    if not eid:
        return fallback
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def _demo() -> None:
    """Self-check for truncate_safe: run `python -m app.formatting`."""
    assert truncate_safe("short", 20) == "short"
    assert truncate_safe("Super Deal Bundle \U0001F1EE\U0001F1F3", 19) == "Super Deal Bundle…"
    assert truncate_safe("Family Pack \U0001F468‍\U0001F469‍\U0001F467‍\U0001F466 Special", 15) == "Family Pack 👨…"
    assert truncate_safe("Thumbs up \U0001F44D\U0001F3FD combo", 12) == "Thumbs up 👍…"
    # cut lands exactly on the skin-tone modifier itself -> must also strip it
    assert truncate_safe("Thumbs up \U0001F44D\U0001F3FD combo", 13) == "Thumbs up 👍…"
    assert truncate_safe("Countdown 1️⃣ deal", 12) == "Countdown 1…"
    print("formatting._demo: all truncate_safe checks passed")


if __name__ == "__main__":
    _demo()
