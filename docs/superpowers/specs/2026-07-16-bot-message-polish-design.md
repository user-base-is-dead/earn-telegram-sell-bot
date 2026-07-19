# Bot message polish: premium, consistent copy across join-gate/payment/top-up screens

Date: 2026-07-16

## Goal

Several buyer-facing Telegram messages look inconsistent with the rest of the bot and were
flagged directly from screenshots: the force-join screen, the balance/top-up flow, and the
payment-instruction screens. The welcome screen (`show_main_menu`) and `/help` already use a
consistent premium style (blockquote hierarchy, bold labels, the store's already-configured
premium custom emoji via `cemoji()`); this brings the flagged screens up to that same bar rather
than inventing a new style. No behavior changes — text/button-styling only.

## Style rules (apply everywhere touched)

- **Brand line**: `{cemoji('star', '🌟')} <b>{store name}</b>` as its own line at the top of every
  entry-point screen (join-gate, balance card, top-up amount prompt, both payment-instruction
  screens, all three buy-flow payment screens). Not repeated on intermediate messages within the
  same conversation turn (e.g. "Choose how you'll pay:" right after the amount prompt) — one
  brand line per screen a user actually lands on, not per message.
- **Header line**: one emoji + bold title (e.g. `🧾 Order #A1B2C3`, `💠 Top-up request`) — never
  more than one leading emoji per line.
- **Bold labels, no per-line emoji**: `<b>Amount:</b>`, `<b>Pay ID:</b>`, `<b>Address:</b>`,
  `<b>UPI ID:</b>` — drop the current per-line emoji prefixes (💰🏦⛓💠) on these detail lines; the
  header emoji already establishes context. This directly addresses "the emojis... not
  optimised" — fewer, more purposeful emoji rather than one per line.
- **Blockquote for the reassurance/CTA note**: the "auto-credited, no need to message us" /
  "pay the exact amount, don't contact anyone" line gets wrapped in `<blockquote>`, matching how
  the welcome screen already sets off its feature list.
- **Buttons through `_btn()`**: every button on a touched screen goes through the existing
  `app.keyboards._btn()` helper (premium custom emoji support) instead of raw
  `InlineKeyboardButton`, using the matching key from `config.CUSTOM_EMOJI_IDS` (already
  populated — no new emoji-ID lookups needed). This is the actual root cause of the force-join
  screen looking dated: it's the one screen in the bot that never went through `_btn()`.

## Screens touched (exact copy)

### 1. `app/middleware/membership.py::send_force_join_message`

```python
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
```

Needs new imports: `esc` from `app.formatting`, `_btn` from `app.keyboards`.

### 2. `app/handlers/topup.py::show_balance`

```python
text = (
    f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
    f"{cemoji('money', '💰')} <b>Your wallet</b>\n"
    f"<blockquote>Balance: <b>{esc(balance)} USDT</b></blockquote>"
)
if config.wallet_topup_enabled():
    text += "\n<i>Top up once, then buy instantly with zero waiting.</i>"
    kb = InlineKeyboardMarkup([
        [_btn("➕ Top up", "topup", callback_data="topup")],
        [_btn("⬅️ Menu", "menu", callback_data="menu:home")],
    ])
else:
    text += "\n\n<i>Top-ups aren't enabled yet — contact support to add funds.</i>"
    kb = back_to_menu_kb()
```

### 3. `app/handlers/topup.py::topup_start` (amount prompt)

```python
await _send(
    update,
    f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
    f"{cemoji('money', '💰')} <b>Wallet top-up</b>\n"
    f"<blockquote>Balance: <b>{esc(balance)} USDT</b></blockquote>\n"
    "How much USDT would you like to add? Send a number, e.g. <code>10</code>.\n"
    "Send /cancel to abort.",
)
```

### 4. `app/handlers/topup.py::topup_amount` (rail-choice buttons only — "Choose how you'll pay:"
text is unchanged, no brand line here since it's the very next message after #3)

```python
_RAIL_EMOJI_KEY = {db.RAIL_BSC: "chain", db.RAIL_BINANCE_PAY: "binance"}
...
kb = InlineKeyboardMarkup(
    [[_btn(label, _RAIL_EMOJI_KEY.get(rail), callback_data=f"topup:rail:{rail}")] for rail, label in choices]
)
```

### 5. `app/handlers/topup.py::_create_topup` (both branches)

```python
brand = f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
kb = InlineKeyboardMarkup([[_btn("🔍 Check my payment", "search", callback_data="topup:check")]])
if rail == db.RAIL_BSC:
    text = (
        brand +
        f"{cemoji('chain', '⛓')} <b>Top-up request</b> ({esc(config.CRYPTO_NETWORK)})\n\n"
        f"<b>Amount:</b> <code>{esc(amount_str)} USDT</code> <i>(send this exact figure)</i>\n"
        f"<b>Address:</b> <code>{esc(config.CRYPTO_ADDRESS)}</code>\n"
        f"<blockquote>{cemoji('bolt', '⚡')} Auto-credited in ~1 min, no need to message us · "
        f"Expires in <b>{config.DEPOSIT_EXPIRY_MINUTES} min</b></blockquote>"
    )
else:
    text = (
        brand +
        f"{cemoji('binance', '💠')} <b>Top-up request</b>\n\n"
        f"<b>Amount:</b> <code>{esc(amount_str)} USDT</code> <i>(send this exact figure)</i>\n"
        f"<b>Pay ID:</b> <code>{esc(config.BINANCE_PAY_ID)}</code>\n"
        f"<blockquote>{cemoji('bolt', '⚡')} Auto-credited in ~1 min, no need to message us · "
        f"Expires in <b>{config.DEPOSIT_EXPIRY_MINUTES} min</b></blockquote>"
    )
```

Note: the BSC branch sends this as a photo caption (QR code), not a plain message — Telegram
photo captions support the same HTML subset including `<blockquote>` on this bot's PTB version,
but this is the one spot in the redesign that should be visually double-checked once built (long
captions or unusual entity nesting occasionally render differently in captions vs. messages).

### 6. `app/handlers/payments.py` — `_show_upi_payment`, `_pay_text_header`, `_PAID_FOOTER`,
`_show_binance_pay_payment`, `_show_blockchain_payment`

```python
# _show_upi_payment
text = (
    f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>\n\n"
    f"{cemoji('receipt', '🧾')} <b>Order {esc(_order_no(order))}</b>\n\n"
    f"<b>Amount:</b> <code>₹{_fmt(order['amount'])}</code>\n"
    f"<b>UPI ID:</b> <code>{esc(config.UPI_ID)}</code>\n"
    f"<blockquote>Pay the exact amount, then tap <b>{cemoji('check', '✅')} I've Paid</b> below.</blockquote>"
)

# _pay_text_header
def _pay_text_header(order) -> list[str]:
    amt_usdt = order["amount_usdt"] if "amount_usdt" in order.keys() else 0.0
    lines = [
        f"{cemoji('star', '🌟')} <b>{esc(config.UPI_PAYEE_NAME)}</b>",
        "",
        f"{cemoji('receipt', '🧾')} <b>Order {esc(_order_no(order))}</b>",
        "",
    ]
    if amt_usdt > 0:
        total = amt_usdt + config.CRYPTO_FEE_USDT
        line = f"<b>Amount:</b> <code>{_fmt(total)} USDT</code>"
        if config.CRYPTO_FEE_USDT > 0:
            line += " <i>(incl. charges)</i>"
        lines.append(line)
    else:
        lines.append(f"<b>Amount:</b> <b>{esc(money(order['amount']))}</b>")
    return lines

# _PAID_FOOTER
_PAID_FOOTER = (
    f"\n<blockquote>{cemoji('warn', '⚠️')} <b>Send this exact amount</b> — a wrong amount will not be refunded.\n"
    f"After paying, tap <b>{cemoji('check', '✅')} I've Paid</b> and send your TxID or a screenshot.</blockquote>"
)

# _show_binance_pay_payment (crypto is the same shape, different label/value)
lines = _pay_text_header(order) + [
    "",
    f"{cemoji('binance', '💠')} <b>Binance Pay</b>",
    f"<b>Pay ID:</b> <code>{esc(config.BINANCE_PAY_ID)}</code>",
]
text = "\n".join(lines) + _PAID_FOOTER
```

### 7. `app/handlers/payments.py::_payment_buttons`

```python
async def _payment_buttons(order_id: int) -> InlineKeyboardMarkup:
    order = await db.get_order(order_id)
    back_cb = f"view:{order['product_id']}" if order else "catalog"
    return InlineKeyboardMarkup(
        [
            [_btn("🏠 Start", "menu", callback_data="menu:home")],
            [_btn("✅ I've Paid", "check", callback_data=f"paid:{order_id}")],
            [
                _btn("🛍 Catalog", "browse", callback_data="catalog"),
                _btn("⬅️ Back", "back", callback_data=back_cb),
            ],
        ]
    )
```

Needs `_btn` added to the existing `from app.keyboards import ...` line in `payments.py`.

## Out of scope

- The main welcome screen (`show_main_menu`) and `/help` — already at this style bar.
- Admin-only screens (order review, product management, broadcast, earnings) — not part of the
  buyer-facing complaint this addresses.
- `topup_check_prompt`/`topup_check_submit` (the "check my payment" self-serve flow) and order
  status/history screens — not flagged, left as-is.
- No new `CUSTOM_EMOJI_IDS` keys — every emoji key used above (`star`, `announce`, `check`,
  `money`, `topup`, `chain`, `binance`, `bolt`, `receipt`, `warn`, `search`, `menu`, `browse`,
  `back`) already exists in the configured set.
- No schema/behavior changes anywhere — purely message text and button-icon styling.
