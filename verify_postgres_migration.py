"""Self-check for the Postgres migration: exercises create/read/update across every
table plus the wallet-ledger invariant, against DATABASE_URL. Uses throwaway rows
only (a fake user_id / product), does not touch real data.

Run: python verify_postgres_migration.py
"""
import asyncio

from app import config, db
from app.db.schema import close_pool, init_pool

_FAKE_USER = -999999001  # negative, unreachable by any real Telegram user_id


async def main() -> None:
    config.validate()
    await init_pool()

    # Products + key pool + concurrent-safe pop.
    product_id = await db.add_product("Selfcheck product", "desc", 1.0, "secret-content", stock=2)
    added = await db.add_product_keys(product_id, ["KEY-A", "KEY-B"])
    assert added == 2
    assert await db.count_unused_keys(product_id) == 2

    # Orders.
    order_id = await db.create_order(_FAKE_USER, "selfcheck", product_id, "Selfcheck product", 1.0)
    key = await db.pop_unused_key(product_id, order_id)
    assert key in ("KEY-A", "KEY-B")
    assert await db.count_unused_keys(product_id) == 1
    await db.set_order_status(order_id, db.STATUS_APPROVED)
    order = await db.get_order(order_id)
    assert order["status"] == db.STATUS_APPROVED

    # Wallet + ledger invariant: SUM(delta_micro) must equal current balance.
    # credit_wallet/debit_wallet only UPDATE an existing users row (no upsert), so
    # the fake user needs a row first — record_user is idempotent and safe to call.
    await db.record_user(_FAKE_USER)
    await db.credit_wallet(_FAKE_USER, 5_000_000, "selfcheck_credit")
    ok = await db.debit_wallet(_FAKE_USER, 2_000_000, "selfcheck_debit")
    assert ok is True
    balance = await db.get_wallet_balance(_FAKE_USER)
    assert balance == 3_000_000
    ledger = await db.get_ledger(_FAKE_USER)
    assert sum(r["delta_micro"] for r in ledger) == balance

    # Cleanup the throwaway rows. NOTE: db.clear_data() deletes ALL rows in the
    # orders table, not just this order — unsafe to call here if the script is run
    # against a live DB that already has real orders. Delete only this order's own
    # row instead.
    async with db.schema._connect() as conn:  # noqa: SLF001 (self-check only)
        await conn.execute("DELETE FROM orders WHERE id = $1", order_id)
        await conn.execute("DELETE FROM product_keys WHERE product_id = $1", product_id)
        await conn.execute("DELETE FROM products WHERE id = $1", product_id)
        await conn.execute(
            "DELETE FROM wallet_ledger WHERE user_id = $1", _FAKE_USER
        )
        await conn.execute("DELETE FROM users WHERE user_id = $1", _FAKE_USER)

    await close_pool()
    print("Postgres migration self-check passed.")


if __name__ == "__main__":
    asyncio.run(main())
