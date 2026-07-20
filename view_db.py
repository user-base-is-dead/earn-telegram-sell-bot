"""Human-readable view / export of the Postgres store.

Usage:
    python view_db.py            # pretty-print products & orders to the console
    python view_db.py --json     # also write a readable data/export.json
    python view_db.py --orders pending_review   # filter orders by status
    python view_db.py --ledger 123456789        # dump one user's wallet transaction history
"""
import asyncio
import json
import os
import sys

from app import db
from app.db import schema as db_schema


async def fetch(status_filter: str | None = None):
    async with db_schema._connect() as conn:
        products = [dict(r) for r in await conn.fetch("SELECT * FROM products ORDER BY id")]
        if status_filter:
            orders = [
                dict(r) for r in await conn.fetch(
                    "SELECT * FROM orders WHERE status = $1 ORDER BY id DESC", status_filter,
                )
            ]
        else:
            orders = [dict(r) for r in await conn.fetch("SELECT * FROM orders ORDER BY id DESC")]
        return products, orders


def _print_block(title: str, rows: list[dict], fields: list[str]) -> None:
    print(f"\n=== {title}  ({len(rows)}) ===")
    if not rows:
        print("  (empty)")
        return
    for r in rows:
        print("  " + "  |  ".join(f"{f}: {r.get(f)}" for f in fields))


async def main() -> None:
    await db_schema.init_pool()
    try:
        status_filter = None
        if "--orders" in sys.argv:
            i = sys.argv.index("--orders")
            if i + 1 < len(sys.argv):
                status_filter = sys.argv[i + 1]

        if "--ledger" in sys.argv:
            i = sys.argv.index("--ledger")
            user_id = int(sys.argv[i + 1])
            rows = [dict(r) for r in await db.get_ledger(user_id)]
            _print_block(
                f"WALLET LEDGER [user {user_id}]", rows,
                ["id", "delta_micro", "reason", "ref", "balance_after_micro", "created_at"],
            )
            return

        products, orders = await fetch(status_filter)

        _print_block("PRODUCTS [price=USDT]", products, ["id", "name", "price", "stock", "active"])
        _print_block(
            f"ORDERS [amount=USDT]{f' [{status_filter}]' if status_filter else ''}",
            orders,
            ["id", "product_name", "amount_usdt", "status", "utr", "user_id", "created_at"],
        )

        if "--json" in sys.argv:
            os.makedirs("data", exist_ok=True)
            out = os.path.join("data", "export.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(
                    {"products": products, "orders": orders},
                    f, indent=2, ensure_ascii=False, default=str,
                )
            print(f"\nReadable JSON written to: {out}")
    finally:
        await db_schema.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
