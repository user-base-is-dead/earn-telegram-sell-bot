"""Settings, read from .env (see .env.example).

Anything required that is missing or malformed stops the bot at startup with a message saying
what to fix, instead of running half-configured.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Public BSC endpoints that allow eth_getLogs. (The bsc-dataseed*.bnbchain.org nodes answer other
# calls but reject eth_getLogs with "limit exceeded", so they cannot detect payments.)
DEFAULT_RPC_URLS = ("https://bsc-rpc.publicnode.com", "https://bsc.publicnode.com")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    wallet_address: str  # where buyers send USDT (BEP20), exactly as written in .env
    rpc_urls: tuple[str, ...]
    fee_cents: int  # flat fee added to every order
    payment_minutes: int  # how long an order stays open (stock is not held for it)
    store_name: str
    support_username: str  # without "@"; empty = none
    db_file: Path
    proxy: str  # optional proxy for reaching Telegram; empty = none
    announce_minutes: tuple[int, int] | None  # random gap between stock announcements; None = off


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def load() -> Config:
    load_dotenv(BASE_DIR / ".env")

    token = _get("BOT_TOKEN")
    if not re.fullmatch(r"[0-9]+:[\w-]{30,}", token):
        raise ConfigError("BOT_TOKEN is missing or malformed. Get one from @BotFather.")

    try:
        admin_ids = frozenset(int(x) for x in _get("ADMIN_IDS").split(",") if x.strip())
    except ValueError:
        raise ConfigError("ADMIN_IDS must be numeric Telegram user IDs, comma separated.") from None
    if not admin_ids:
        raise ConfigError("ADMIN_IDS is empty. Add your numeric Telegram user ID (ask @userinfobot).")

    wallet = _get("WALLET_ADDRESS")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet):
        raise ConfigError("WALLET_ADDRESS must be your USDT BEP20 (BSC) address: 0x + 40 hex characters.")

    rpc_urls = tuple(u.strip() for u in _get("BSC_RPC_URLS").split(",") if u.strip()) or DEFAULT_RPC_URLS
    if not all(u.startswith(("https://", "http://")) for u in rpc_urls):
        raise ConfigError("BSC_RPC_URLS must be http(s) URLs, comma separated.")

    try:
        fee = Decimal(_get("FEE_USDT", "0"))
    except InvalidOperation:
        raise ConfigError("FEE_USDT must be a number, e.g. 0.2") from None
    if not fee.is_finite() or not 0 <= fee <= 100:
        raise ConfigError("FEE_USDT must be between 0 and 100.")

    try:
        minutes = int(_get("PAYMENT_MINUTES", "30"))
    except ValueError:
        raise ConfigError("PAYMENT_MINUTES must be a whole number.") from None
    if not 5 <= minutes <= 180:
        raise ConfigError("PAYMENT_MINUTES must be between 5 and 180.")

    db_file = Path(_get("DB_FILE", "data/shop.db"))
    if not db_file.is_absolute():
        db_file = BASE_DIR / db_file

    announce = _get("ANNOUNCE_MINUTES", "120-240").replace(" ", "")
    announce_minutes = None
    if announce not in ("0", "off"):
        m = re.fullmatch(r"([0-9]{1,5})(?:-([0-9]{1,5}))?", announce)
        low, high = (int(m.group(1)), int(m.group(2) or m.group(1))) if m else (0, 0)
        if not 5 <= low <= high <= 10080:
            raise ConfigError("ANNOUNCE_MINUTES must look like 120-240 (minutes, between 5 and 10080), or 0 for off.")
        announce_minutes = (low, high)

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        wallet_address=wallet,
        rpc_urls=rpc_urls,
        fee_cents=int((fee * 100).to_integral_value(ROUND_HALF_UP)),
        payment_minutes=minutes,
        store_name=_get("STORE_NAME", "Digital Store"),
        support_username=_get("SUPPORT_USERNAME").lstrip("@"),
        db_file=db_file,
        proxy=_get("TELEGRAM_PROXY"),
        announce_minutes=announce_minutes,
    )
