"""Configuration loaded from environment variables (.env)."""
import json
import os

from dotenv import load_dotenv

load_dotenv()


def _get_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                raise ValueError(f"Invalid ADMIN_IDS entry: {part!r} (must be a number)")
    return ids


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = _get_admin_ids(os.getenv("ADMIN_IDS", ""))
UPI_ID = os.getenv("UPI_ID", "").strip()
UPI_PAYEE_NAME = os.getenv("UPI_PAYEE_NAME", "Store").strip()

# --- Force-join channels (optional) ---
# Comma-separated @usernames a user must join before using the bot. Leave blank
# to disable the force-join check entirely.
REQUIRED_CHANNELS = [
    c.strip().lstrip("@") for c in os.getenv("REQUIRED_CHANNELS", "").split(",") if c.strip()
]

# --- Customer support contact ---
# Telegram @username (without the @) to open when a buyer taps "Customer support".
# If left blank, the bot falls back to opening the first admin's profile by ID.
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@")

# --- Crypto payment (optional; set Binance Pay ID and/or a crypto address to enable) ---
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "").strip()
CRYPTO_ADDRESS = os.getenv("CRYPTO_ADDRESS", "").strip()
CRYPTO_NETWORK = os.getenv("CRYPTO_NETWORK", "USDT (BEP20)").strip()

# --- Auto-confirmed wallet top-ups (optional) ---
# Public BSC RPC endpoints (comma separated) polled for incoming USDT-BEP20
# transfers to CRYPTO_ADDRESS. Rotated on failure so one dead endpoint doesn't
# stall detection.
BSC_RPC_URLS = [
    u.strip() for u in os.getenv(
        "BSC_RPC_URLS",
        "https://bsc-dataseed.bnbchain.org,https://bsc-dataseed1.defibit.io",
    ).split(",") if u.strip()
]
# Binance personal API (read-only key) used to poll /sapi/v1/pay/transactions for
# incoming Binance Pay transfers. Leave blank to not offer this top-up rail.
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
# Decimal places used to tag a top-up amount uniquely (e.g. 2 -> 10.03). Kept low
# because some wallet/exchange withdrawal UIs round off finer decimals.
TOPUP_TAG_DECIMALS = 2
DEPOSIT_EXPIRY_MINUTES = 45
# Blocks to wait behind BSC's chain head before treating a transfer as final
# (matches Binance's own credited-after-15-confirmations threshold for BEP20).
BSC_CONFIRMATIONS = 15

# --- Premium custom emoji on buttons (optional) ---
# Maps a semantic key (e.g. "browse", "buy") to a Telegram custom_emoji_id, shown
# before that button's text. Requires the bot owner to have Telegram Premium (or
# the bot to own a Fragment username) — see app/handlers/menu.py's admin emoji-id
# lookup tool for how to get real IDs. Empty/unknown keys just show no icon.
try:
    CUSTOM_EMOJI_IDS: dict[str, str] = json.loads(os.getenv("CUSTOM_EMOJI_IDS", "") or "{}")
except ValueError:
    CUSTOM_EMOJI_IDS = {}


def _get_float(raw: str, default: float) -> float:
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(raw: str, default: int) -> int:
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Flat fee (in USDT) added on top of a product's USDT price for Binance Pay /
# crypto payments, to cover network/transfer charges so you receive the full
# amount. Example: product 6 USDT + fee 0.2 -> buyer sends 6.2 USDT. Set to 0
# to disable. Does NOT affect UPI (INR) payments.
CRYPTO_FEE_USDT = _get_float(os.getenv("CRYPTO_FEE_USDT", ""), 0.2)

# --- Daily ops digest ---
# UTC hour (0-23) the once-daily admin summary DM is sent. Default 20:00 UTC.
DAILY_DIGEST_HOUR_UTC = _get_int(os.getenv("DAILY_DIGEST_HOUR_UTC", ""), 20)


# Local SQLite database file. Relative paths resolve from wherever the bot is
# started (the project root for `python bot.py`), so the default keeps the DB
# beside the code; point it elsewhere (e.g. ../earn-seller-bot-db-main/store.db)
# to store it in a separate folder/repo. Its parent dir is created at startup.
DB_PATH = os.getenv("DB_PATH", "data/store.db").strip() or "data/store.db"

# Legacy Postgres connection string — no longer used by the bot (it runs on the
# local SQLite file above). Kept only so the historical migrate_to_postgres.py /
# verify_postgres_migration.py tooling still imports; not required to run.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# --- Network / connection to Telegram ---
# Optional proxy for reaching api.telegram.org. Handy when an ISP blocks Telegram.
#   http://127.0.0.1:8080   or   socks5://127.0.0.1:1080
# (socks proxies also need:  pip install "python-telegram-bot[socks]")
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()
# Seconds to wait while establishing the connection to Telegram (default 20).
CONNECT_TIMEOUT = _get_float(os.getenv("CONNECT_TIMEOUT", ""), 20.0)
# How many times to retry the initial connection before giving up (default 5).
STARTUP_RETRIES = _get_int(os.getenv("STARTUP_RETRIES", ""), 5)


def upi_enabled() -> bool:
    return bool(UPI_ID)


def binance_pay_enabled() -> bool:
    return bool(BINANCE_PAY_ID)


def blockchain_enabled() -> bool:
    return bool(CRYPTO_ADDRESS)


def binance_pay_autoconfirm_enabled() -> bool:
    return bool(BINANCE_API_KEY and BINANCE_API_SECRET)


def wallet_topup_enabled() -> bool:
    """Any auto-confirmed top-up rail configured (BSC watch and/or Binance Pay poll)."""
    return blockchain_enabled() or binance_pay_autoconfirm_enabled()


def binance_enabled() -> bool:
    return binance_pay_enabled() or blockchain_enabled()


def _half_configured_rails() -> list[str]:
    """Rails where some but not all required settings are present — these fail
    silently at runtime (inside crypto_watch.py's try/except) instead of at
    startup, so validate() rejects them explicitly instead."""
    problems = []
    if bool(BINANCE_API_KEY) != bool(BINANCE_API_SECRET):
        problems.append(
            "BINANCE_API_KEY and BINANCE_API_SECRET must both be set, or both left blank"
        )
    if blockchain_enabled() and not BSC_RPC_URLS:
        problems.append(
            "CRYPTO_ADDRESS is set but BSC_RPC_URLS is empty — no RPC endpoint to watch it on"
        )
    return problems


def validate() -> None:
    """Fail fast with a clear message if required config is missing."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_IDS")
    if not DB_PATH:
        missing.append("DB_PATH")
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nCopy .env.example to .env and fill in the values."
        )
    if not (upi_enabled() or binance_enabled()):
        raise SystemExit(
            "No payment method configured. Set UPI_ID and/or "
            "BINANCE_PAY_ID / CRYPTO_ADDRESS in .env."
        )
    problems = _half_configured_rails()
    if problems:
        raise SystemExit(
            "Invalid payment configuration:\n- " + "\n- ".join(problems)
        )


def require_disposable_db_for_selfcheck() -> None:
    """Self-check scripts (crypto_watch._demo, products._demo, etc.) write real
    rows and must never run against a real store by accident. Require an explicit
    ALLOW_SELFCHECK_DB=1 alongside DB_PATH, so running e.g.
    `python -m app.services.crypto_watch` with a normal .env loaded fails loudly
    instead of writing test rows into the live SQLite file. Point DB_PATH at a
    disposable/scratch file first (e.g. data/selfcheck.db)."""
    if os.getenv("ALLOW_SELFCHECK_DB", "").strip() != "1":
        raise SystemExit(
            "Refusing to run: this self-check writes real rows to DB_PATH.\n"
            f"Point .env's DB_PATH at a disposable/scratch file (currently {DB_PATH!r}),\n"
            "then re-run with ALLOW_SELFCHECK_DB=1 set (e.g.:\n"
            "  ALLOW_SELFCHECK_DB=1 python -m app.services.crypto_watch"
        )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def support_url() -> str | None:
    """A deep link that opens the support contact's Telegram profile.

    Returns https://t.me/<SUPPORT_USERNAME> (works in apps AND on Telegram Web),
    or None if no username is configured. We avoid tg://user?id= links because
    they do not open in browsers.
    """
    if SUPPORT_USERNAME:
        return f"https://t.me/{SUPPORT_USERNAME}"
    return None


# Runtime-mutable store mode ("auto" | "manual"). Loaded from the DB once at
# startup (app.db.schema.init_pool) and kept in sync live by
# app.db.settings.set_store_mode — not read from .env, so an admin's choice
# survives restarts without a redeploy.
STORE_MODE = "auto"


def is_auto_mode() -> bool:
    return STORE_MODE == "auto"
