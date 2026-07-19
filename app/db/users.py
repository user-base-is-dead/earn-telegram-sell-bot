"""Broadcast audience tracking."""
import asyncpg

from app.db.schema import _connect, _now


async def record_user(user_id: int, first_name: str = "", username: str = "") -> None:
    """Remember a user the bot can later message. Idempotent (called on every update)."""
    async with _connect() as conn:
        await conn.execute(
            """INSERT INTO users (user_id, first_name, username, started_at, clicks)
               VALUES ($1, $2, $3, $4, 1)
               ON CONFLICT (user_id) DO UPDATE SET
                   first_name = EXCLUDED.first_name,
                   username   = EXCLUDED.username,
                   clicks     = users.clicks + 1""",
            user_id, first_name or "", username or "", _now(),
        )


async def get_user(user_id: int):
    async with _connect() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


async def set_user_notes(user_id: int, notes: str) -> None:
    async with _connect() as conn:
        await conn.execute("UPDATE users SET notes = $1 WHERE user_id = $2", notes, user_id)


async def all_users() -> list[asyncpg.Record]:
    """All tracked users, newest first."""
    async with _connect() as conn:
        return await conn.fetch("SELECT * FROM users ORDER BY started_at DESC")


async def all_recipient_ids() -> list[int]:
    """Every user to broadcast to: those who used the bot, plus anyone who ever
    placed an order (covers customers from before user-tracking existed)."""
    async with _connect() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM users UNION SELECT DISTINCT user_id FROM orders"
        )
        return [r["user_id"] for r in rows]


async def delete_users(user_ids: list[int]) -> None:
    """Used only by app.services.crypto_watch's self-check to clean up the
    throwaway users it creates, so its self-check can be re-run without
    manual DB intervention."""
    async with _connect() as conn:
        await conn.execute("DELETE FROM users WHERE user_id = ANY($1::bigint[])", user_ids)
