"""Dependency-free market retrieval and technical signal calculations."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from statistics import fmean, pstdev
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MarketSignal:
    symbol: str
    label: str
    price: float
    signal: str
    score: int
    rsi: float
    last_updated: str
    details: str


MARKETS = (
    ("GC=F", "XAU / Gold"),
    ("BTC-USD", "BTC / USD"),
)


def _fetch_candles(symbol: str) -> list[tuple[int, float, float]]:
    """Retrieve 15-minute Yahoo Finance candles without pandas or yfinance."""
    query = urlencode({"interval": "15m", "range": "30d"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    request = Request(url, headers={"User-Agent": "MarketRadar/1.0"})

    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError("Yahoo Finance tidak mengembalikan data market.")

    result = chart.get("result") or []
    if not result:
        raise ValueError("Data market tidak tersedia.")

    item = result[0]
    timestamps = item.get("timestamp") or []
    quote = (item.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    candles = []
    for timestamp, close, volume in zip(timestamps, closes, volumes):
        if close is not None:
            candles.append((timestamp, float(close), float(volume or 0)))
    return candles


def _sma(values: list[float], period: int) -> float:
    return fmean(values[-period:])


def _ema(values: list[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    ema = fmean(values[:period])
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
    return ema


def _rsi(values: list[float], period: int = 14) -> float:
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]
    average_gain = fmean(gains[:period])
    average_loss = fmean(losses[:period])

    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def analyze_market(symbol: str, label: str) -> MarketSignal:
    """Return a five-indicator consensus signal from 15-minute price candles."""
    candles = _fetch_candles(symbol)
    if len(candles) < 210:
        raise ValueError("Data candle belum cukup untuk menghitung indikator.")

    prices = [candle[1] for candle in candles]
    volumes = [candle[2] for candle in candles]
    latest_price = prices[-1]
    latest_rsi = _rsi(prices)
    sma_20 = _sma(prices, 20)
    sma_50 = _sma(prices, 50)
    sma_200 = _sma(prices, 200)

    macd_series = [_ema(prices[:index], 12) - _ema(prices[:index], 26) for index in range(26, len(prices) + 1)]
    macd = macd_series[-1]
    macd_signal = _ema(macd_series, 9)

    middle_band = _sma(prices, 20)
    band_deviation = pstdev(prices[-20:])
    upper_band = middle_band + 2 * band_deviation
    lower_band = middle_band - 2 * band_deviation

    votes: list[int] = []
    notes: list[str] = []

    if sma_20 > sma_50:
        votes.append(1)
        notes.append("SMA20 di atas SMA50")
    else:
        votes.append(-1)
        notes.append("SMA20 di bawah SMA50")

    if latest_price > sma_200:
        votes.append(1)
        notes.append("harga di atas SMA200")
    else:
        votes.append(-1)
        notes.append("harga di bawah SMA200")

    if latest_rsi < 35:
        votes.append(1)
        notes.append("RSI mendekati oversold")
    elif latest_rsi > 65:
        votes.append(-1)
        notes.append("RSI mendekati overbought")
    else:
        votes.append(0)
        notes.append("RSI netral")

    if macd > macd_signal:
        votes.append(1)
        notes.append("MACD bullish")
    else:
        votes.append(-1)
        notes.append("MACD bearish")

    if latest_price < lower_band:
        votes.append(1)
        notes.append("harga di bawah Bollinger bawah")
    elif latest_price > upper_band:
        votes.append(-1)
        notes.append("harga di atas Bollinger atas")
    else:
        votes.append(0)
        notes.append("Bollinger netral")

    score = sum(votes)
    average_volume = fmean(volumes[-20:])
    volume_confirmed = average_volume > 0 and volumes[-1] >= average_volume
    details = "; ".join(notes)
    if volume_confirmed:
        details += "; volume mengonfirmasi"

    if score >= 4 and volume_confirmed:
        signal = "STRONG BUY"
    elif score >= 2:
        signal = "BUY"
    elif score <= -4 and volume_confirmed:
        signal = "STRONG SELL"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "WAIT"

    last_updated = datetime.fromtimestamp(candles[-1][0], tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")
    return MarketSignal(
        symbol=symbol,
        label=label,
        price=latest_price,
        signal=signal,
        score=score,
        rsi=latest_rsi,
        last_updated=last_updated,
        details=details,
    )
