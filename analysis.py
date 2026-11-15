"""Scoring of one candle window plus the snapshot handed to the UI.

Design (after review feedback):
- ADX(14) < 20 is a hard gate: no trend signal in a range (Wilder's threshold).
- EMA20/50, price vs EMA200, higher-timeframe trend and DMI all measure price
  direction, so they are merged into ONE trend factor instead of five votes.
- Independent evidence is added instead: room to the next support/resistance,
  volatility regime and volume.
- The tradable signal is produced by ``signals.SignalEngine`` from closed
  candles only, with hysteresis and a locked trade plan. ``live`` here is only a
  preview of the candle that is still forming.
"""

from __future__ import annotations

import time
from bisect import bisect_right
from dataclasses import dataclass, field
from statistics import fmean

from data import HTF_SECONDS, TIMEFRAMES, Candle, Feed, MarketState, OrderFlow, Timeframe, is_closed, resample
from indicators import adx, atr_series, bollinger, ema_series, macd_series, pivots, rsi_series
from reversal import Reversal, detect as detect_reversal

MIN_CANDLES = 260
CHART_CANDLES = 400
ADX_GATE = 20
ENTER = 35  # |score| needed to open a signal on a closed candle
EXIT = 20  # |score| below which an open signal is dropped (hysteresis)
STRONG = 60


@dataclass(frozen=True)
class Factor:
    name: str
    detail: str
    points: float  # signed contribution to the score


@dataclass(frozen=True)
class Evaluation:
    score: int
    signal: str  # label this window alone would give (no hysteresis)
    factors: list[Factor]
    rsi: float
    adx: float
    atr: float
    trend_htf: int  # +1 up, -1 down, 0 flat on the higher timeframe
    sideways: bool  # ADX below the gate
    htf: str
    supports: list[float]  # below price, nearest first
    resistances: list[float]  # above price, nearest first
    t: int  # open time of the last candle in the window


def label_for(score: float, gated: bool = False) -> str:
    if gated:
        return "WAIT"
    if score >= STRONG:
        return "STRONG BUY"
    if score >= ENTER:
        return "BUY"
    if score <= -STRONG:
        return "STRONG SELL"
    if score <= -ENTER:
        return "SELL"
    return "WAIT"


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def sr_levels(candles: list[Candle], price: float, lookback: int = 150) -> tuple[list[float], list[float]]:
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    ph, pl = pivots(highs, lows, k=3, lookback=lookback)
    levels = [highs[i] for i in ph] + [lows[i] for i in pl]
    supports = sorted((v for v in levels if v < price), reverse=True)
    resistances = sorted(v for v in levels if v > price)
    return supports, resistances


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
    atr_s = atr_series(highs, lows, closes)
    atr = atr_s[-1]
    adx_val, plus_di, minus_di = adx(highs, lows, closes)
    lower, _, upper = bollinger(closes)

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
    arrow = {1: "↑", -1: "↓", 0: "–"}

    # 1. Trend: one factor built from four views of the same thing (max 35).
    votes = {
        "EMA20/50": _sign(ema20 - ema50),
        "EMA200": _sign(price - ema200),
        f"tren {tf.htf_key}": trend_htf,
        "DMI": _sign(plus_di - minus_di),
    }
    trend = sum(votes.values()) / len(votes)
    agree = sum(1 for v in votes.values() if v == _sign(trend)) if trend else 0
    f.append(
        Factor(
            "Tren",
            "Tren gabungan: " + ", ".join(f"{k} {arrow[v]}" for k, v in votes.items()) + f" ({agree}/4 searah)",
            round(35 * trend, 1),
        )
    )

    # 2. Momentum: RSI around 50 and MACD histogram sign (max 20).
    h_now = hist[-1] or 0.0
    momentum = (_clip((rsi - 50) / 20) + _sign(h_now)) / 2
    f.append(Factor("Momentum", f"RSI {rsi:.0f}, histogram MACD {'+' if h_now > 0 else '−'}", round(20 * momentum, 1)))

    direction = _sign(35 * trend + 20 * momentum)

    # 3. Location: room to the next support/resistance in the signal direction (max 15).
    supports, resistances = sr_levels(candles, price)
    if direction and atr:
        ahead = resistances if direction > 0 else supports
        name = "resistance" if direction > 0 else "support"
        if not ahead:
            f.append(Factor("Ruang gerak", f"Tidak ada {name} terdekat dalam 150 candle", 10 * direction))
        else:
            room = abs(ahead[0] - price) / atr
            if room >= 2.5:
                pts, txt = 15, f"Ruang lega ke {name} {ahead[0]:,.2f} ({room:.1f}× ATR)"
            elif room >= 1.5:
                pts, txt = 8, f"Ruang cukup ke {name} {ahead[0]:,.2f} ({room:.1f}× ATR)"
            elif room < 0.75:
                pts, txt = -15, f"Mepet {name} {ahead[0]:,.2f} ({room:.1f}× ATR), rawan memantul"
            else:
                pts, txt = 0, f"Ruang sempit ke {name} {ahead[0]:,.2f} ({room:.1f}× ATR)"
            f.append(Factor("Ruang gerak", txt, pts * direction))

    # 4. Volatility regime: expanding range confirms a move, a squeeze warns of chop (max 10).
    recent_atr = [a for a in atr_s[-50:] if a is not None]
    if direction and recent_atr and atr:
        ratio = atr / fmean(recent_atr)
        if ratio >= 1.2:
            f.append(Factor("Volatilitas", f"ATR {ratio:.1f}× rata-rata 50 candle (meluas)", 10 * direction))
        elif ratio <= 0.7:
            f.append(Factor("Volatilitas", f"ATR {ratio:.1f}× rata-rata (menyempit, rawan whipsaw)", -5 * direction))
        else:
            f.append(Factor("Volatilitas", f"ATR {ratio:.1f}× rata-rata (normal)", 0))

    # 5. Volume on the last bar of the window (max 10).
    last = candles[-1]
    avg_vol = fmean(c.v for c in candles[-21:-1])
    if avg_vol > 0 and last.v >= 1.3 * avg_vol and last.c != last.o:
        # Points follow the candle colour (+ bullish, - bearish), like every other
        # factor's sign. The old code gave a high-volume green candle -10 while the
        # score leaned bearish, i.e. it read buying pressure as bearish.
        bar = _sign(last.c - last.o)
        word = "searah skor" if bar == direction else "melawan arah skor"
        f.append(
            Factor(
                "Volume",
                f"Volume {last.v / avg_vol:.1f}× rata-rata, candle {'hijau (tekanan beli)' if bar > 0 else 'merah (tekanan jual)'}, {word}",
                10 * bar * abs(direction),
            )
        )
    else:
        f.append(Factor("Volume", "Volume normal, tidak ada konfirmasi", 0))

    # 6. Over-extension against the direction.
    if direction > 0 and (rsi > 75 or price > upper):
        f.append(Factor("Jenuh beli", "RSI/Bollinger overbought, rawan koreksi", -15))
    elif direction < 0 and (rsi < 25 or price < lower):
        f.append(Factor("Jenuh jual", "RSI/Bollinger oversold, rawan pantulan", 15))

    sideways = adx_val < ADX_GATE
    f.append(
        Factor(
            "ADX",
            f"ADX {adx_val:.0f} < {ADX_GATE}: pasar sideways, sinyal tren DIMATIKAN"
            if sideways
            else f"ADX {adx_val:.0f}: {'tren kuat' if adx_val >= 25 else 'tren lemah-sedang'}",
            0,
        )
    )

    score = int(round(_clip(sum(x.points for x in f) / 100) * 100))
    return Evaluation(
        score, label_for(score, sideways), f, rsi, adx_val, atr, trend_htf, sideways, tf.htf_key,
        supports, resistances, candles[-1].t,
    )


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
    live: Evaluation  # preview including the forming candle (not tradable)
    confirmed: Evaluation  # last closed candle
    signal: object  # signals.SignalState
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


def build_snapshot(state: MarketState, flow: OrderFlow | None = None, news=None) -> Snapshot:
    from signals import engine_for  # local import: signals imports this module

    candles = state.candles
    if state.quote is None or len(candles) < MIN_CANDLES:
        raise ValueError("Data candle belum cukup untuk analisis.")
    price = state.quote.price

    # Patch the forming candle with the latest quote so the live preview is current.
    tf = state.tf
    last = candles[-1]
    if not is_closed(last, tf.seconds) and last.c != price:
        candles = candles[:-1] + [Candle(last.t, last.o, max(last.h, price), min(last.l, price), price, last.v)]
    closed = candles if is_closed(candles[-1], tf.seconds) else candles[:-1]

    engine = engine_for(state)
    signal_state = engine.update(closed)
    confirmed = engine.last_eval or evaluate(closed, tf)
    live = evaluate(candles, tf)

    # 24h change: close of the last candle that opened 24h before the quote.
    times = [c.t for c in candles]
    idx = bisect_right(times, state.quote.time - 86400) - 1
    ref = candles[max(idx, 0)].c
    change = (price / ref - 1) * 100

    closes = [c.c for c in candles]
    ema20 = ema_series(closes, 20)[-CHART_CANDLES:]
    ema50 = ema_series(closes, 50)[-CHART_CANDLES:]

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
        signal=signal_state,
        latency_ms=state.last_latency_ms,
        reversal=reversal,
        flow=flow,
    )
