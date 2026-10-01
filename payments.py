"""USDT (BEP20) payment verification on BNB Smart Chain. Nothing in here knows about Telegram.

The scheme follows tg-kiro-sell-bot:

* Every order gets its own amount: the price plus a random sub-cent tail, e.g. $5.20 -> 5.2043
  USDT. A blockchain transfer carries no order number, so the amount *is* the order number, and
  no two orders that can still be paid ever share one (`pick_amount`).
* Incoming transfers are read straight from a BSC node (eth_getLogs): only `Transfer` events of
  the real USDT contract into the shop wallet. A fake token calling itself "USDT" never counts.
* A transfer pays an order only when all of these hold:
    - the amount carries that order's tail (within 0.00004 USDT) or, if no order fits that
      closely, it is within 3 cents of exactly one order (wallets that round, small fees);
    - it was sent after the order was created and before the order stopped being payable;
    - CONFIRMATIONS blocks have been built on top of it;
    - it was never used before: its (tx hash, log index) is stored when first processed.
  A transfer that fits more than one order is never guessed at: nobody gets it, admins are told.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

from db import Database, Row, Settlement, Transfer

log = logging.getLogger(__name__)

# Binance-Peg BSC-USD: the token everyone means by "USDT BEP20". 18 decimals.
USDT_CONTRACT = "0x55d398326f99059ff775485246999027b3197955"
# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

UNIT = 10**14  # token base units in 0.0001 USDT, the precision order amounts are kept in
CENT = 10**16  # token base units in 0.01 USDT
EXACT_TOLERANCE = 4 * 10**13  # 0.00004 USDT: under half the 0.0001 gap between two tails
NEAR_CENTS = 3  # how far off (in cents, after rounding) a rounded or fee-shaved payment may be
DUST = CENT  # transfers under 0.01 USDT are address-poisoning spam and are ignored
TAILS = range(1, 100)  # 0.0001 ... 0.0099 added on top of the price

CONFIRMATIONS = 5  # blocks built on top of a transfer before it counts (a few seconds on BSC)
POLL_SECONDS = 15  # how often the chain is checked
LATE_MINUTES = 120  # a payment arriving this long after its order expired is still accepted
CLOCK_SLACK = 120  # seconds allowed between block timestamps and this machine's clock
RESCAN_BLOCKS = 200  # every check re-reads this many already-read blocks, in case a node lagged
LOG_SPAN = 2000  # preferred blocks per eth_getLogs request (shrunk per endpoint if refused)
MIN_LOG_SPAN = 25  # smallest span we fall back to for endpoints that cap eth_getLogs ranges
MAX_SPANS = 10  # eth_getLogs requests per check while catching up after downtime


def pick_amount(base_units: int, taken: set[int]) -> int | None:
    """A random amount for a new order: its price plus a tail that no payable order is using."""
    free = [base_units + tail for tail in TAILS if base_units + tail not in taken]
    return secrets.choice(free) if free else None


def matching(value_raw: int, candidates: Sequence[Row]) -> list[Row]:
    """The orders a transfer of `value_raw` could be paying. Only a single result is a payment."""
    exact, near = [], []
    for inv in candidates:
        diff = abs(value_raw - inv["amount_units"] * UNIT)
        if diff <= EXACT_TOLERANCE:
            exact.append(inv)
        elif (diff + CENT // 2) // CENT <= NEAR_CENTS:  # the difference, rounded to the cent
            near.append(inv)
    # The tail identifies one order, so an exact hit wins outright. The 3-cent window is far
    # wider than the gap between two tails, which is why it is only a fallback.
    return exact or near


def usdt(units: int) -> str:
    """An order amount (in 0.0001 USDT) as text: 52043 -> '5.2043'."""
    return f"{units // 10_000}.{units % 10_000:04d}"


def usdt_raw(value_raw: int) -> str:
    """Token base units as text, 4 decimals."""
    return f"{Decimal(value_raw) / 10**18:.4f}"


def parse_log(entry: dict, wallet_topic: str) -> Transfer | None:
    """Check one eth_getLogs entry ourselves instead of trusting the node's filter."""
    try:
        topics = [t.lower() for t in entry["topics"]]
        data = entry["data"]
        if (
            entry.get("removed")
            or entry["address"].lower() != USDT_CONTRACT
            or len(topics) != 3
            or topics[0] != TRANSFER_TOPIC
            or topics[2] != wallet_topic
            or len(data) != 66
        ):
            return None
        return Transfer(
            tx_hash=entry["transactionHash"].lower(),
            log_index=int(entry["logIndex"], 16),
            block_number=int(entry["blockNumber"], 16),
            from_address="0x" + topics[1][-40:],
            value_raw=int(data, 16),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class RpcError(RuntimeError):
    pass


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {exc}"[:200]


# JSON-RPC has no standard code for "your eth_getLogs block range is too wide", so match the
# message. Seen in the wild: "limit exceeded", "limited to 0 - 50 blocks range", "range must not
# exceed 25 blocks". A false positive only costs one wasted retry at a smaller span.
_RANGE_HINTS = ("limit", "range", "exceed", "too many", "too large", "up to")


def _is_range_error(exc: Exception) -> bool:
    return isinstance(exc, RpcError) and any(hint in str(exc).lower() for hint in _RANGE_HINTS)


class Chain:
    """A minimal BSC JSON-RPC client that fails over between endpoints.

    Endpoints differ in how wide an eth_getLogs block range they allow: publicnode serves
    thousands at once, while some free nodes cap it at 25-50 blocks. The log scan adapts per
    endpoint -- when a node refuses a span as too wide, that node's span is shrunk and remembered,
    so capped nodes still work as fallbacks when the preferred ones are blocked (publicnode returns
    HTTP 403 to some server IPs, and with one provider that stops payments entirely).
    """

    def __init__(self, urls: Sequence[str], wallet: str) -> None:
        self.urls = list(urls)
        self.wallet_topic = "0x" + wallet.lower()[2:].rjust(64, "0")
        self._current = 0
        self._span = {url: LOG_SPAN for url in self.urls}  # eth_getLogs block span per endpoint
        self._http: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _advance(self) -> None:
        self._current = (self._current + 1) % len(self.urls)

    async def _post(self, url: str, method: str, params: list):
        """One JSON-RPC call to one endpoint. Raises on transport, HTTP, or RPC-level error."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15)
        resp = await self._http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RpcError(str(data["error"])[:200])
        return data["result"]

    async def _call(self, method: str, params: list):
        """A call that fails over to the next endpoint on any error."""
        problems = []
        for _ in self.urls:
            url = self.urls[self._current]
            try:
                return await self._post(url, method, params)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, RpcError) as exc:
                # Only the host is reported: a keyed endpoint carries its API key in the URL.
                problems.append(f"{urlsplit(url).hostname}: {_describe(exc)}")
                self._advance()
        raise RpcError(f"{method} failed on every endpoint ({'; '.join(problems)})")

    async def head(self) -> int:
        return int(await self._call("eth_blockNumber", []), 16)

    async def block_time(self, number: int) -> int:
        block = await self._call("eth_getBlockByNumber", [hex(number), False])
        if not block:
            raise RpcError(f"block {number} is not available yet")
        return int(block["timestamp"], 16)

    async def incoming(self, start: int, end: int) -> list[dict]:
        """USDT Transfer logs into the shop wallet in blocks start..end (inclusive).

        The range is read in chunks sized to the current endpoint. A chunk refused for being too
        wide shrinks that endpoint's span and is retried on the same endpoint; any other error
        (403, 429, timeout) fails over to the next endpoint. The whole range is read before
        anything is returned, so a caller never advances its cursor over a half-read span.
        """
        logs: list[dict] = []
        cursor = start
        problems: list[str] = []
        stale = 0  # consecutive failures with no forward progress
        while cursor <= end:
            if stale >= len(self.urls) * 6:
                raise RpcError(f"eth_getLogs failed on every endpoint ({'; '.join(problems[-len(self.urls):])})")
            url = self.urls[self._current]
            span = self._span.get(url, LOG_SPAN)
            stop = min(cursor + span - 1, end)
            flt = {
                "fromBlock": hex(cursor),
                "toBlock": hex(stop),
                "address": USDT_CONTRACT,
                "topics": [TRANSFER_TOPIC, None, self.wallet_topic],
            }
            try:
                logs += await self._post(url, "eth_getLogs", [flt]) or []
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, RpcError) as exc:
                problems.append(f"{urlsplit(url).hostname}: {_describe(exc)}")
                stale += 1
                if _is_range_error(exc) and span > MIN_LOG_SPAN:
                    self._span[url] = max(MIN_LOG_SPAN, span // 4)  # this node wants a smaller range
                else:
                    self._advance()  # 403/429/timeout, or already at the floor: try the next endpoint
                continue
            cursor = stop + 1
            stale = 0
        return logs


@dataclass
class ScanResult:
    settled: list[tuple[Transfer, Settlement]] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False  # the previous check was too recent


class Scanner:
    """Reads new blocks and settles every incoming transfer exactly once.

    The last block read is stored (the cursor), so after downtime the bot catches up instead of
    missing payments, and every check re-reads RESCAN_BLOCKS behind the cursor in case a node
    answered before it had those blocks. Re-reading is harmless: settled transfers are remembered.
    Runs are serialised by a lock, so it is safe to trigger from several places.
    """

    def __init__(self, db: Database, chain: Chain, *, window: int) -> None:
        self.db = db
        self.chain = chain
        self.window = window  # seconds after its creation that an order can still be paid
        self._lock = asyncio.Lock()
        self._last_run = 0.0

    async def run(self, *, min_interval: float = 0.0) -> ScanResult:
        result = ScanResult()
        async with self._lock:
            if time.monotonic() - self._last_run < min_interval:
                result.skipped = True
                return result
            try:
                await self._scan(result)
            except RpcError as exc:
                result.error = str(exc)
                log.warning("Payment check failed: %s", exc)
            except Exception as exc:  # keep the settlements already made so they get announced
                result.error = f"{type(exc).__name__}: {exc}"
                log.exception("Payment check crashed")
            self._last_run = time.monotonic()
        return result

    async def _scan(self, result: ScanResult) -> None:
        safe_head = await self.chain.head() - CONFIRMATIONS
        cursor = await self.db.get_cursor()
        if cursor is None:  # first start: nothing on chain can have paid an order yet
            cursor = safe_head
            await self.db.set_cursor(cursor)
        start = cursor - RESCAN_BLOCKS + 1
        for _ in range(MAX_SPANS):
            if start > safe_head:
                break
            end = min(start + LOG_SPAN - 1, safe_head)
            await self._settle(await self.chain.incoming(start, end), result)
            if end > cursor:
                cursor = end
                await self.db.set_cursor(cursor)
            start = end + 1

    async def _settle(self, logs: list[dict], result: ScanResult) -> None:
        transfers = [t for entry in logs if (t := parse_log(entry, self.chain.wallet_topic)) is not None]
        transfers.sort(key=lambda t: (t.block_number, t.log_index))
        block_times: dict[int, int] = {}
        for t in transfers:
            if await self.db.transfer_seen(t.tx_hash, t.log_index):
                continue
            if t.value_raw >= DUST:
                if t.block_number not in block_times:
                    block_times[t.block_number] = await self.chain.block_time(t.block_number)
                t.block_time = block_times[t.block_number]
            s = await self.db.settle_transfer(t, window=self.window, slack=CLOCK_SLACK, dust=DUST, match=matching)
            if s.note == "seen":
                continue
            result.settled.append((t, s))
            log.info(
                "Transfer %s:%d, %s USDT from %s: %s%s",
                t.tx_hash,
                t.log_index,
                usdt_raw(t.value_raw),
                t.from_address,
                s.note,
                f" -> order #{s.invoice['id']} ({s.invoice['status']})" if s.invoice is not None else "",
            )
