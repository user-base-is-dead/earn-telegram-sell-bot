# Telegram Seller Bot 🛒

A Telegram bot that shows a product catalog, generates a **UPI payment QR code**
for each order, takes manual payment confirmation, and **auto-delivers a digital
product** once an admin approves the payment.

> Verification is **manual** by design: the buyer pays via UPI and submits the
> UTR / a screenshot, then you (admin) approve from inside Telegram. This works
> with any personal or business UPI ID — no payment gateway or KYC needed.

---

## Features

- 📚 Product catalog with inline buttons
- 💱 **Dual USDT + INR pricing** — set each product's price in **both** USDT and
  INR manually; buyers see both currencies (no live/auto conversion)
- 💳 Multiple payment methods — **UPI** (auto QR + copyable UPI ID) and/or
  **Binance** (Pay ID and/or crypto address with QR); buyer picks at checkout
- ✅ "I've Paid" flow — buyer sends UTR / Binance TxID **or** a screenshot
- 🔔 Admins get an approve / reject notification (with the method) for every payment
- 📦 Digital product (key / link / code / text) delivered automatically on approval
- 🗂 Stock tracking (limited or unlimited) and order history
- ➕ Add products from chat with the guided `/addproduct` wizard
- 📣 **Approved broadcasts** — adding a product, restocking, a price change, or a
  sell-out prompts an **Approve / Deny** card to announce it to everyone who has used the bot
- 💾 Zero-config storage (SQLite), secrets via `.env`

---

## Project layout

```
telegram-seller-bot/
├── bot.py            # all handlers + bot bootstrap
├── config.py         # loads & validates .env
├── database.py       # SQLite layer (products, orders) with indexes
├── qr_utils.py       # UPI link + QR PNG generation
├── view_db.py        # human-readable view / export of the database
├── requirements.txt
├── .env.example      # copy to .env and fill in
└── data/             # SQLite db (created at runtime, git-ignored)
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

| Variable          | Description                                              |
|-------------------|----------------------------------------------------------|
| `BOT_TOKEN`       | Token from BotFather                                     |
| `ADMIN_IDS`       | Your numeric ID(s), comma-separated                      |
| `UPI_ID`          | UPI VPA that receives money, e.g. `yourname@okhdfcbank`  |
| `UPI_PAYEE_NAME`  | Store / payee name shown in the UPI app                  |
| `BINANCE_PAY_ID`  | *(optional)* Binance Pay ID to receive on Binance        |
| `BINANCE_ADDRESS` | *(optional)* Crypto deposit address (a QR is generated)  |
| `BINANCE_NETWORK` | Coin/network label shown to buyer (default `USDT (TRC20)`)|
| `DB_PATH`         | SQLite file path (default `data/store.db`)               |

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
   name → description → **USDT price** → **INR price** → stock. You set both
   prices manually per product; buyers see both currencies. The "content"
   is exactly what the buyer receives on approval (a license key, a download
   link, an access code, etc.). Made a typo? Tap **⬅️ Back** (or send
   `/back`) to fix the previous step, or `/cancel` to abort.
2. **📦 Products** (or `/products`) — list everything with stock and status.
3. **🧾 Orders** (or `/orders`) — see the latest orders and their status.
4. When a buyer pays, you get a message with **✅ Approve / ❌ Reject** buttons.
   - **Approve** → the product content is sent to the buyer automatically and
     stock is decremented.
   - **Reject** → the buyer is notified the payment couldn't be verified.
5. **📣 Announcements** — adding a product, increasing its stock, changing its
   price (up or down), or a product selling out pops up an **✅ Approve & send /
   ❌ Deny** card. Approve to broadcast a short message to everyone who has used
   the bot; Deny to skip it.

### As a customer
1. `/start` → **🛍 Browse products** → tap a product → **Buy now**.
2. Pick a payment method (UPI and/or Binance, whichever you enabled):
   - **UPI:** scan the QR in any UPI app (GPay / PhonePe / Paytm) and pay the
     exact amount. Paying on the same phone? Screenshot the QR and scan it from
     your gallery, or copy the shown UPI ID.
   - **Binance:** send to the shown Pay ID / address (scan the address QR).
3. Tap **✅ I've Paid** and send the **UTR** / **Binance TxID** or a **screenshot**.
4. After the seller approves, the product is delivered right in the chat.

---

## Order lifecycle

```
created  ──(buyer submits UTR/screenshot)──►  pending_review
pending_review  ──(admin approves)──►  approved   (product delivered)
pending_review  ──(admin rejects)──►   rejected
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

- **Manual verification:** the bot does not auto-read your bank. Always confirm
  the money actually arrived (check the UTR in your UPI/bank app) before
  approving. The buyer-supplied UTR/screenshot is a claim, not proof.
- **Secrets:** `.env` and the `data/` database are git-ignored. Never commit them.
- **Admins only:** `/addproduct`, `/products`, `/orders`, and approve/reject are
  restricted to the IDs in `ADMIN_IDS`.
- **Transport:** the bot uses Telegram long-polling, so it opens **no inbound
  network port** on your machine.

---

## Possible extensions

- Auto payment verification via a gateway (Razorpay / Cashfree / PhonePe).
- File/document delivery (send actual files instead of text content).
- Edit / delete products, deactivate from chat.
- Webhook deployment instead of polling for production hosting.
