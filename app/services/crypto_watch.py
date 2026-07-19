"""Auto-confirmed wallet top-ups: watch BSC (USDT-BEP20) and poll Binance Pay.

Both rails funnel into the same match/credit path: an incoming transfer's exact
amount (in integer micro-USDT) is matched against a pending `deposits` row
tagged with that amount (see app/db/wallet.py), credited to the buyer's
wallet, and recorded in `processed_tx` so a restart or re-scan can never
double-credit it.
"""
import hashlib
import hmac
import logging
import re
import time
from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

import httpx

from app import config, db
from app.formatting import cemoji

logger = logging.getLogger(__name__)

USDT_BEP20_CONTRACT = "0x55d398326f99059ff775485246999027b3197955"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_BSC_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _topic_address(addr: str) -> str:
    """Left-pad an address to a 32-byte log topic, as eth_getLogs expects."""
    return "0x" + addr.lower().removeprefix("0x").rjust(64, "0")


async def _credit_if_matched(rail: str, tx_ref: str, amount_micro: int) -> Optional[int]:
    """If `amount_micro` matches a pending deposit on `rail` and `tx_ref` hasn't
    already been credited, credit the wallet. Returns the credited user_id, or
    None if nothing matched / it was already processed.

    db.credit_deposit_once() does the match+credit+mark atomically, so the
    periodic poller and a buyer's own "check my payment" lookup racing on the
    same transfer can't both credit it."""
    return await db.credit_deposit_once(rail, tx_ref, amount_micro, reason=f"deposit_{rail}")


async def _notify_credited(context, user_id: int, amount_micro: int) -> None:
    amount = Decimal(amount_micro) / Decimal(1_000_000)
    try:
        await context.bot.send_message(
            user_id,
            f"{cemoji('check', '✅')} Wallet top-up received: <b>{amount.normalize()} USDT</b>. "
            f"You can spend it now — pick a product and choose {cemoji('money', '💳')} Pay from wallet.",
            parse_mode="HTML",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not notify %s of wallet credit: %s", user_id, e)


async def _alert_admins_stall(context, blocks_behind: int, cursor: int, safe_head: int) -> None:
    """DM every admin once when the BSC poller falls stall-level behind, so a
    stuck deposit watcher (e.g. all RPC endpoints rate-limited) gets noticed
    instead of only showing up in logs nobody is tailing. Resets in poll_bsc
    once the poller catches back up, so a second stall gets a second alert."""
    global _stall_alerted
    if _stall_alerted:
        return
    _stall_alerted = True
    text = (
        f"{cemoji('warn', '⚠️')} BSC deposit poller is {blocks_behind} blocks behind "
        f"(cursor={cursor}, safe_head={safe_head}). Wallet top-ups on this rail may be "
        f"delayed. Check RPC connectivity (BSC_RPC_URLS)."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not DM admin %s about BSC stall: %s", admin_id, e)


async def send_daily_digest(context) -> None:
    """PTB job: once a day, DM every admin a one-message summary of today's
    sales/deposits and what's still waiting on them. Plain f-string formatting
    (not app.formatting.money()/usdt()) because those helpers render 0 as ""
    / "Free" for buyer-facing prices — a digest needs to show "₹0" on a slow
    day, not a blank field."""
    orders_today = await db.today_summary()
    deposits_today = await db.today_deposit_summary()
    deposits_pending = await db.pending_deposit_summary()
    pending_orders = await db.list_orders(status=db.STATUS_PENDING, limit=1000)

    today = time.strftime("%Y-%m-%d", time.gmtime())
    text = (
        f"{cemoji('chart', '📊')} Daily summary — {today}\n\n"
        f"Sales: ₹{orders_today['inr']:,.0f} · ${orders_today['usdt']:.2f} USDT "
        f"({orders_today['count']} order{'s' if orders_today['count'] != 1 else ''})\n"
        f"Deposits credited: ${deposits_today['micro'] / 1_000_000:.2f} USDT "
        f"({deposits_today['count']})\n\n"
        f"Pending now: {len(pending_orders)} order{'s' if len(pending_orders) != 1 else ''} · "
        f"{deposits_pending['count']} deposit{'s' if deposits_pending['count'] != 1 else ''} "
        f"(${deposits_pending['micro'] / 1_000_000:.2f} USDT)"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not DM admin %s the daily digest: %s", admin_id, e)


async def _alert_admins_binance_pay_stall(context, consecutive_failures: int) -> None:
    """DM every admin once when the Binance Pay poller has failed this many
    times in a row (e.g. an expired API key, or Binance's API down) — same
    one-shot pattern as _alert_admins_stall, but keyed on consecutive
    failures since Binance Pay polling has no block-height cursor to measure
    'behind' against. Resets in poll_binance_pay on the next successful poll."""
    global _binance_pay_alerted
    if _binance_pay_alerted:
        return
    _binance_pay_alerted = True
    text = (
        f"{cemoji('warn', '⚠️')} Binance Pay deposit poller has failed "
        f"{consecutive_failures} times in a row. Wallet top-ups on this rail may be "
        f"delayed. Check BINANCE_API_KEY/BINANCE_API_SECRET and Binance API status."
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not DM admin %s about Binance Pay stall: %s", admin_id, e)


# --------------------------------------------------------------------------- #
# BSC (USDT-BEP20)
# --------------------------------------------------------------------------- #
def _bsc_rpc(payload: dict) -> dict:
    """POST a JSON-RPC call, trying each configured endpoint until one answers."""
    last_err = None
    for url in config.BSC_RPC_URLS:
        try:
            resp = httpx.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise RuntimeError(f"All BSC RPC endpoints failed: {last_err}")


def _latest_block() -> int:
    data = _bsc_rpc({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    return int(data["result"], 16)


def _get_logs(from_block: int, to_block: int) -> list[dict]:
    if from_block > to_block:
        return []
    params = [{
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": USDT_BEP20_CONTRACT,
        "topics": [_TRANSFER_TOPIC, None, _topic_address(config.CRYPTO_ADDRESS)],
    }]
    data = _bsc_rpc({"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs", "params": params})
    return data.get("result", [])


# Most public RPC providers reject eth_getLogs calls spanning more than a few
# thousand blocks — Ankr's free tier specifically caps out around ~1000-1999
# (measured directly; 1000 confirmed working, 1999 rejected). Chunking +
# persisting the cursor after each chunk means a poller that's fallen behind
# (e.g. after downtime) makes steady forward progress across ticks instead of
# needing one giant call to ever succeed.
_MAX_BLOCK_RANGE = 1000
# Cap how many chunks one tick processes, so a large backlog (e.g. after
# downtime) can't turn into one multi-second burst of blocking HTTP calls —
# it instead paces itself across ticks via the job's normal interval.
_MAX_CHUNKS_PER_TICK = 5
# ~50 minutes of BSC blocks (3s/block) — if the cursor is ever this far
# behind, something is stuck (the exact failure mode this poller hit once
# already) and it's worth a loud log line instead of silent catch-up.
_STALL_WARNING_BLOCKS = 1000
_stall_alerted = False
_BINANCE_PAY_FAILURE_THRESHOLD = 5  # ~2 min at the existing 25s poll interval
_binance_pay_consecutive_failures = 0
_binance_pay_alerted = False


async def poll_bsc(context) -> None:
    """PTB job: scan new, sufficiently-confirmed BSC blocks for USDT transfers in."""
    if not config.blockchain_enabled():
        return
    try:
        latest = _latest_block()
        safe_head = latest - config.BSC_CONFIRMATIONS
        cursor = await db.get_cursor(db.RAIL_BSC)
        if cursor is None:
            cursor = safe_head - 1  # first run: start watching from now, not genesis
        global _stall_alerted
        blocks_behind = safe_head - cursor
        if blocks_behind > _STALL_WARNING_BLOCKS:
            logger.warning(
                "BSC poller is %d blocks behind (cursor=%d, safe_head=%d) — catching up",
                blocks_behind, cursor, safe_head,
            )
            await _alert_admins_stall(context, blocks_behind, cursor, safe_head)
        else:
            _stall_alerted = False
        chunks_done = 0
        while cursor < safe_head and chunks_done < _MAX_CHUNKS_PER_TICK:
            chunk_to = min(cursor + _MAX_BLOCK_RANGE, safe_head)
            logs = _get_logs(cursor + 1, chunk_to)
            for log in logs:
                tx_hash = log["transactionHash"]
                raw_value = int(log["data"], 16)
                amount_micro = raw_value // 10**12  # 18 decimals -> micro-USDT (6 decimals)
                user_id = await _credit_if_matched(db.RAIL_BSC, tx_hash, amount_micro)
                if user_id:
                    await _notify_credited(context, user_id, amount_micro)
            cursor = chunk_to
            await db.set_cursor(db.RAIL_BSC, cursor)
            chunks_done += 1
    except Exception as e:  # noqa: BLE001
        logger.warning("BSC deposit poll failed (will retry next tick): %s", e)


async def lookup_bsc_tx(tx_hash: str) -> str:
    """Self-serve recheck: look up one tx hash directly. Returns a user-facing message."""
    if not _BSC_TX_HASH_RE.match(tx_hash):
        # Reject obviously-malformed input before spending an RPC round trip on
        # it — also avoids the raw Go/geth error (e.g. "invalid argument 0:
        # json: cannot unmarshal hex string...") that an unvalidated hash used
        # to leak straight to the buyer.
        return (
            f"{cemoji('warn', '⚠️')} That doesn't look like a valid BSC transaction hash "
            "(should be 0x followed by 64 hex characters). Double-check and paste it again."
        )
    try:
        data = _bsc_rpc({
            "jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [tx_hash],
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("BSC tx lookup failed for %s: %s", tx_hash, e)
        return f"{cemoji('warn', '⚠️')} Couldn't reach the network right now. Try again shortly."
    receipt = data.get("result")
    if not receipt:
        return f"{cemoji('reject', '❌')} Transaction not found. Double-check the hash, or wait a bit if it was just sent."
    tx_block = int(receipt["blockNumber"], 16)
    latest = _latest_block()
    if latest - tx_block < config.BSC_CONFIRMATIONS:
        return f"{cemoji('clock', '⏳')} Found it, but it's still confirming. Try again in about a minute."
    for log in receipt.get("logs", []):
        if (
            log["address"].lower() == USDT_BEP20_CONTRACT
            and log["topics"][0].lower() == _TRANSFER_TOPIC
            and len(log["topics"]) > 2
            and log["topics"][2].lower() == _topic_address(config.CRYPTO_ADDRESS)
        ):
            amount_micro = int(log["data"], 16) // 10**12
            user_id = await _credit_if_matched(db.RAIL_BSC, tx_hash, amount_micro)
            if user_id:
                return f"{cemoji('check', '✅')} Credited <b>{Decimal(amount_micro) / Decimal(1_000_000)} USDT</b> to your wallet."
            if await db.is_tx_processed(db.RAIL_BSC, tx_hash):
                return f"{cemoji('info', 'ℹ️')} This transaction was already credited."
            return f"{cemoji('reject', '❌')} This transfer's amount doesn't match any pending top-up of yours."
    return f"{cemoji('reject', '❌')} No USDT transfer to our address was found in that transaction."


# --------------------------------------------------------------------------- #
# Binance Pay (personal API, best-effort)
# --------------------------------------------------------------------------- #
def _binance_signed_get(path: str, params: dict) -> dict:
    params = {**params, "timestamp": int(time.time() * 1000)}
    query = urlencode(params)
    sig = hmac.new(config.BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    resp = httpx.get(
        f"https://api.binance.com{path}?{query}&signature={sig}",
        headers={"X-MBX-APIKEY": config.BINANCE_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_pay_transactions() -> list[dict]:
    data = _binance_signed_get("/sapi/v1/pay/transactions", {})
    return data.get("data", []) or []


async def poll_binance_pay(context) -> None:
    if not config.binance_pay_autoconfirm_enabled():
        return
    global _binance_pay_consecutive_failures, _binance_pay_alerted
    try:
        txns = _fetch_pay_transactions()
    except Exception as e:  # noqa: BLE001
        logger.warning("Binance Pay poll failed (will retry next tick): %s", e)
        _binance_pay_consecutive_failures += 1
        if _binance_pay_consecutive_failures >= _BINANCE_PAY_FAILURE_THRESHOLD:
            await _alert_admins_binance_pay_stall(context, _binance_pay_consecutive_failures)
        return
    _binance_pay_consecutive_failures = 0
    _binance_pay_alerted = False
    for t in txns:
        # Personal-to-personal Binance Pay transfers (what a buyer sending to your
        # Pay ID actually looks like) come back as orderType "C2C", not "PAY" —
        # "PAY" appears reserved for registered-merchant orders, unavailable on a
        # personal account. Accept both in case that ever differs.
        if t.get("orderType") not in ("PAY", "C2C"):
            continue
        # Only credit transfers we *received* — not ones sent from this account,
        # which could otherwise coincidentally match a pending deposit's amount.
        if str(t.get("receiverInfo", {}).get("binanceId")) != config.BINANCE_PAY_ID:
            continue
        tx_ref = str(t.get("transactionId"))
        try:
            amount_micro = int(Decimal(str(t.get("amount", "0"))) * 1_000_000)
        except Exception:  # noqa: BLE001
            continue
        user_id = await _credit_if_matched(db.RAIL_BINANCE_PAY, tx_ref, amount_micro)
        if user_id:
            await _notify_credited(context, user_id, amount_micro)


async def lookup_binance_pay_txn(txn_id: str) -> str:
    """Self-serve recheck for a Binance Pay transaction id."""
    if not config.binance_pay_autoconfirm_enabled():
        return f"{cemoji('warn', '⚠️')} Binance Pay top-ups aren't enabled."
    try:
        txns = _fetch_pay_transactions()
    except Exception as e:  # noqa: BLE001
        logger.warning("Binance Pay tx lookup failed: %s", e)
        return f"{cemoji('warn', '⚠️')} Couldn't reach Binance right now. Try again shortly."
    for t in txns:
        if str(t.get("transactionId")) == txn_id.strip():
            if t.get("orderType") not in ("PAY", "C2C"):
                continue
            if str(t.get("receiverInfo", {}).get("binanceId")) != config.BINANCE_PAY_ID:
                continue
            amount_micro = int(Decimal(str(t.get("amount", "0"))) * 1_000_000)
            user_id = await _credit_if_matched(db.RAIL_BINANCE_PAY, txn_id, amount_micro)
            if user_id:
                return f"{cemoji('check', '✅')} Credited <b>{Decimal(amount_micro) / Decimal(1_000_000)} USDT</b> to your wallet."
            if await db.is_tx_processed(db.RAIL_BINANCE_PAY, txn_id):
                return f"{cemoji('info', 'ℹ️')} This transaction was already credited."
            return f"{cemoji('reject', '❌')} This transfer's amount doesn't match any pending top-up of yours."
    return f"{cemoji('reject', '❌')} Transaction id not found in recent Binance Pay history."


# --------------------------------------------------------------------------- #
# Manual credits (resolved from the admin panel, not auto-matched)
# --------------------------------------------------------------------------- #
async def poll_manual_credits(context) -> None:
    """PTB job: DM buyers whose stuck deposit an admin manually resolved from the
    admin panel. The panel writes directly to Postgres with no bridge back to this
    process, so it can't send the Telegram message itself — this job is that bridge,
    polling for deposits it hasn't notified about yet."""
    try:
        rows = await db.list_unnotified_manual_credits()
    except Exception as e:  # noqa: BLE001
        logger.warning("Manual-credit notify poll failed (will retry next tick): %s", e)
        return
    for row in rows:
        await _notify_credited(context, row["user_id"], row["delta_micro"])
        await db.mark_deposit_notified(row["id"])


async def _notify_rejected(context, user_id: int, credited_tx_ref: str) -> None:
    """credited_tx_ref holds 'rejected:<adminId>' or 'rejected:<adminId>:<note>'
    (same trick manual credits use to tag who/why, reusing the existing column
    rather than adding a new one) — the optional note becomes the buyer-facing
    reason."""
    note = credited_tx_ref.split(":", 2)[2] if credited_tx_ref.count(":") >= 2 else ""
    reason_line = f"\n\n<b>Reason:</b> {note}" if note else ""
    try:
        await context.bot.send_message(
            user_id,
            f"{cemoji('reject', '❌')} Your wallet top-up request wasn't received.{reason_line}\n\n"
            f"If you already sent it, use {cemoji('search', '🔍')} Check my payment with your TxID.",
            parse_mode="HTML",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not notify %s of deposit rejection: %s", user_id, e)


async def poll_manual_rejections(context) -> None:
    """PTB job: DM buyers whose pending deposit an admin rejected (marked as
    never received) from the admin panel. Mirrors poll_manual_credits — same
    bridge, opposite outcome."""
    try:
        rows = await db.list_unnotified_rejections()
    except Exception as e:  # noqa: BLE001
        logger.warning("Rejection notify poll failed (will retry next tick): %s", e)
        return
    for row in rows:
        await _notify_rejected(context, row["user_id"], row["credited_tx_ref"])
        await db.mark_deposit_notified(row["id"])


class _FakeBot:
    def __init__(self):
        self.sent: list[int] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(chat_id)


class _FakeContext:
    def __init__(self):
        self.bot = _FakeBot()


async def _demo() -> None:
    """Self-check for the money-critical matching path, no network calls. Verifies
    (a) a transfer credits the one pending deposit it's tagged for, exactly once,
    (b) replaying the same tx never double-credits, (c) the amount-tagging helper
    in app.handlers.topup never hands out the same tagged amount to two
    concurrently-pending deposits.

    ponytail: this used to spin up a throwaway sqlite file per run; asyncpg has
    no local-file equivalent, so it now runs against whatever DATABASE_URL points
    at (must be a disposable/test database — it writes real rows). Task 11 owns
    building a proper isolated migration self-check; this is left mechanically
    async so it still compiles and runs against a scratch DB in the meantime.
    """
    await db.init_pool()
    try:
        await db.record_user(1, "A", "a")
        await db.record_user(2, "B", "b")
        await db.create_deposit(1, db.RAIL_BSC, 10_010_000, 45)
        await db.create_deposit(2, db.RAIL_BSC, 20_010_000, 45)

        credited = await _credit_if_matched(db.RAIL_BSC, "0xtx1", 10_010_000)
        assert credited == 1, "should credit user 1's deposit, not user 2's"
        assert await db.get_wallet_balance(1) == 10_010_000
        assert await db.get_wallet_balance(2) == 0

        ledger_1 = await db.get_ledger(1)
        assert len(ledger_1) == 1, "one credit should write exactly one ledger row"
        assert ledger_1[0]["delta_micro"] == 10_010_000
        assert ledger_1[0]["reason"] == "deposit_bsc"
        assert ledger_1[0]["balance_after_micro"] == 10_010_000

        replay = await _credit_if_matched(db.RAIL_BSC, "0xtx1", 10_010_000)
        assert replay is None, "replaying the same tx hash must not double-credit"
        assert await db.get_wallet_balance(1) == 10_010_000

        unmatched = await _credit_if_matched(db.RAIL_BSC, "0xtx2", 99_990_000)
        assert unmatched is None, "an amount with no pending deposit must not credit anyone"

        seen = set()
        for _ in range(5):
            amt = await db.create_tagged_deposit(3, db.RAIL_BSC, 30_000_000, 45)
            assert amt not in seen, "tagged amount handed out twice while still pending"
            seen.add(amt)

        await db.debit_wallet(1, 3_000_000, reason="purchase", ref="999")
        await db.credit_wallet(1, 500_000, reason="refund_stockout", ref="999")
        ledger_total = sum(row["delta_micro"] for row in await db.get_ledger(1))
        assert ledger_total == await db.get_wallet_balance(1), (
            "ledger must always sum to the cached balance — if this fails, a "
            "credit/debit path is updating the balance without logging it"
        )

        global _stall_alerted
        _stall_alerted = False
        fake_ctx = _FakeContext()
        await _alert_admins_stall(fake_ctx, 1500, 100, 1600)
        assert len(fake_ctx.bot.sent) == len(config.ADMIN_IDS), (
            "stall alert should DM every configured admin once"
        )
        await _alert_admins_stall(fake_ctx, 1600, 100, 1700)
        assert len(fake_ctx.bot.sent) == len(config.ADMIN_IDS), (
            "a second stall while already alerted must not re-send (one-shot until caught up)"
        )
        _stall_alerted = False

        global _binance_pay_alerted, _binance_pay_consecutive_failures
        _binance_pay_alerted = False
        fake_ctx_bp = _FakeContext()
        await _alert_admins_binance_pay_stall(fake_ctx_bp, 5)
        assert len(fake_ctx_bp.bot.sent) == len(config.ADMIN_IDS), (
            "Binance Pay stall alert should DM every configured admin once"
        )
        await _alert_admins_binance_pay_stall(fake_ctx_bp, 6)
        assert len(fake_ctx_bp.bot.sent) == len(config.ADMIN_IDS), (
            "a second stall while already alerted must not re-send (one-shot until caught up)"
        )
        _binance_pay_alerted = False
        _binance_pay_consecutive_failures = 0

        # poll_manual_credits: simulate what the admin panel's resolveDeposit action
        # writes directly to Postgres (no Python facade for that write — it's a
        # TypeScript server action, not something this process ever calls itself).
        # A raw UPDATE for the deposit's two changed columns is the closest match;
        # everything else goes through the normal facade.
        await db.record_user(4, "D", "d")
        manual_dep_id = await db.create_deposit(4, db.RAIL_BSC, 40_000_000, 45)
        from app.db.schema import _connect as _raw_connect

        async with _raw_connect() as conn:
            await conn.execute(
                "UPDATE deposits SET status = $1, credited_tx_ref = $2 WHERE id = $3",
                db.DEPOSIT_CREDITED, "manual:999", manual_dep_id,
            )
        await db.credit_wallet(4, 39_500_000, reason="manual_admin_credit", ref=f"deposit:{manual_dep_id}")

        # Exercises list_unnotified_manual_credits/mark_deposit_notified/_notify_credited
        # individually rather than calling poll_manual_credits() itself: that function
        # loops over *every* unnotified manual credit in the database with no filter,
        # so calling it here would also process any real, unrelated rows that happen
        # to exist — silently marking a real buyer "notified" via this fake bot
        # without ever sending them the actual Telegram message. The loop body poll_
        # manual_credits wraps around these three calls is a direct 1:1 pass-through
        # (see its definition), so covering them individually covers it.
        pending_manual = await db.list_unnotified_manual_credits()
        assert any(r["id"] == manual_dep_id for r in pending_manual), (
            "a manually-resolved, not-yet-notified deposit should show up in the poll list"
        )
        our_row = next(r for r in pending_manual if r["id"] == manual_dep_id)
        assert our_row["delta_micro"] == 39_500_000, (
            "the polled amount must be what was actually credited, not the tagged amount"
        )

        fake_ctx2 = _FakeContext()
        await _notify_credited(fake_ctx2, our_row["user_id"], our_row["delta_micro"])
        assert fake_ctx2.bot.sent == [4], "should DM exactly the buyer of the manual credit"

        await db.mark_deposit_notified(manual_dep_id)
        still_pending = await db.list_unnotified_manual_credits()
        assert not any(r["id"] == manual_dep_id for r in still_pending), (
            "mark_deposit_notified must remove the deposit from the unnotified list"
        )

        fake_ctx3 = _FakeContext()
        await send_daily_digest(fake_ctx3)
        assert len(fake_ctx3.bot.sent) == len(config.ADMIN_IDS), (
            "daily digest should DM every configured admin exactly once"
        )
    finally:
        await db.delete_wallet_data_for_users([1, 2, 3, 4], db.RAIL_BSC, "0xtx1")
        await db.delete_users([1, 2, 3, 4])
        await db.close_pool()

    print("crypto_watch self-check: all assertions passed")


if __name__ == "__main__":
    import asyncio

    config.require_disposable_db_for_selfcheck()
    asyncio.run(_demo())
