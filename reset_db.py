"""Safely clean the Postgres store. Always writes a JSON backup of affected rows first.

Usage:
    python reset_db.py              # delete ALL orders + reset order numbering (keeps products)
    python reset_db.py --abandoned  # delete ONLY abandoned (unpaid 'created') orders
    python reset_db.py --all        # also delete ALL products + reset their numbering
    python reset_db.py --users      # also delete ALL users + wallet ledger/deposits/tx history
    add  --yes  to skip the confirmation prompt

--all and --users compose (e.g. `--all --users --yes` wipes everything: orders,
products, product_keys, users, wallet_ledger, deposits, processed_tx, chain_cursor).
bot_settings (the store-mode toggle) is never touched — it's config, not data.

A JSON backup of every row about to be deleted is written to
data/reset_db_backup_<timestamp>.json before anything is removed.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from app import db
from app.db import schema as db_schema


def _confirm(msg: str) -> bool:
    if "--yes" in sys.argv or "-y" in sys.argv:
        return True
    try:
        return input(f"{msg} [y/N]: ").strip().lower() == "y"
    except EOFError:
        return False


async def main() -> None:
    await db_schema.init_pool()
    try:
        abandoned = "--abandoned" in sys.argv
        wipe_all = "--all" in sys.argv
        wipe_users = "--users" in sys.argv
        if abandoned:
            action = "Delete ONLY abandoned (unpaid) orders — existing IDs kept"
        else:
            action = "Delete ALL orders and reset order numbering"
            if wipe_all:
                action += ", AND ALL products"
            if wipe_users:
                action += ", AND ALL users/wallet balances/deposit history"

        if not _confirm(f"{action}.\nContinue?"):
            print("Cancelled.")
            return

        async with db_schema._connect() as conn:
            o_before = await conn.fetchval("SELECT COUNT(*) FROM orders")
            orders_backup = [dict(r) for r in await conn.fetch("SELECT * FROM orders")]
            products_backup = (
                [dict(r) for r in await conn.fetch("SELECT * FROM products")] if wipe_all else []
            )
            # product_keys has a NOT NULL FK to products.id (see app/db/schema.py) —
            # must be cleared before products or the DELETE trips a FK violation,
            # same reason app/db/products.py::delete_product clears a product's own
            # keys first for the single-product case.
            product_keys_backup = (
                [dict(r) for r in await conn.fetch("SELECT * FROM product_keys")] if wipe_all else []
            )
            users_backup = [dict(r) for r in await conn.fetch("SELECT * FROM users")] if wipe_users else []
            wallet_ledger_backup = (
                [dict(r) for r in await conn.fetch("SELECT * FROM wallet_ledger")] if wipe_users else []
            )
            deposits_backup = (
                [dict(r) for r in await conn.fetch("SELECT * FROM deposits")] if wipe_users else []
            )
            processed_tx_backup = (
                [dict(r) for r in await conn.fetch("SELECT * FROM processed_tx")] if wipe_users else []
            )

            os.makedirs("data", exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            bak_path = os.path.join("data", f"reset_db_backup_{ts}.json")
            with open(bak_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "orders": orders_backup, "products": products_backup, "product_keys": product_keys_backup,
                        "users": users_backup, "wallet_ledger": wallet_ledger_backup,
                        "deposits": deposits_backup, "processed_tx": processed_tx_backup,
                    },
                    f, indent=2, ensure_ascii=False, default=str,
                )
            print(f"Backup saved: {bak_path}")

            if abandoned:
                await conn.execute("DELETE FROM orders WHERE status = $1", db.STATUS_CREATED)
                print("Removed abandoned orders (existing IDs kept).")
            else:
                await conn.execute("DELETE FROM orders")
                remaining = await conn.fetchval("SELECT COUNT(*) FROM orders")
                if remaining == 0:
                    await conn.execute("ALTER SEQUENCE orders_id_seq RESTART WITH 1")
                print("Cleared all orders; the next order will be #1.")
            if wipe_all:
                await conn.execute("DELETE FROM product_keys")
                await conn.execute("ALTER SEQUENCE product_keys_id_seq RESTART WITH 1")
                await conn.execute("DELETE FROM products")
                await conn.execute("ALTER SEQUENCE products_id_seq RESTART WITH 1")
                print("Cleared all products (and their delivery keys); the next product will be #1.")
            if wipe_users:
                # No FK constraints tie these to users.user_id (see app/db/schema.py),
                # so order doesn't matter for safety — deleting dependents first is
                # just cleaner bookkeeping. chain_cursor is also cleared: with no
                # deposits left to match against, resuming from an old block cursor
                # would just rescan history for nothing.
                await conn.execute("DELETE FROM wallet_ledger")
                await conn.execute("ALTER SEQUENCE wallet_ledger_id_seq RESTART WITH 1")
                await conn.execute("DELETE FROM deposits")
                await conn.execute("ALTER SEQUENCE deposits_id_seq RESTART WITH 1")
                await conn.execute("DELETE FROM processed_tx")
                await conn.execute("ALTER SEQUENCE processed_tx_id_seq RESTART WITH 1")
                await conn.execute("DELETE FROM chain_cursor")
                await conn.execute("DELETE FROM users")
                print("Cleared all users, wallet balances, and deposit/transaction history.")

            o_after = await conn.fetchval("SELECT COUNT(*) FROM orders")
            p_now = await conn.fetchval("SELECT COUNT(*) FROM products")
            u_now = await conn.fetchval("SELECT COUNT(*) FROM users")

        print(f"\nOrders: {o_before} -> {o_after}    Products now: {p_now}    Users now: {u_now}")
        print("Done. The JSON backup above lets you manually re-insert rows if needed.")
    finally:
        await db_schema.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
