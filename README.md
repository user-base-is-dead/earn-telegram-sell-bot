# Telegram Seller Bot 🛒

A Telegram bot that shows a product catalog, takes **crypto / Binance Pay** payments
(or instant purchases from an internal **USDT wallet**), and **auto-delivers a digital
product** on approval / on payment.

> Two ways to sell, toggled live with `/mode`:
> - **Manual** — the buyer pays via **Binance Pay** or a **crypto address** and submits
>   the TxID / a screenshot; you (admin) approve from inside Telegram.
> - **Auto (wallet)** — the buyer tops up an internal USDT balance (auto-credited from
>   an on-chain USDT-BEP20 transfer and/or Binance Pay), then buys instantly with a
>   delivery code sent automatically — zero admin involvement.

No payment gateway or KYC needed — you receive funds straight to your own Binance Pay
ID / crypto address.

---

## Features

- 📚 Product catalog with inline buttons
- 💵 **USDT pricing** — set each product's price in USDT (no live/auto conversion)
- 💳 Payment rails — **Binance Pay** (Pay ID) and/or **Crypto** (address with QR); buyer
  picks at checkout, or pays instantly from their **wallet balance** in auto mode
- ⚡ **Auto-confirmed wallet top-ups** — USDT-BEP20 (watched on-chain via public RPCs)
  and best-effort Binance Pay, matched to a uniquely-tagged amount
- ✅ "I've Paid" flow — buyer sends the **Binance TxID** / transaction hash **or** a screenshot
- 🔔 Admins get an approve / reject notification (with the method) for every manual payment
- 📦 Digital product (key / link / code / text) delivered automatically on approval, or
  instantly from the product's delivery-code pool for wallet purchases
- 🗂 Stock tracking (limited or unlimited) and order history
- ➕ Add products from chat with the guided `/addproduct` wizard
- 📣 **Approved broadcasts** — adding a product, restocking, a price change, or a
  sell-out prompts an **Approve / Deny** card to announce it to everyone who has used the bot
- 💾 Zero-config storage (SQLite), secrets via `.env`

---

## Project layout

The implementation lives in the `app/` package; `bot.py` at the repo root is a two-line
entrypoint into `app.main.main()`.

```
telegram-seller-bot/
├── bot.py                # entrypoint: `from app.main import main`
├── app/
│   ├── main.py           # wires every handler + background job, boots the bot
│   ├── config.py         # loads & validates .env
│   ├── formatting.py     # money/text formatting helpers
│   ├── keyboards.py      # every inline/reply keyboard builder
│   ├── states.py         # conversation state constants
│   ├── db/               # SQLite layer (products, orders, users, wallet) + schema
│   ├── handlers/         # one module per feature (catalog, payments, topup, …)
│   ├── services/         # qr.py (crypto QR) + crypto_watch.py (top-up watchers)
│   └── middleware/       # force-join membership gate
├── view_db.py            # human-readable view / export of the database
├── requirements.txt
├── .env.example          # copy to .env and fill in
└── data/                 # SQLite db (created at runtime, git-ignored)
```

---

## Setup

### 1. Get a bot token
Open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.

### 2. Get your admin user ID
Message [@userinfobot](https://t.me/userinfobot) → it replies with your numeric ID.

### 3. Install

```powershell
# from the project folder
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

(On macOS / Linux use `source .venv/bin/activate`.)

### 4. Configure

```powershell
copy .env.example .env   # macOS/Linux: cp .env.example .env
```

Edit `.env`:

| Variable          | Description                                                          |
|-------------------|----------------------------------------------------------------------|
| `BOT_TOKEN`       | Token from BotFather                                                 |
| `ADMIN_IDS`       | Your numeric ID(s), comma-separated                                  |
| `STORE_NAME`      | Store / brand name shown to buyers throughout the bot                |
| `BINANCE_PAY_ID`  | *(optional)* Binance Pay ID to receive on Binance                    |
| `CRYPTO_ADDRESS`  | *(optional)* Crypto deposit address (a QR is generated)              |
| `CRYPTO_NETWORK`  | Coin/network label shown to buyer (default `USDT (BEP20)`)           |
| `CRYPTO_FEE_USDT` | Flat USDT fee added on Binance Pay / crypto to cover network charges |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | *(optional, read-only)* auto-detect incoming Binance Pay top-ups |
| `DB_PATH`         | SQLite file path (default `data/store.db`)                           |

At least one payment rail (`BINANCE_PAY_ID` or `CRYPTO_ADDRESS`) must be set, or the bot
refuses to start.

### 5. Run

```powershell
python bot.py
```

You should see `Bot starting (polling)...`. Open your bot in Telegram and send `/start`.

---

## How to use

Send `/start` (or `/menu`) to open the **button menu**. It is role-aware:
customers see *Browse products* and *Help*; admins additionally see
*Add product*, *Products*, and *Orders*. Every screen has a **⬅️ Menu** button,
and the old slash-commands still work too.

### As the seller (admin)
1. **➕ Add product** (or `/addproduct`) — the bot walks you through
   name → description → **USDT price** → stock → icon. The "content" is exactly
   what the buyer receives on approval (a license key, a download link, an access
   code, etc.). Made a typo? Tap **⬅️ Back** (or send `/back`) to fix the previous
   step, or `/cancel` to abort.
2. **📦 Products** (or `/products`) — list everything with stock and status.
3. **🧾 Orders** (or `/orders`) — see the latest orders and their status.
4. `/mode` — toggle **auto** (wallet-only, instant delivery) vs **manual**
   (Binance Pay / crypto, you review + deliver each order).
5. When a buyer pays manually, you get a message with **✅ Approve / ❌ Reject** buttons.
   - **Approve** → the product content is sent to the buyer automatically and
     stock is decremented.
   - **Reject** → the buyer is notified the payment couldn't be verified.
6. **📣 Announcements** — adding a product, increasing its stock, changing its
   price (up or down), or a product selling out pops up an **✅ Approve & send /
   ❌ Deny** card. Approve to broadcast a short message to everyone who has used
   the bot; Deny to skip it.

### As a customer
1. `/start` → **🛍 Browse products** → tap a product → **Buy now**.
2. **Manual mode:** pick a payment method (Binance Pay and/or Crypto, whichever you
   enabled), send to the shown Pay ID / address (scan the address QR), then tap
   **✅ I've Paid** and send the **Binance TxID** / transaction hash or a **screenshot**.
   After the seller approves, the product is delivered right in the chat.
3. **Auto mode:** top up your wallet once (send the exact tagged USDT amount to the
   shown address / Pay ID — it's auto-credited in ~1 min), then buy any product and
   receive your delivery code **instantly**, no review.

---

## Order lifecycle

```
created  ──(buyer submits TxID/screenshot)──►  pending_review
pending_review  ──(admin approves)──►  approved   (product delivered)
pending_review  ──(admin rejects)──►   rejected

# wallet purchases skip the review step:
created  ──(wallet debited, code auto-delivered)──►  approved
```

---

## Database — fast *and* readable

The store uses **SQLite**, not a JSON file. This matters at scale:

- A **JSON** store must read & parse the whole file and scan from the top to
  find one record — that gets slow as orders pile up (`O(n)`).
- **SQLite** uses **B-tree indexes**, so a lookup jumps straight to the row
  (`~O(log n)`) and only the needed data is read. Indexes are created on
  `orders.status`, `orders.user_id`, and `products.active`, so common queries
  never do a full scan even with millions of rows.

Because a `.db` file is binary (not openable in a text editor), use the viewer
to read it in a human-friendly form:

```powershell
python view_db.py                       # print products & orders
python view_db.py --json                # also write data/export.json
python view_db.py --orders pending_review   # only orders with this status
```

Prefer a GUI? Open `data/store.db` in the free
[DB Browser for SQLite](https://sqlitebrowser.org/) to browse, search, and edit
visually — all backed by the same fast indexed queries.

---

## Security & notes

- **Manual verification:** the bot does not auto-read manual payments. Always confirm
  the money actually arrived (check the TxID on-chain / in Binance) before approving.
  The buyer-supplied TxID/screenshot is a claim, not proof. Wallet top-ups, by contrast,
  are confirmed automatically from the actual on-chain transfer.
- **Secrets:** `.env` and the `data/` database are git-ignored. Never commit them.
  Any `BINANCE_API_KEY` must be **read-only** (Enable Reading only — no trading or
  withdrawal permission).
- **Admins only:** `/addproduct`, `/products`, `/orders`, `/mode`, and approve/reject are
  restricted to the IDs in `ADMIN_IDS`.
- **Transport:** the bot uses Telegram long-polling, so it opens **no inbound
  network port** on your machine.

---

## Possible extensions

- File/document delivery (send actual files instead of text content).
- Additional auto-confirmed top-up networks.
- Webhook deployment instead of polling for production hosting.
