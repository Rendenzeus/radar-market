"""Market data feeds with incremental updates and an on-disk candle cache.

The first sync downloads ~10 days of 15-minute candles. Every refresh after
that only downloads the last few candles (a few hundred bytes) and merges them
into the cache, which is what keeps the refresh loop fast.
"""

from __future__ import annotations

import gzip
import json
import os
import ssl
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

INTERVAL_SECONDS = 15 * 60
MAX_CANDLES = 1200
FULL_RESYNC_SECONDS = 30 * 60

_HEADERS = {
    # Yahoo answers 429 to full browser user-agents without cookies; keep it short.
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}


def _ssl_context() -> ssl.SSLContext:
    # Android builds of Python often ship without system CA certificates.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - certifi missing
        return ssl.create_default_context()


_SSL = _ssl_context()


def _get_json(url: str, timeout: float = 8.0):
    with urlopen(Request(url, headers=_HEADERS), timeout=timeout, context=_SSL) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


@dataclass(frozen=True)
class Candle:
    t: int  # open time, epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def closed(self) -> bool:
        return self.t + INTERVAL_SECONDS <= time.time()


@dataclass(frozen=True)
class Quote:
    price: float
    time: float  # epoch seconds of the last trade/quote


class Feed:
    """Base class. Subclasses implement ``_fetch(full)``."""

    symbol: str
    label: str
    short: str
    source: str
    always_open: bool = False
    recent_window: float = 3600  # seconds an incremental request safely covers
    refresh_seconds: float = 15

    def _fetch(self, full: bool) -> tuple[list[Candle], Quote]:
        raise NotImplementedError


class BinanceFeed(Feed):
    """Binance spot klines: real-time, the forming candle carries live volume."""

    HOSTS = ("https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com")

    def __init__(self, pair: str, label: str, short: str):
        self.symbol = pair
        self.label = label
        self.short = short
        self.source = "Binance spot · real-time"
        self.always_open = True
        self.recent_window = 30 * 60  # limit=3 -> 45 minutes of candles
        self.refresh_seconds = 3
        self._host = self.HOSTS[0]

    def _fetch(self, full: bool) -> tuple[list[Candle], Quote]:
        query = urlencode({"symbol": self.symbol, "interval": "15m", "limit": 1000 if full else 3})
        last_error: Exception | None = None
        for host in (self._host, *[h for h in self.HOSTS if h != self._host]):
            try:
                rows = _get_json(f"{host}/api/v3/klines?{query}")
                self._host = host
                break
            except Exception as exc:  # try the next mirror
                last_error = exc
        else:
            raise ConnectionError(f"Binance tidak dapat dihubungi: {last_error}")

        candles = [
            Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows
        ]
        return candles, Quote(candles[-1].c, time.time())


class YahooFeed(Feed):
    """Yahoo Finance chart API (used for COMEX gold futures)."""

    def __init__(self, ticker: str, label: str, short: str, source: str):
        self.symbol = ticker
        self.label = label
        self.short = short
        self.source = source
        self.recent_window = 12 * 3600  # range=1d
        self.refresh_seconds = 15

    def _fetch(self, full: bool) -> tuple[list[Candle], Quote]:
        query = urlencode({"interval": "15m", "range": "15d" if full else "1d", "includePrePost": "true"})
        payload = None
        for host in ("query1", "query2"):
            try:
                payload = _get_json(f"https://{host}.finance.yahoo.com/v8/finance/chart/{self.symbol}?{query}")
                break
            except Exception as exc:
                last_error = exc
        if payload is None:
            raise ConnectionError(f"Yahoo Finance tidak dapat dihubungi: {last_error}")
        chart = payload.get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise ValueError("Yahoo Finance tidak mengembalikan data.")
        item = chart["result"][0]
        meta = item.get("meta", {})
        quote = (item.get("indicators", {}).get("quote") or [{}])[0]
        candles = []
        for t, o, h, l, c, v in zip(
            item.get("timestamp") or [],
            quote.get("open") or [],
            quote.get("high") or [],
            quote.get("low") or [],
            quote.get("close") or [],
            quote.get("volume") or [],
        ):
            if None in (o, h, l, c):
                continue
            # Align to the 15m grid; Yahoo's last point is sometimes stamped "now".
            t = int(t) - int(t) % INTERVAL_SECONDS
            candles.append(Candle(t, float(o), float(h), float(l), float(c), float(v or 0)))
        if not candles:
            raise ValueError("Data candle kosong.")
        price = float(meta.get("regularMarketPrice") or candles[-1].c)
        quote_time = float(meta.get("regularMarketTime") or candles[-1].t)
        return candles, Quote(price, quote_time)


class MarketState:
    """Holds the candle cache for one feed and merges incremental updates."""

    def __init__(self, feed: Feed, cache_dir: Path):
        self.feed = feed
        self.candles: list[Candle] = []
        self.quote: Quote | None = None
        self.last_full_sync = 0.0
        self.last_latency_ms = 0
        self.last_success = 0.0
        self._cache_file = cache_dir / f"{feed.symbol.replace('=', '_')}.json"
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            raw = json.loads(self._cache_file.read_text("utf-8"))
            self.candles = [Candle(**c) for c in raw["candles"]]
            q = raw.get("quote")
            self.quote = Quote(**q) if q else None
        except Exception:
            self.candles = []

    def save_cache(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"candles": [asdict(c) for c in self.candles], "quote": asdict(self.quote) if self.quote else None}
            self._cache_file.write_text(json.dumps(data, separators=(",", ":")), "utf-8")
        except Exception:
            pass

    def _merge(self, fresh: list[Candle]) -> None:
        by_time = {c.t: c for c in self.candles}
        by_time.update((c.t, c) for c in fresh)  # newest data wins
        self.candles = sorted(by_time.values(), key=lambda c: c.t)[-MAX_CANDLES:]

    def refresh(self) -> None:
        """Blocking network refresh; run it in a worker thread."""
        now = time.time()
        full = (
            len(self.candles) < 300
            or now - self.last_full_sync > FULL_RESYNC_SECONDS
            # App was suspended longer than an incremental request can cover.
            or now - self.last_success > self.feed.recent_window
        )
        started = time.perf_counter()
        fresh, quote = self.feed._fetch(full)
        self.last_latency_ms = int((time.perf_counter() - started) * 1000)
        if full:
            self.candles = fresh[-MAX_CANDLES:]
            self.last_full_sync = time.time()
            self.save_cache()
        else:
            self._merge(fresh)
        self.quote = quote
        self.last_success = time.time()


def default_cache_dir() -> Path:
    base = os.getenv("FLET_APP_STORAGE_DATA")
    return Path(base) / "candles" if base else Path.home() / ".market-radar" / "candles"


FEEDS: tuple[Feed, ...] = (
    YahooFeed("GC=F", "Gold", "XAU", "COMEX futures · Yahoo"),
    BinanceFeed("BTCUSDT", "Bitcoin", "BTC"),
)
