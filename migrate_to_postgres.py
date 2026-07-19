"""One-off: copy every row from the old SQLite store into the new Postgres DB.

Run once during cutover:
    python migrate_to_postgres.py path/to/old/store.db

Requires DATABASE_URL to already point at the target Postgres instance (.env),
and that the bot has been started at least once against it (or app.db.schema.init_pool
has otherwise created the schema) so the tables already exist.
"""
import asyncio
import sqlite3
import sys

import asyncpg

from app import config


async def _copy_table(sconn: sqlite3.Connection, pconn: asyncpg.Connection, table: str, columns: list[str]) -> int:
    rows = sconn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
    await pconn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [tuple(row) for row in rows],
    )
    return len(rows)


async def main(sqlite_path: str) -> None:
    sconn = sqlite3.connect(sqlite_path)
    sconn.row_factory = sqlite3.Row
    pconn = await asyncpg.connect(config.DATABASE_URL)

    # FK-safe order: products before orders/product_keys.
    plan = [
        ("products", ["id", "name", "description", "price", "price_inr", "content", "stock",
                       "active", "created_at", "offer_price", "offer_price_inr", "offer_until"]),
        ("users", ["user_id", "first_name", "username", "started_at", "clicks", "wallet_balance_micro"]),
        ("orders", ["id", "user_id", "username", "product_id", "product_name", "amount",
                     "amount_usdt", "status", "utr", "method", "ref", "reason",
                     "created_at", "updated_at"]),
        ("product_keys", ["id", "product_id", "code", "used", "used_by_order_id"]),
        ("deposits", ["id", "user_id", "rail", "tagged_amount_micro", "status",
                       "created_at", "expires_at", "credited_tx_ref"]),
        ("wallet_ledger", ["id", "user_id", "delta_micro", "reason", "ref",
                            "balance_after_micro", "created_at"]),
        ("processed_tx", ["id", "rail", "tx_ref", "credited_at"]),
        ("chain_cursor", ["rail", "last_block"]),
    ]

    for table, columns in plan:
        n = await _copy_table(sconn, pconn, table, columns)
        print(f"{table}: copied {n} rows")

    # Realign Postgres BIGSERIAL sequences past the max copied id, so new inserts
    # don't collide with the migrated rows.
    for table in ("products", "orders", "product_keys", "deposits", "wallet_ledger", "processed_tx"):
        await pconn.execute(
            f"SELECT setval('{table}_id_seq', COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )

    await pconn.close()
    sconn.close()
    print("Migration complete.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python migrate_to_postgres.py path/to/old/store.db")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
