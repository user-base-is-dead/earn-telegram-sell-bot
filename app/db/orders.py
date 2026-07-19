"""Order lifecycle, listing/cleanup, and earnings."""
import secrets
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from app.db.schema import STATUS_APPROVED, STATUS_CREATED, STATUS_PENDING, STATUS_REJECTED, _REF_ALPHABET, _connect, _now


async def _gen_ref(conn: asyncpg.Connection) -> str:
    """A unique, non-repeating public order reference like '260618-K7P2'."""
    date = datetime.now(timezone.utc).strftime("%y%m%d")
    while True:
        suffix = "".join(secrets.choice(_REF_ALPHABET) for _ in range(4))
        ref = f"{date}-{suffix}"
        exists = await conn.fetchval("SELECT 1 FROM orders WHERE ref = $1", ref)
        if not exists:
            return ref


async def create_order(
    user_id: int, username: str, product_id: int, product_name: str, amount: float,
    amount_usdt: float = 0.0, qty: int = 1,
) -> int:
    now = _now()
    async with _connect() as conn:
        ref = await _gen_ref(conn)
        return await conn.fetchval(
            """INSERT INTO orders
               (user_id, username, product_id, product_name, amount, amount_usdt, qty, status, ref, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id""",
            user_id, username, product_id, product_name, amount, amount_usdt, qty,
            STATUS_CREATED, ref, now, now,
        )


async def get_order(order_id: int) -> Optional[asyncpg.Record]:
    async with _connect() as conn:
        return await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)


async def set_order_status(order_id: int, status: str, expected_status: Optional[str] = None) -> bool:
    """Set an order's status. If `expected_status` is given, this is a compare-
    and-swap: the update only applies if the order is still in that status, so
    two admins racing to approve/reject the same order (or an approve racing a
    reject) can't both act on it. Returns whether the update actually applied."""
    async with _connect() as conn:
        if expected_status is None:
            await conn.execute(
                "UPDATE orders SET status = $1, updated_at = $2 WHERE id = $3",
                status, _now(), order_id,
            )
            return True
        result = await conn.execute(
            "UPDATE orders SET status = $1, updated_at = $2 WHERE id = $3 AND status = $4",
            status, _now(), order_id, expected_status,
        )
        return result.endswith(" 1")


async def set_order_method(order_id: int, method: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE orders SET method = $1, updated_at = $2 WHERE id = $3",
            method, _now(), order_id,
        )


async def reject_order(order_id: int, reason: str, expected_status: Optional[str] = None) -> bool:
    """Mark an order rejected and record the reason shown to the buyer. Same
    compare-and-swap as set_order_status when `expected_status` is given."""
    async with _connect() as conn:
        if expected_status is None:
            await conn.execute(
                "UPDATE orders SET status = $1, reason = $2, updated_at = $3 WHERE id = $4",
                STATUS_REJECTED, reason, _now(), order_id,
            )
            return True
        result = await conn.execute(
            "UPDATE orders SET status = $1, reason = $2, updated_at = $3 WHERE id = $4 AND status = $5",
            STATUS_REJECTED, reason, _now(), order_id, expected_status,
        )
        return result.endswith(" 1")


async def set_order_utr(order_id: int, utr: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE orders SET utr = $1, status = $2, updated_at = $3 WHERE id = $4",
            utr, STATUS_PENDING, _now(), order_id,
        )


async def list_orders(
    status: Optional[str] = None,
    limit: int = 20,
    include_created: bool = True,
    user_id: Optional[int] = None,
) -> list[asyncpg.Record]:
    """List orders, newest first. Filters combine:
    - status: exact status, OR
    - include_created=False: everything except abandoned ('created') orders
    - user_id: only this buyer's orders
    """
    clauses: list[str] = []
    params: list = []
    if status:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    elif not include_created:
        params.append(STATUS_CREATED)
        clauses.append(f"status != ${len(params)}")
    if user_id is not None:
        params.append(user_id)
        clauses.append(f"user_id = ${len(params)}")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    async with _connect() as conn:
        return await conn.fetch(
            f"SELECT * FROM orders{where} ORDER BY id DESC LIMIT ${len(params)}", *params
        )


async def count_user_orders(user_id: int, status: Optional[str] = None) -> int:
    """How many orders this buyer placed (optionally filtered to one status) —
    used on their profile screen."""
    async with _connect() as conn:
        if status:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM orders WHERE user_id = $1 AND status = $2",
                user_id, status,
            )
        return await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE user_id = $1 AND status != $2",
            user_id, STATUS_CREATED,
        )


async def list_abandoned_orders(limit: int = 50) -> list[asyncpg.Record]:
    """Orders that were created but never paid (buyer left without paying)."""
    async with _connect() as conn:
        return await conn.fetch(
            "SELECT * FROM orders WHERE status = $1 ORDER BY id DESC LIMIT $2",
            STATUS_CREATED, limit,
        )


async def clear_abandoned_orders() -> int:
    """Delete only abandoned (unpaid/created) orders. Returns count deleted."""
    async with _connect() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE status = $1", STATUS_CREATED
        )
        await conn.execute("DELETE FROM orders WHERE status = $1", STATUS_CREATED)
        return n


async def clear_successful_orders() -> int:
    """Delete only non-abandoned orders (pending/approved/rejected). Returns count."""
    async with _connect() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE status != $1", STATUS_CREATED
        )
        await conn.execute("DELETE FROM orders WHERE status != $1", STATUS_CREATED)
        # Reset the id sequence only if no orders remain at all.
        remaining = await conn.fetchval("SELECT COUNT(*) FROM orders")
        if remaining == 0:
            await conn.execute("ALTER SEQUENCE orders_id_seq RESTART WITH 1")
        return n


async def clear_data(wipe_products: bool = False) -> dict:
    """Delete all orders (and optionally all products), resetting id numbering so
    the next row starts at #1.

    No backup is made — the caller is responsible for confirming first. Orders are
    always deleted before products, so the products foreign key is never violated.
    Returns how many rows were removed: ``{"orders": n, "products": m}``.
    """
    async with _connect() as conn:
        orders = await conn.fetchval("SELECT COUNT(*) FROM orders")
        await conn.execute("DELETE FROM orders")
        await conn.execute("ALTER SEQUENCE orders_id_seq RESTART WITH 1")
        products = 0
        if wipe_products:
            products = await conn.fetchval("SELECT COUNT(*) FROM products")
            await conn.execute("DELETE FROM products")
            await conn.execute("ALTER SEQUENCE products_id_seq RESTART WITH 1")
        return {"orders": orders, "products": products}


async def earnings_summary() -> dict:
    """Totals across all approved (delivered) orders."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount), 0) AS inr, COALESCE(SUM(amount_usdt), 0) AS usdt,
                      COUNT(*) AS n
               FROM orders WHERE status = $1""",
            STATUS_APPROVED,
        )
        return {"inr": row["inr"], "usdt": row["usdt"], "count": row["n"]}


async def today_summary() -> dict:
    """Approved-order totals for today (UTC calendar day) — powers the admin
    daily digest DM. Separate from earnings_summary(), which is all-time."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(amount), 0) AS inr, COALESCE(SUM(amount_usdt), 0) AS usdt,
                      COUNT(*) AS n
               FROM orders
               WHERE status = $1 AND created_at >= $2""",
            STATUS_APPROVED, today_start,
        )
        return {"inr": row["inr"], "usdt": row["usdt"], "count": row["n"]}
