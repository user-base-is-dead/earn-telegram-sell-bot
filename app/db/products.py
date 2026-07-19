"""Product CRUD and the auto-delivery key pool."""
from typing import Optional

from app.db import _compat as asyncpg
from app.db.schema import UNLIMITED_STOCK, _connect, _now


async def add_product(
    name: str,
    description: str,
    price: float,
    content: str,
    stock: int = UNLIMITED_STOCK,
    price_inr: float = 0.0,
    name_html: Optional[str] = None,
    description_html: Optional[str] = None,
    icon_char: Optional[str] = None,
    icon_emoji_id: Optional[str] = None,
) -> int:
    async with _connect() as conn:
        return await conn.fetchval(
            """INSERT INTO products
               (name, description, price, price_inr, content, stock, active, created_at,
                name_html, description_html, icon_char, icon_emoji_id)
               VALUES ($1, $2, $3, $4, $5, $6, 1, $7, $8, $9, $10, $11) RETURNING id""",
            name, description, price, price_inr, content, stock, _now(),
            name_html, description_html, icon_char, icon_emoji_id,
        )


async def get_product(product_id: int) -> Optional[asyncpg.Record]:
    async with _connect() as conn:
        return await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)


async def list_products(only_active: bool = True, include_out_of_stock: bool = False) -> list[asyncpg.Record]:
    """List products in display order.

    only_active=True  -> only active (non-deactivated) products.
    include_out_of_stock=True -> also include active products with 0 stock, so the
        catalog can still show them (marked sold-out) instead of hiding them.
    """
    async with _connect() as conn:
        if only_active:
            if include_out_of_stock:
                return await conn.fetch(
                    "SELECT * FROM products WHERE active = 1 ORDER BY id"
                )
            return await conn.fetch(
                """SELECT * FROM products
                   WHERE active = 1 AND (stock = $1 OR stock > 0)
                   ORDER BY id""",
                UNLIMITED_STOCK,
            )
        return await conn.fetch("SELECT * FROM products ORDER BY id")


async def set_product_active(product_id: int, active: bool) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE products SET active = $1 WHERE id = $2",
            1 if active else 0, product_id,
        )


# Only these product fields may be edited from chat (whitelist guards the SQL).
EDITABLE_FIELDS = {
    "name", "description", "price", "price_inr", "stock",
    "name_html", "description_html", "icon_char", "icon_emoji_id",
}


async def update_product(product_id: int, field: str, value) -> None:
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"Field not editable: {field!r}")
    async with _connect() as conn:
        await conn.execute(
            f"UPDATE products SET {field} = $1 WHERE id = $2", value, product_id
        )


async def delete_product(product_id: int) -> None:
    """Hard-delete a product. Raises if any of its keys were ever actually
    used (used_by_order_id ties them to a real order — that FK is the audit
    trail and must block the delete); callers fall back to deactivating
    instead (see products_admin.delete_do). Unused keys carry no such trail,
    so they're cleared first — a keyed product with nothing sold yet is
    fully deletable in one shot instead of tripping its own FK for no reason.
    """
    async with _connect() as conn:
        await conn.execute("DELETE FROM product_keys WHERE product_id = $1 AND used = 0", product_id)
        await conn.execute("DELETE FROM products WHERE id = $1", product_id)


async def decrement_stock(product_id: int, n: int = 1) -> bool:
    """Reserve n units of stock, all-or-nothing. Returns True if reserved,
    False if fewer than n units were available (caller must not treat the
    purchase as fulfilled in that case).

    Single atomic UPDATE (not read-then-write): two admins approving two
    different pending orders for the same limited-stock product at once can't
    both pass a stale stock>=n check and oversell — the WHERE clause is
    evaluated by Postgres against the current row on write, not a value read
    earlier in a separate round trip. Unlimited stock (-1) always matches and
    is left untouched (never decremented, always reports success).
    """
    async with _connect() as conn:
        row = await conn.fetchrow(
            """UPDATE products
               SET stock = CASE WHEN stock = $3 THEN stock ELSE stock - $2 END
               WHERE id = $1 AND (stock = $3 OR stock >= $2)
               RETURNING id""",
            product_id, n, UNLIMITED_STOCK,
        )
        return row is not None


# --------------------------------------------------------------------------- #
# Product key pool (auto-delivered digital codes)
# --------------------------------------------------------------------------- #
async def add_product_keys(product_id: int, codes: list[str]) -> int:
    """Add a batch of unused codes to a product's delivery pool. Returns count added."""
    async with _connect() as conn:
        await conn.executemany(
            "INSERT INTO product_keys (product_id, code) VALUES ($1, $2)",
            [(product_id, c) for c in codes],
        )
        return len(codes)


async def count_unused_keys(product_id: int) -> int:
    async with _connect() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM product_keys WHERE product_id = $1 AND used = 0",
            product_id,
        )


async def get_key_pool_count(product_id: int) -> Optional[tuple[int, int]]:
    """(total_keys, unused_keys) for one product, or None if it has no key pool
    at all — i.e. a "manual"/reseller product with no delivery codes, whose
    typed `stock` number is the real source of truth (see effective_stock)."""
    async with _connect() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE used = 0) AS unused
               FROM product_keys WHERE product_id = $1""",
            product_id,
        )
        if not row or row["total"] == 0:
            return None
        return (row["total"], row["unused"])


async def get_key_pool_counts() -> dict[int, tuple[int, int]]:
    """Same as get_key_pool_count, but for every product with a key pool in one
    query — used by the catalog list so it doesn't need one query per product
    on the page."""
    async with _connect() as conn:
        rows = await conn.fetch(
            """SELECT product_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE used = 0) AS unused
               FROM product_keys GROUP BY product_id"""
        )
        return {r["product_id"]: (r["total"], r["unused"]) for r in rows if r["total"] > 0}


async def clear_unused_keys(product_id: int) -> int:
    """Delete every unused delivery code for a product. Returns count deleted.
    Used codes (used=1) are never touched — they're the audit trail for past
    orders, same reasoning as delete_product's own key cleanup."""
    async with _connect() as conn:
        result = await conn.execute(
            "DELETE FROM product_keys WHERE product_id = $1 AND used = 0", product_id
        )
        # asyncpg execute() returns a command tag string like "DELETE 3".
        return int(result.split()[-1])


def effective_stock(manual_stock: int, key_counts: Optional[tuple[int, int]]) -> int:
    """A keyed ("automatic") product's real stock is its unused-key count, not
    the manually-typed placeholder — the bot-side counterpart of the admin
    panel's identical fix (git log: "Tie displayed product stock to real
    key-pool count"). A product with no key-pool rows at all is "manual"/
    reseller and keeps the typed stock number as the source of truth."""
    if key_counts is None:
        return manual_stock
    return key_counts[1]


async def get_effective_stock(product_id: int, manual_stock: int) -> int:
    return effective_stock(manual_stock, await get_key_pool_count(product_id))


async def set_product_offer(product_id: int, offer_price: float, offer_price_inr: float, offer_until: str) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE products SET offer_price = $1, offer_price_inr = $2, offer_until = $3 WHERE id = $4",
            offer_price, offer_price_inr, offer_until, product_id,
        )


async def clear_offer(product_id: int) -> None:
    async with _connect() as conn:
        await conn.execute(
            "UPDATE products SET offer_price = 0, offer_price_inr = 0, offer_until = '' WHERE id = $1",
            product_id,
        )


def effective_price(p) -> tuple[float, float, bool]:
    """A product's real charge/display price: its discount price while
    offer_until is set and still in the future, else its normal price.
    Pure (no DB call) so callers can use it on a Record they already have,
    the same shape as effective_stock(). ISO-8601 UTC strings compare
    correctly lexicographically (see app.db.wallet's expires_at checks)."""
    offer_until = p["offer_until"] if "offer_until" in p.keys() else ""
    if offer_until and offer_until > _now():
        return float(p["offer_price"]), float(p["offer_price_inr"]), True
    return float(p["price"]), float(p["price_inr"]), False


async def clear_expired_offers() -> list[asyncpg.Record]:
    """Revert every product whose discount window has passed. Returns the
    rows that were cleared, for the caller to log/inspect."""
    async with _connect() as conn:
        return await conn.fetch(
            """UPDATE products SET offer_price = 0, offer_price_inr = 0, offer_until = ''
               WHERE offer_until != '' AND offer_until <= $1
               RETURNING *""",
            _now(),
        )


async def pop_unused_keys(product_id: int, order_id: int, n: int = 1) -> Optional[list[str]]:
    """Atomically claim n unused codes for this product, all-or-nothing. Returns
    the list of n codes, or None if fewer than n were available — in that case
    nothing is mutated (the UPDATE never runs unless all n were found), so a
    shortfall is a clean no-op, not a partial claim.

    Under SQLite this is safe without any row lock: the whole _connect() block
    runs inside one process-wide serialized transaction (see app.db.schema),
    so two simultaneous buyers can't both read the same unused key before either
    UPDATE commits.
    """
    async with _connect() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, code FROM product_keys
                   WHERE product_id = $1 AND used = 0
                   ORDER BY id LIMIT $2""",
                product_id, n,
            )
            if len(rows) < n:
                return None
            ids = [r["id"] for r in rows]
            placeholders = ", ".join(f"${i + 2}" for i in range(len(ids)))
            await conn.execute(
                f"UPDATE product_keys SET used = 1, used_by_order_id = $1 WHERE id IN ({placeholders})",
                order_id, *ids,
            )
            return [r["code"] for r in rows]


async def _demo() -> None:
    """Self-check: two concurrent decrements on a 1-in-stock product must not
    both succeed (oversell). Writes real rows — must be run against a
    disposable/test DATABASE_URL, never production."""
    import asyncio

    # Deferred, aliased import: app/db/__init__.py imports this module to build
    # its facade, so a top-level `from app import db` here would be circular.
    from app import db as _db

    await _db.init_pool()
    try:
        pid = await add_product("selftest-stock", "", 1.0, "content", stock=1)
        await asyncio.gather(
            decrement_stock(pid), decrement_stock(pid), decrement_stock(pid),
        )
        row = await get_product(pid)
        assert row["stock"] == 0, (
            f"expected stock to floor at 0 after 3 concurrent decrements on "
            f"stock=1, got {row['stock']} (oversold or under-decremented)"
        )

        # Qty > 1: all-or-nothing, never goes negative.
        qty_pid = await add_product("selftest-qty", "", 1.0, "content", stock=2)
        ok = await decrement_stock(qty_pid, 3)
        assert ok is False, "reserving 3 units against stock=2 must fail, not oversell"
        row = await get_product(qty_pid)
        assert row["stock"] == 2, f"a failed reservation must not touch stock, got {row['stock']}"
        ok = await decrement_stock(qty_pid, 2)
        assert ok is True, "reserving exactly the available stock must succeed"
        row = await get_product(qty_pid)
        assert row["stock"] == 0, f"expected stock 0 after reserving all 2 units, got {row['stock']}"

        unlimited_pid = await add_product("selftest-unlimited", "", 1.0, "content", stock=UNLIMITED_STOCK)
        ok = await decrement_stock(unlimited_pid, 50)
        assert ok is True, "unlimited stock must always succeed regardless of n"
        row = await get_product(unlimited_pid)
        assert row["stock"] == UNLIMITED_STOCK, "unlimited stock must never actually be decremented"

        await delete_product(pid)
        await delete_product(qty_pid)
        await delete_product(unlimited_pid)
    finally:
        await _db.close_pool()

    print("products self-check: all assertions passed")


async def _demo_effective_stock() -> None:
    """Self-check: a keyed product's effective stock tracks its unused-key
    count regardless of its typed `stock` field; a keyless product falls back
    to the typed field. Writes real rows — must be run against a disposable/
    test DATABASE_URL, never production."""
    from app import db as _db

    await _db.init_pool()
    try:
        keyed_pid = await add_product("selftest-keyed", "", 1.0, "content", stock=0)
        await add_product_keys(keyed_pid, ["CODE-A", "CODE-B", "CODE-C"])
        codes = await pop_unused_keys(keyed_pid, order_id=0, n=1)  # consume one -> 2 left unused
        assert codes == ["CODE-A"], f"expected exactly ['CODE-A'], got {codes}"
        stock = await get_effective_stock(keyed_pid, manual_stock=0)
        assert stock == 2, f"expected effective stock 2 (2 unused keys), got {stock}"

        # Qty > 1: asking for more than available must claim nothing (no partial pop).
        short = await pop_unused_keys(keyed_pid, order_id=1, n=3)
        assert short is None, "requesting 3 keys with only 2 unused must return None"
        stock = await get_effective_stock(keyed_pid, manual_stock=0)
        assert stock == 2, f"a failed n-key claim must not consume any keys, got {stock} left"

        # Qty > 1: asking for exactly what's available must claim all of it atomically.
        codes = await pop_unused_keys(keyed_pid, order_id=2, n=2)
        assert codes is not None and set(codes) == {"CODE-B", "CODE-C"}, (
            f"expected both remaining keys claimed together, got {codes}"
        )
        stock = await get_effective_stock(keyed_pid, manual_stock=0)
        assert stock == 0, f"expected 0 unused keys left, got {stock}"

        manual_pid = await add_product("selftest-manual", "", 1.0, "content", stock=5)
        stock = await get_effective_stock(manual_pid, manual_stock=5)
        assert stock == 5, f"expected effective stock 5 (no key pool, typed stock), got {stock}"

        # delete_product: an all-unused key pool must not block its own delete.
        unused_pid = await add_product("selftest-unused-keys", "", 1.0, "content", stock=0)
        await add_product_keys(unused_pid, ["CODE-D", "CODE-E"])
        await delete_product(unused_pid)  # must not raise
        assert await get_product(unused_pid) is None, "product should be gone"
        assert await count_unused_keys(unused_pid) == 0, "its unused keys should be gone too"

        # delete_product: a USED key must still block the delete (audit trail).
        used_pid = await add_product("selftest-used-keys", "", 1.0, "content", stock=0)
        await add_product_keys(used_pid, ["CODE-F"])
        await pop_unused_keys(used_pid, order_id=99, n=1)
        try:
            await delete_product(used_pid)
            raise AssertionError("deleting a product with a USED key should raise (FK), not silently succeed")
        except AssertionError:
            raise
        except Exception:  # noqa: BLE001  (the expected FK violation)
            pass

        # delete_product only auto-clears UNUSED keys (see its docstring) — every
        # key created above ended up used by the assertions, so they still need
        # clearing by hand here, or cleanup would fail on this product's own FK.
        async with _connect() as conn:
            await conn.execute("DELETE FROM product_keys WHERE product_id = $1", keyed_pid)
            await conn.execute("DELETE FROM product_keys WHERE product_id = $1", used_pid)
        await delete_product(keyed_pid)
        await delete_product(manual_pid)
        await delete_product(used_pid)
    finally:
        await _db.close_pool()

    print("effective-stock self-check: all assertions passed")


async def _demo_clear_unused_keys() -> None:
    """Self-check: clear_unused_keys deletes only unused codes and leaves used
    ones (the audit trail) untouched. Writes real rows — must be run against a
    disposable/test DATABASE_URL, never production."""
    from app import db as _db

    await _db.init_pool()
    try:
        pid = await add_product("selftest-clear-keys", "", 1.0, "content", stock=0)
        await add_product_keys(pid, ["CODE-A", "CODE-B", "CODE-C"])
        await pop_unused_keys(pid, order_id=0, n=1)  # CODE-A becomes used
        deleted = await clear_unused_keys(pid)
        assert deleted == 2, f"expected 2 unused codes deleted, got {deleted}"
        assert await count_unused_keys(pid) == 0, "no unused codes should remain"

        async with _connect() as conn:
            remaining = await conn.fetch(
                "SELECT code FROM product_keys WHERE product_id = $1", pid
            )
        assert {r["code"] for r in remaining} == {"CODE-A"}, (
            f"the used code must survive clear_unused_keys, got {[r['code'] for r in remaining]}"
        )

        empty_deleted = await clear_unused_keys(pid)
        assert empty_deleted == 0, "clearing again with nothing unused must return 0, not error"

        async with _connect() as conn:
            await conn.execute("DELETE FROM product_keys WHERE product_id = $1", pid)
        await delete_product(pid)
    finally:
        await _db.close_pool()

    print("clear-unused-keys self-check: all assertions passed")


async def _demo_effective_price() -> None:
    """Self-check: effective_price uses the offer price only while offer_until
    is in the future; clear_expired_offers reverts only rows whose window has
    passed, and never touches rows with no offer or a future one. Writes real
    rows — must be run against a disposable/test DATABASE_URL, never production."""
    from datetime import datetime, timedelta, timezone

    from app import db as _db

    await _db.init_pool()
    try:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")

        active_pid = await add_product("selftest-offer-active", "", 10.0, "content", price_inr=900.0)
        await set_product_offer(active_pid, 5.0, 450.0, future)
        p = await get_product(active_pid)
        usdt_p, inr_p, discounted = effective_price(p)
        assert (usdt_p, inr_p, discounted) == (5.0, 450.0, True), (
            f"expected discounted price while offer_until is future, got {(usdt_p, inr_p, discounted)}"
        )

        expired_pid = await add_product("selftest-offer-expired", "", 10.0, "content", price_inr=900.0)
        await set_product_offer(expired_pid, 5.0, 450.0, past)
        p = await get_product(expired_pid)
        usdt_p, inr_p, discounted = effective_price(p)
        assert (usdt_p, inr_p, discounted) == (10.0, 900.0, False), (
            f"expected normal price once offer_until is past, got {(usdt_p, inr_p, discounted)}"
        )

        no_offer_pid = await add_product("selftest-offer-none", "", 10.0, "content", price_inr=900.0)
        p = await get_product(no_offer_pid)
        usdt_p, inr_p, discounted = effective_price(p)
        assert (usdt_p, inr_p, discounted) == (10.0, 900.0, False), (
            f"expected normal price with no offer set, got {(usdt_p, inr_p, discounted)}"
        )

        cleared = await clear_expired_offers()
        cleared_ids = {r["id"] for r in cleared}
        assert expired_pid in cleared_ids, "expired offer must be cleared"
        assert active_pid not in cleared_ids, "future offer must not be cleared"
        assert no_offer_pid not in cleared_ids, "product with no offer must not be touched"

        p = await get_product(expired_pid)
        assert p["offer_until"] == "" and p["offer_price"] == 0, "cleared row must have offer fields reset"
        p = await get_product(active_pid)
        assert p["offer_until"] == future, "future offer must be untouched by clear_expired_offers"

        await clear_offer(active_pid)
        p = await get_product(active_pid)
        assert p["offer_until"] == "" and p["offer_price"] == 0, "clear_offer must reset all offer fields"

        await delete_product(active_pid)
        await delete_product(expired_pid)
        await delete_product(no_offer_pid)
    finally:
        await _db.close_pool()

    print("effective-price self-check: all assertions passed")


if __name__ == "__main__":
    import asyncio

    from app import config

    config.require_disposable_db_for_selfcheck()
    asyncio.run(_demo())
    asyncio.run(_demo_effective_stock())
    asyncio.run(_demo_clear_unused_keys())
    asyncio.run(_demo_effective_price())
