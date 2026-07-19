"""Wallet balance, pending deposits, and idempotency/cursor bookkeeping for the
auto-confirmed crypto/Binance Pay top-up watchers (app.services.crypto_watch)."""
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import _compat as asyncpg
from app.db.schema import DEPOSIT_CREDITED, DEPOSIT_PENDING, DEPOSIT_REJECTED, _connect, _now


async def get_wallet_balance(user_id: int) -> int:
    """Balance in integer micro-USDT (amount * 1_000_000)."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            "SELECT wallet_balance_micro FROM users WHERE user_id = $1", user_id
        )
        return row["wallet_balance_micro"] if row else 0


async def _log_ledger(conn: asyncpg.Connection, user_id: int, delta_micro: int, reason: str, ref: str) -> None:
    balance_after = await conn.fetchval(
        "SELECT wallet_balance_micro FROM users WHERE user_id = $1", user_id
    )
    await conn.execute(
        """INSERT INTO wallet_ledger (user_id, delta_micro, reason, ref, balance_after_micro, created_at)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        user_id, delta_micro, reason, ref, balance_after, _now(),
    )


async def credit_wallet(user_id: int, amount_micro: int, reason: str, ref: str = "") -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro + $1 WHERE user_id = $2",
            amount_micro, user_id,
        )
        await _log_ledger(conn, user_id, amount_micro, reason, ref)


async def debit_wallet(user_id: int, amount_micro: int, reason: str, ref: str = "") -> bool:
    """Atomically deduct if the balance covers it. Returns False (no-op) if insufficient.

    FOR UPDATE locks the row for the life of the transaction, so a second
    concurrent debit (e.g. a double-tapped Buy button) blocks until this one
    commits and then sees the already-reduced balance, instead of both reading
    the same stale balance and both passing the check.
    """
    async with _connect() as conn:
        row = await conn.fetchrow(
            "SELECT wallet_balance_micro FROM users WHERE user_id = $1 FOR UPDATE", user_id
        )
        if not row or row["wallet_balance_micro"] < amount_micro:
            return False
        await conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro - $1 WHERE user_id = $2",
            amount_micro, user_id,
        )
        await _log_ledger(conn, user_id, -amount_micro, reason, ref)
        return True


async def get_ledger(user_id: int) -> list[asyncpg.Record]:
    """A user's full transaction history, oldest first."""
    async with _connect() as conn:
        return await conn.fetch(
            "SELECT * FROM wallet_ledger WHERE user_id = $1 ORDER BY id", user_id
        )


async def create_deposit(user_id: int, rail: str, tagged_amount_micro: int, expires_minutes: int) -> int:
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=expires_minutes)).isoformat(timespec="seconds")
    async with _connect() as conn:
        return await conn.fetchval(
            """INSERT INTO deposits (user_id, rail, tagged_amount_micro, status, created_at, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            user_id, rail, tagged_amount_micro, DEPOSIT_PENDING, _now(), expires_at,
        )


async def create_tagged_deposit(user_id: int, rail: str, base_micro: int, expires_minutes: int) -> int:
    """Create a pending deposit tagged with an amount unique among this rail's
    pending deposits, nudging up by 0.001 steps and retrying on the rare race
    where two concurrent requests try the same amount — the partial unique
    index on (rail, tagged_amount_micro) WHERE status='pending' makes that
    collision impossible to persist, so this can't silently hand out the same
    tag twice the way a plain check-then-insert would. Returns the tagged
    amount actually used.

    The starting tag is nudged up by a random 0.003-0.009 USDT, never the exact
    round amount a buyer thinks of (e.g. "$10") — so if a different buyer gets
    sloppy and sends that flat round figure instead of their own assigned tag,
    it matches no one's deposit (a safe no-op, resolved via "check my payment")
    instead of coincidentally matching whichever buyer's tag happened to be
    that round number. Both BSC (6-decimal micro-USDT precision from 18-decimal
    on-chain values) and Binance Pay's transaction amounts carry more than 2
    decimals, so the tag can stay under a cent instead of costing buyers a
    whole 0.03-0.09 USDT.
    """
    TAG_STEP = 1_000  # 0.001 USDT
    base_micro = (base_micro // TAG_STEP) * TAG_STEP
    if base_micro <= 0:
        base_micro = TAG_STEP
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=expires_minutes)).isoformat(timespec="seconds")
    amount = base_micro + random.randint(3, 9) * TAG_STEP
    for _ in range(100):
        try:
            async with _connect() as conn:
                await conn.execute(
                    """INSERT INTO deposits (user_id, rail, tagged_amount_micro, status, created_at, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    user_id, rail, amount, DEPOSIT_PENDING, _now(), expires_at,
                )
            return amount
        except asyncpg.UniqueViolationError:
            amount += TAG_STEP
    raise RuntimeError("Could not find a free tagged deposit amount after 100 attempts")


async def is_tx_processed(rail: str, tx_ref: str) -> bool:
    async with _connect() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM processed_tx WHERE rail = $1 AND tx_ref = $2)",
            rail, tx_ref,
        )


async def credit_deposit_once(rail: str, tx_ref: str, amount_micro: int, reason: str) -> Optional[int]:
    """Atomically match `tx_ref` to its pending deposit and credit it, exactly once.

    Both the periodic chain poller and the buyer-triggered "check my payment"
    lookup can reach this for the same transfer at nearly the same time.
    Matching, crediting and marking used to be four separate round-trips
    (find_matching_deposit -> credit_wallet -> mark_deposit_credited ->
    mark_tx_processed), each its own transaction, so two concurrent calls
    could both see the deposit as still-pending and both credit it. Here,
    `SELECT ... FOR UPDATE` locks the deposit row for the whole transaction:
    a second concurrent call blocks until the first commits, then re-reads
    status = 'credited' and finds no pending match. The processed_tx insert
    (unique on rail+tx_ref) is the second guard, for replays of the same tx
    against a *different* still-pending deposit.

    Returns the credited user_id, or None if nothing matched / already credited.
    """
    async with _connect() as conn:
        dep = await conn.fetchrow(
            """SELECT * FROM deposits
               WHERE rail = $1 AND tagged_amount_micro = $2 AND status = $3 AND expires_at > $4
               ORDER BY id LIMIT 1 FOR UPDATE""",
            rail, amount_micro, DEPOSIT_PENDING, _now(),
        )
        if not dep:
            return None
        try:
            await conn.execute(
                "INSERT INTO processed_tx (rail, tx_ref, credited_at) VALUES ($1, $2, $3)",
                rail, tx_ref, _now(),
            )
        except asyncpg.UniqueViolationError:
            return None
        await conn.execute(
            "UPDATE users SET wallet_balance_micro = wallet_balance_micro + $1 WHERE user_id = $2",
            amount_micro, dep["user_id"],
        )
        await _log_ledger(conn, dep["user_id"], amount_micro, reason, tx_ref)
        await conn.execute(
            "UPDATE deposits SET status = $1, credited_tx_ref = $2 WHERE id = $3",
            DEPOSIT_CREDITED, tx_ref, dep["id"],
        )
        return dep["user_id"]


async def get_cursor(rail: str) -> Optional[int]:
    async with _connect() as conn:
        row = await conn.fetchrow(
            "SELECT last_block FROM chain_cursor WHERE rail = $1", rail
        )
        return row["last_block"] if row else None


async def set_cursor(rail: str, last_block: int) -> None:
    async with _connect() as conn:
        await conn.execute(
            """INSERT INTO chain_cursor (rail, last_block) VALUES ($1, $2)
               ON CONFLICT (rail) DO UPDATE SET last_block = EXCLUDED.last_block""",
            rail, last_block,
        )


async def list_unnotified_manual_credits() -> list[asyncpg.Record]:
    """Deposits an admin resolved manually (from the admin panel) whose buyer hasn't
    been DM'd yet. Joins to wallet_ledger to get the amount actually credited, which
    can differ from tagged_amount_micro — the whole point of a manual resolve is the
    admin confirming what the buyer really sent."""
    async with _connect() as conn:
        return await conn.fetch(
            """SELECT d.id, d.user_id, l.delta_micro
               FROM deposits d
               JOIN wallet_ledger l ON l.ref = 'deposit:' || d.id AND l.reason = 'manual_admin_credit'
               WHERE d.status = $1 AND d.credited_tx_ref LIKE 'manual:%' AND d.notified = 0""",
            DEPOSIT_CREDITED,
        )


async def mark_deposit_notified(deposit_id: int) -> None:
    async with _connect() as conn:
        await conn.execute("UPDATE deposits SET notified = 1 WHERE id = $1", deposit_id)


async def list_unnotified_rejections() -> list[asyncpg.Record]:
    """Deposits an admin rejected (from the admin panel) whose buyer hasn't been
    DM'd yet. No wallet_ledger join needed here — unlike a manual credit,
    nothing was ever credited, so there's no amount to report."""
    async with _connect() as conn:
        return await conn.fetch(
            "SELECT id, user_id, credited_tx_ref FROM deposits WHERE status = $1 AND notified = 0",
            DEPOSIT_REJECTED,
        )


async def delete_wallet_data_for_users(user_ids: list[int], rail: str, tx_ref: str) -> None:
    """Delete a set of users' wallet_ledger/deposits rows plus one processed_tx
    row. Used only by app.services.crypto_watch's self-check to clean up after
    itself, so its self-check can be re-run without manual DB intervention."""
    async with _connect() as conn:
        if user_ids:
            placeholders = ", ".join(f"${i + 1}" for i in range(len(user_ids)))
            await conn.execute(
                f"DELETE FROM wallet_ledger WHERE user_id IN ({placeholders})", *user_ids
            )
            await conn.execute(
                f"DELETE FROM deposits WHERE user_id IN ({placeholders})", *user_ids
            )
        await conn.execute(
            "DELETE FROM processed_tx WHERE rail = $1 AND tx_ref = $2", rail, tx_ref
        )


async def today_deposit_summary() -> dict:
    """Credited-deposit totals for today (UTC calendar day) — powers the admin
    daily digest DM."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(tagged_amount_micro), 0) AS micro, COUNT(*) AS n
               FROM deposits
               WHERE status = $1 AND created_at >= $2""",
            DEPOSIT_CREDITED, today_start,
        )
        return {"micro": row["micro"], "count": row["n"]}


async def pending_deposit_summary() -> dict:
    """All currently-pending deposits, regardless of age — powers the admin
    daily digest DM's 'needs attention' line."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(tagged_amount_micro), 0) AS micro, COUNT(*) AS n
               FROM deposits WHERE status = $1""",
            DEPOSIT_PENDING,
        )
        return {"micro": row["micro"], "count": row["n"]}
