# Telegram login shop bot

A small Telegram bot that sells logins (or any text-based digital goods) for **USDT on BNB Smart
Chain (BEP20)** and delivers them automatically as soon as the payment confirms. It needs no
payment gateway and no manual approval, because the bot reads the blockchain itself.

## How a sale works

1. The buyer taps /start and picks a product and a quantity.
2. The bot reserves the logins and shows an amount that belongs to this order only, e.g.
   `5.2043 USDT` for a $5.00 item with a $0.20 fee. The buyer has 30 minutes to pay.
3. The buyer sends exactly that amount to your wallet.
4. Every 15 seconds the bot reads new USDT transfers into your wallet from a BSC node. The
   transfer pays the order whose amount it carries: the logins are sent to the buyer, and you
   get a sale notification.

## How payments are verified

- Only `Transfer` events of the real USDT contract (`0x55d398326f99059fF775485246999027B3197955`)
  into your wallet count. Fake tokens that call themselves "USDT" are ignored.
- Every order that can still be paid has its own amount (a random tail below one cent). A
  transfer with that exact amount pays that order. If no order matches exactly, a transfer
  within 3 cents of **exactly one** order (wallets that round, small fees) pays that order.
- The transfer must be sent after the order was created and no later than 2 hours after it
  expired, and it needs 5 confirmations.
- Each transfer (tx hash + log index) is stored and can pay only once.
- If an amount fits several orders or none, the bot doesn't guess. Nothing is delivered, and the
  admins get the amount, the sender and a BscScan link.
- The last block read is stored, so after downtime the bot catches up instead of missing
  payments. If the RPC endpoints keep failing, the admins are alerted.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then fill in BOT_TOKEN, ADMIN_IDS and WALLET_ADDRESS
.venv\Scripts\python bot.py
```

Keep it running (a VPS, or a service/`pm2`/`nohup` on a server). Payments that arrive while it
is offline are picked up when it starts again.

## Admin commands (in a private chat with the bot)

| Command | What it does |
| --- | --- |
| `/admin` | Products, stock, open orders, last 24h sales |
| `/add Netflix 1 Month \| 4.99` | Create a product |
| `/stock 1` + one login per line below it | Add logins to product 1 (or send a `.txt` file with the caption `/stock 1`) |
| `/price 1 5.99` | Change a price (open orders keep their amount) |
| `/clear 1` | Delete product 1's unsold logins |
| `/del 1` | Remove product 1 from the shop |

The same login is never added twice to a product. If a payment arrives after its order expired
and the product has sold out in the meantime, the order waits and is delivered automatically on
the next `/stock`.

## Files

- `bot.py`: Telegram handlers, messages, background job
- `payments.py`: BSC reading and payment matching
- `db.py`: SQLite storage (`data/shop.db`)
- `config.py`: `.env` settings
