"""Global bot settings — currently just the auto/manual store-mode switch."""
from app import config
from app.db.schema import STORE_MODE_AUTO, _connect


async def get_store_mode() -> str:
    async with _connect() as conn:
        mode = await conn.fetchval("SELECT store_mode FROM bot_settings WHERE id = 1")
        return mode or STORE_MODE_AUTO


async def set_store_mode(mode: str) -> None:
    """Persist the new mode and update the in-memory cache other modules read
    synchronously (app.config.is_auto_mode()) so the change is visible on the
    very next keyboard render, with no extra DB round trip anywhere else."""
    async with _connect() as conn:
        await conn.execute("UPDATE bot_settings SET store_mode = $1 WHERE id = 1", mode)
    config.STORE_MODE = mode
