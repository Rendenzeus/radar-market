"""Market data feeds with incremental updates and an on-disk candle cache.

The first sync for a (market, timeframe) downloads enough history for the
indicators. Every refresh after that only downloads the last few candles and
merges them into the cache, which is what keeps the refresh loop fast.
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

MAX_CANDLES = 1500
FULL_RESYNC_SECONDS = 30 * 60


@dataclass(frozen=True)
class Timeframe:
    key: str  # label shown in the UI and Binance interval code
    seconds: int
    htf_key: str  # higher timeframe used for trend confirmation


TIMEFRAMES: dict[str, Timeframe] = {
    tf.key: tf
    for tf in (
        Timeframe("1m", 60, "15m"),
        Timeframe("5m", 300, "1h"),
        Timeframe("15m", 900, "1h"),
        Timeframe("30m", 1800, "4h"),
        Timeframe("1h", 3600, "4h"),
        Timeframe("4h", 14400, "1d"),
        Timeframe("1d", 86400, "1w"),
    )
}
HTF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 7 * 86400}
DEFAULT_TIMEFRAME = "15m"

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


def _get_text(url: str, timeout: float = 8.0) -> str:
    with urlopen(Request(url, headers=_HEADERS), timeout=timeout, context=_SSL) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _get_json(url: str, timeout: float = 8.0):
    return json.loads(_get_text(url, timeout))


@dataclass(frozen=True)
class Candle:
    t: int  # open time, epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float


def is_closed(candle: Candle, seconds: int, now: float | None = None) -> bool:
    return candle.t + seconds <= (now if now is not None else time.time())


def resample(candles: list[Candle], seconds: int) -> list[Candle]:
    """Aggregate candles into buckets of ``seconds`` (UTC aligned)."""
    out: list[Candle] = []
    for c in candles:
        bucket = c.t - c.t % seconds
        if out and out[-1].t == bucket:
            last = out[-1]
            out[-1] = Candle(bucket, last.o, max(last.h, c.h), min(last.l, c.l), c.c, last.v + c.v)
        else:
            out.append(Candle(bucket, c.o, c.h, c.l, c.c, c.v))
    return out


@dataclass(frozen=True)
class OrderFlow:
    """Aggressor flow. The scored part is accumulated over 15 closed 1m candles
    (taker-buy volume from klines); the ~60s trade window and the order-book
    imbalance are too noisy for a 15m decision and are shown as info only."""

    buy_ratio_15m: float  # taker-buy volume / total volume, last 15 closed minutes
    buy_ratio_5m: float  # last 5 closed minutes
    buy_ratio_prev10m: float  # the 10 minutes before that
    cvd_15m: float  # cumulative volume delta (buy - sell, base asset) over 15 minutes
    buy_ratio_60s: float  # info only
    imbalance: float  # info only: bid qty / ask qty within 0.25% of mid price
    window: float  # seconds covered by the 60s trade sample
    time: float


@dataclass(frozen=True)
class Quote:
    price: float
    time: float  # epoch seconds of the last trade/quote


class Feed:
    """Base class. Subclasses implement ``fetch(tf, full)``."""

    symbol: str
    label: str
    short: str
    source: str
    always_open: bool = False
    refresh_seconds: float = 15
    # Round-trip trading cost as a fraction of price (fees + slippage, both sides).
    cost_pct: float = 0.0
    cost_note: str = ""
    # Shortest timeframe with enough history to test the signal; below it the
    # app shows the chart but issues no trade signals.
    min_signal_seconds: int = 0
    default_timeframe: str = DEFAULT_TIMEFRAME

    def fetch(self, tf: Timeframe, full: bool) -> tuple[list[Candle], Quote]:
        raise NotImplementedError

    def recent_window(self, tf: Timeframe) -> float:
        """Seconds of history an incremental request safely covers."""
        raise NotImplementedError

    def fetch_flow(self) -> OrderFlow | None:
        """Order-flow snapshot, or None when the venue does not publish it."""
        return None


class BinanceFeed(Feed):
    """Binance spot klines: real-time, the forming candle carries live volume."""

    HOSTS = ("https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com")

    def __init__(self, pair: str, label: str, short: str):
        self.symbol = pair
        self.label = label
        self.short = short
        self.source = "Binance spot · real-time"
        self.always_open = True
        # Binance spot Regular User: 0.10% maker/taker per side + ~0.03% slippage per side.
        self.cost_pct = 2 * (0.0010 + 0.0003)
        self.cost_note = "biaya 0,1%/sisi + slippage 0,03%/sisi"
        self.refresh_seconds = 3
        self._host = self.HOSTS[0]

    def recent_window(self, tf: Timeframe) -> float:
        return tf.seconds * 2  # limit=3 -> current + 2 previous candles

    def _get(self, path: str):
        last_error: Exception | None = None
        for host in (self._host, *[h for h in self.HOSTS if h != self._host]):
            try:
                data = _get_json(f"{host}{path}")
                self._host = host
                return data
            except Exception as exc:  # try the next mirror
                last_error = exc
        raise ConnectionError(f"Binance tidak dapat dihubungi: {last_error}")

    def fetch_flow(self) -> OrderFlow:
        klines = self._get(f"/api/v3/klines?symbol={self.symbol}&interval=1m&limit=16")[:-1]  # drop forming minute
        trades = self._get(f"/api/v3/aggTrades?symbol={self.symbol}&limit=1000")
        book = self._get(f"/api/v3/depth?symbol={self.symbol}&limit=100")

        def kratio(rows) -> float:
            vol = sum(float(r[5]) for r in rows)
            return sum(float(r[9]) for r in rows) / vol if vol else 0.5  # r[9]: taker-buy base volume

        cvd = sum(2 * float(r[9]) - float(r[5]) for r in klines)
        buy = sum(float(r["q"]) for r in trades if not r["m"])  # m=True: buyer was maker -> taker sold
        total = sum(float(r["q"]) for r in trades)
        best_bid = float(book["bids"][0][0])
        best_ask = float(book["asks"][0][0])
        mid = (best_bid + best_ask) / 2
        band = mid * 0.0025
        bids = sum(float(q) for p, q in book["bids"] if float(p) >= mid - band)
        asks = sum(float(q) for p, q in book["asks"] if float(p) <= mid + band)
        return OrderFlow(
            buy_ratio_15m=kratio(klines),
            buy_ratio_5m=kratio(klines[-5:]),
            buy_ratio_prev10m=kratio(klines[:-5]),
            cvd_15m=cvd,
            buy_ratio_60s=buy / total if total else 0.5,
            imbalance=bids / asks if asks else 1.0,
            window=(trades[-1]["T"] - trades[0]["T"]) / 1000,
            time=time.time(),
        )

    def fetch(self, tf: Timeframe, full: bool) -> tuple[list[Candle], Quote]:
        query = urlencode({"symbol": self.symbol, "interval": tf.key, "limit": 1000 if full else 3})
        rows = self._get(f"/api/v3/klines?{query}")

        candles = [
            Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows
        ]
        return candles, Quote(candles[-1].c, time.time())


class YahooFeed(Feed):
    """Yahoo Finance chart API (used for COMEX gold futures)."""

    # timeframe -> (Yahoo interval, full range, incremental range). 4h is resampled from 60m.
    PARAMS = {
        "1m": ("1m", "5d", "1d"),
        "5m": ("5m", "15d", "1d"),
        "15m": ("15m", "20d", "1d"),
        "30m": ("30m", "40d", "1d"),
        "1h": ("60m", "90d", "5d"),
        "4h": ("60m", "1y", "5d"),
        "1d": ("1d", "5y", "1mo"),
    }

    def __init__(self, ticker: str, label: str, short: str, source: str):
        self.symbol = ticker
        self.label = label
        self.short = short
        self.source = source
        self.refresh_seconds = 15
        # Rough futures/CFD cost (spread + commission + slippage); an assumption.
        self.cost_pct = 0.0003
        self.cost_note = "asumsi biaya+slippage 0,03% pulang-pergi"
        # Yahoo keeps only ~60 days of 15m/30m bars but ~730 days of 1h bars.
        self.min_signal_seconds = 3600
        self.default_timeframe = "1h"

    def recent_window(self, tf: Timeframe) -> float:
        return {"1d": 20 * 86400, "1h": 3 * 86400, "4h": 3 * 86400}.get(tf.key, 12 * 3600)

    def fetch(self, tf: Timeframe, full: bool) -> tuple[list[Candle], Quote]:
        interval, full_range, recent_range = self.PARAMS[tf.key]
        query = urlencode(
            {"interval": interval, "range": full_range if full else recent_range, "includePrePost": "true"}
        )
        payload = None
        last_error: Exception | None = None
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
        base_seconds = 3600 if interval == "60m" else tf.seconds
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
            t = int(t)
            if base_seconds < 86400:
                # Align to the grid; Yahoo's last point is sometimes stamped "now".
                t -= t % base_seconds
            candles.append(Candle(t, float(o), float(h), float(l), float(c), float(v or 0)))
        price = meta.get("regularMarketPrice")
        quote_time = meta.get("regularMarketTime")
        if not candles:
            # Normal for an incremental request while the market is closed.
            if full or price is None:
                raise ValueError("Data candle kosong.")
            return [], Quote(float(price), float(quote_time or time.time()))
        if base_seconds < 86400:
            # Merges Yahoo's duplicate "now" point and builds 4h bars from 60m.
            candles = resample(candles, tf.seconds)
        return candles, Quote(float(price or candles[-1].c), float(quote_time or candles[-1].t))


class MarketState:
    """Candle cache for one (feed, timeframe) pair; merges incremental updates."""

    def __init__(self, feed: Feed, tf: Timeframe, cache_dir: Path):
        self.feed = feed
        self.tf = tf
        self.cache_dir = cache_dir
        self.candles: list[Candle] = []
        self.quote: Quote | None = None
        self.last_full_sync = 0.0
        self.last_latency_ms = 0
        self.last_success = 0.0
        self._cache_file = cache_dir / f"{feed.symbol.replace('=', '_')}_{tf.key}.json"
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
            or now - self.last_success > self.feed.recent_window(self.tf)
        )
        started = time.perf_counter()
        fresh, quote = self.feed.fetch(self.tf, full)
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
    YahooFeed("GC=F", "Gold Futures", "GC", "COMEX GC=F · Yahoo · bukan harga spot"),
    BinanceFeed("BTCUSDT", "Bitcoin", "BTC"),
)
