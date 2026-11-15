"""Signal engine: weighted trend + momentum score with a chop (ADX) filter.

The old version gave one vote each to five indicators. Trend votes (SMA, MACD)
and mean-reversion votes (RSI oversold, below lower Bollinger) contradict each
other, so a STRONG signal needed conditions that almost never co-exist. Here
trend and momentum decide direction, RSI/Bollinger only flag over-extension,
and ADX scales the score down in sideways markets.
"""

from __future__ import annotations

import time
from bisect import bisect_right
from dataclasses import dataclass, field
from statistics import fmean

from data import HTF_SECONDS, TIMEFRAMES, Candle, Feed, MarketState, OrderFlow, Timeframe, is_closed, resample
from reversal import Reversal, detect as detect_reversal
from indicators import adx, atr_series, bollinger, ema_series, macd_series, rsi_series

MIN_CANDLES = 260
CHART_CANDLES = 400
STRONG = 60
NORMAL = 25


@dataclass(frozen=True)
class Factor:
    name: str
    detail: str
    points: float  # signed contribution to the score


@dataclass(frozen=True)
class Evaluation:
    score: int
    signal: str
    factors: list[Factor]
    rsi: float
    adx: float
    atr: float
    trend_htf: int  # +1 up, -1 down, 0 flat on the higher timeframe
    sideways: bool
    htf: str


@dataclass(frozen=True)
class Levels:
    stop: float
    tp1: float
    tp2: float


@dataclass
class Snapshot:
    feed: Feed
    price: float
    quote_time: float
    change_24h: float
    tf: Timeframe
    chart: list[Candle]  # last CHART_CANDLES candles, forming candle included
    ema20: list[float | None]  # aligned with ``chart``
    ema50: list[float | None]
    live: Evaluation
    confirmed: Evaluation  # evaluated on closed candles only (does not repaint)
    levels: Levels | None
    latency_ms: int
    reversal: Reversal
    flow: OrderFlow | None = None
    fetched_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return max(0.0, self.fetched_at - self.quote_time)

    @property
    def status(self) -> str:
        """LIVE, DELAYED or CLOSED based on how old the last quote is."""
        age = self.age_seconds
        if age < 120:
            return "LIVE"
        if self.feed.always_open or age < 45 * 60:
            return "DELAYED"
        return "CLOSED"


def label_for(score: float) -> str:
    if score >= STRONG:
        return "STRONG BUY"
    if score >= NORMAL:
        return "BUY"
    if score <= -STRONG:
        return "STRONG SELL"
    if score <= -NORMAL:
        return "SELL"
    return "WAIT"


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def evaluate(candles: list[Candle], tf: Timeframe | None = None) -> Evaluation:
    tf = tf or TIMEFRAMES["15m"]
    if len(candles) < MIN_CANDLES:
        raise ValueError("Data candle belum cukup untuk analisis.")

    closes = [c.c for c in candles]
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    price = closes[-1]

    ema20 = ema_series(closes, 20)[-1]
    ema50 = ema_series(closes, 50)[-1]
    ema200 = ema_series(closes, 200)[-1]
    _, _, hist = macd_series(closes)
    rsi = rsi_series(closes)[-1]
    atr = atr_series(highs, lows, closes)[-1]
    adx_val, plus_di, minus_di = adx(highs, lows, closes)
    lower, middle, upper = bollinger(closes)

    # Higher-timeframe trend: close vs a rising/falling EMA50 on resampled bars.
    higher = [c.c for c in resample(candles, HTF_SECONDS[tf.htf_key])]
    h_ema50 = ema_series(higher, 50)
    trend_htf = 0
    if len(higher) >= 55 and h_ema50[-1] is not None and h_ema50[-4] is not None:
        rising = h_ema50[-1] > h_ema50[-4]
        if higher[-1] > h_ema50[-1] and rising:
            trend_htf = 1
        elif higher[-1] < h_ema50[-1] and not rising:
            trend_htf = -1

    f: list[Factor] = []

    # --- Trend (max 45) ---------------------------------------------------
    up = ema20 > ema50
    f.append(Factor("EMA 20/50", "EMA20 di atas EMA50" if up else "EMA20 di bawah EMA50", 15 if up else -15))
    above = price > ema200
    f.append(Factor("EMA 200", "Harga di atas EMA200" if above else "Harga di bawah EMA200", 10 if above else -10))
    f.append(
        Factor(
            f"Trend {tf.htf_key}",
            {1: "Uptrend", -1: "Downtrend", 0: "Datar / belum jelas"}[trend_htf] + f" di chart {tf.htf_key}",
            15 * trend_htf,
        )
    )
    di = 5 if plus_di > minus_di else -5
    f.append(Factor("DMI", "+DI > -DI (tekanan beli)" if di > 0 else "-DI > +DI (tekanan jual)", di))

    # --- Momentum (max 35) ------------------------------------------------
    h_now, h_prev = hist[-1] or 0.0, hist[-2] or 0.0
    macd_pts = (10 if h_now > 0 else -10) + (5 if h_now > h_prev else -5)
    macd_txt = ("Histogram MACD positif" if h_now > 0 else "Histogram MACD negatif") + (
        ", menguat" if h_now > h_prev else ", melemah"
    )
    f.append(Factor("MACD", macd_txt, macd_pts))
    rsi_pts = round(_clip((rsi - 50) / 20) * 20, 1)
    f.append(Factor("RSI", f"RSI {rsi:.0f} " + ("(momentum naik)" if rsi > 55 else "(momentum turun)" if rsi < 45 else "(netral)"), rsi_pts))

    # --- Volume confirmation on the last closed bar (max 10) --------------
    closed = [c for c in candles if is_closed(c, tf.seconds)] or candles
    last = closed[-1]
    avg_vol = fmean(c.v for c in closed[-21:-1]) if len(closed) > 21 else 0
    if avg_vol > 0 and last.v >= 1.3 * avg_vol:
        vol_pts = 10 if last.c > last.o else -10 if last.c < last.o else 0
        f.append(Factor("Volume", f"Volume {last.v / avg_vol:.1f}x rata-rata, candle {'hijau' if vol_pts > 0 else 'merah'}", vol_pts))
    else:
        f.append(Factor("Volume", "Volume normal, tidak ada konfirmasi", 0))

    raw = sum(x.points for x in f)

    # --- Over-extension: fade signals that are already stretched ----------
    if raw > 0 and (rsi > 75 or price > upper):
        f.append(Factor("Jenuh beli", "RSI/Bollinger overbought, rawan koreksi", -15))
    elif raw < 0 and (rsi < 25 or price < lower):
        f.append(Factor("Jenuh jual", "RSI/Bollinger oversold, rawan pantulan", 15))
    raw = sum(x.points for x in f)

    # --- Chop filter -----------------------------------------------------
    sideways = adx_val < 20
    if sideways:
        raw *= 0.6
        f.append(Factor("ADX", f"ADX {adx_val:.0f}: pasar sideways, sinyal dilemahkan", 0))
    else:
        f.append(Factor("ADX", f"ADX {adx_val:.0f}: tren {'kuat' if adx_val >= 30 else 'cukup'}", 0))

    score = int(round(_clip(raw / 100) * 100))
    return Evaluation(score, label_for(score), f, rsi, adx_val, atr, trend_htf, sideways, tf.htf_key)


def build_snapshot(state: MarketState, flow: OrderFlow | None = None, news=None) -> Snapshot:
    candles = state.candles
    if state.quote is None or len(candles) < MIN_CANDLES:
        raise ValueError("Data candle belum cukup untuk analisis.")
    price = state.quote.price

    # Patch the forming candle with the latest quote so the live view is current.
    tf = state.tf
    last = candles[-1]
    if not is_closed(last, tf.seconds) and last.c != price:
        candles = candles[:-1] + [Candle(last.t, last.o, max(last.h, price), min(last.l, price), price, last.v)]

    live = evaluate(candles, tf)
    closed = candles if is_closed(candles[-1], tf.seconds) else candles[:-1]
    confirmed = evaluate(closed, tf)

    # 24h change: close of the last candle that opened 24h before the quote.
    times = [c.t for c in candles]
    idx = bisect_right(times, state.quote.time - 86400) - 1
    ref = candles[max(idx, 0)].c
    change = (price / ref - 1) * 100

    closes = [c.c for c in candles]
    ema20 = ema_series(closes, 20)[-CHART_CANDLES:]
    ema50 = ema_series(closes, 50)[-CHART_CANDLES:]

    levels = None
    if live.signal != "WAIT":
        direction = 1 if live.score > 0 else -1
        levels = Levels(
            stop=price - direction * 1.5 * live.atr,
            tp1=price + direction * 1.5 * live.atr,
            tp2=price + direction * 3.0 * live.atr,
        )

    reversal = detect_reversal(
        candles,
        tf,
        price,
        flow=flow,
        news_score=news.recent if news and news.recent else (news.score if news else 0.0),
        news_shift=news.shift if news else 0.0,
        has_news=bool(news and news.headlines),
    )

    return Snapshot(
        reversal=reversal,
        flow=flow,
        feed=state.feed,
        price=price,
        quote_time=state.quote.time,
        change_24h=change,
        tf=tf,
        chart=candles[-CHART_CANDLES:],
        ema20=ema20,
        ema50=ema50,
        live=live,
        confirmed=confirmed,
        levels=levels,
        latency_ms=state.last_latency_ms,
    )
