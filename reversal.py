"""Trend-reversal detector.

Flow (bullish case; the bearish case runs the same code on mirrored prices):

1. Context   - only look for a reversal when there is a trend to reverse
               (EMA20 vs EMA50 separated by > 0.3 ATR on closed candles).
2. Exhaustion (closed candles, max 70) - is the trend running out of fuel?
               RSI divergence, RSI leaving oversold, price stretched from EMA20,
               MACD histogram shrinking, hammer/engulfing candle, capitulation volume.
3. Order flow (live, max 20, Binance only) - are aggressive buyers taking over
               in the last ~60 seconds? taker-buy ratio, its change, book imbalance.
4. Sentiment (max 15) - are credible headlines turning against the trend?
5. Stage     - WATCH (score >= 30), SETUP (>= 50), TRIGGER when the live price
               breaks the high of the last 3 closed candles while in SETUP.
               INVALIDATION sits below the recent swing low.

Steps 3-5 are re-evaluated on every refresh (BTC: ~3 s), which is what makes the
alert land within seconds of the break. It cannot tell *when* a reversal will
happen; it only reports how many reversal conditions are present right now.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from data import Candle, OrderFlow, Timeframe, is_closed
from indicators import atr_series, ema_series, macd_series, rsi_series

STAGE_WATCH = 30
STAGE_SETUP = 50
PIVOT = 3


@dataclass(frozen=True)
class RFactor:
    group: str  # "Kelelahan tren" | "Order flow" | "Sentimen"
    detail: str
    points: int


@dataclass(frozen=True)
class Reversal:
    direction: int  # +1 bullish reversal of a downtrend, -1 bearish reversal of an uptrend, 0 none
    stage: str  # NONE | WATCH | SETUP | TRIGGER
    score: int  # 0..100, count of conditions present (not a calibrated probability)
    factors: list[RFactor]
    trigger: float | None
    invalidation: float | None
    note: str


def _mirror(candles: list[Candle]) -> list[Candle]:
    """Flip prices so a bearish reversal can be detected with the bullish logic."""
    return [Candle(c.t, -c.o, -c.l, -c.h, -c.c, c.v) for c in candles]


def trend_direction(closed: list[Candle]) -> int:
    closes = [c.c for c in closed]
    e20 = ema_series(closes, 20)[-1]
    e50 = ema_series(closes, 50)[-1]
    atr = atr_series([c.h for c in closed], [c.l for c in closed], closes)[-1]
    if not atr or e20 is None or e50 is None:
        return 0
    gap = (e20 - e50) / atr
    return 1 if gap > 0.3 else -1 if gap < -0.3 else 0


def _pivot_lows(lows: list[float], lookback: int) -> list[int]:
    n = len(lows)
    out = []
    for i in range(max(PIVOT, n - lookback), n - PIVOT):
        window = lows[i - PIVOT : i + PIVOT + 1]
        if lows[i] == min(window):
            out.append(i)
    return out


_BULL_TEXT = {
    "div": "Divergensi RSI: harga lower low, RSI higher low ({a:.0f}→{b:.0f})",
    "rsi_exit": "RSI keluar dari oversold ({a:.0f}→{b:.0f})",
    "rsi_ext": "RSI oversold ({a:.0f})",
    "stretch": "Harga {a:.1f}× ATR di bawah EMA20{b}",
    "macd": "Histogram MACD negatif tapi mengecil 3 candle",
    "pin": "Candle hammer (ekor bawah panjang, pembeli menolak harga rendah)",
    "engulf": "Bullish engulfing",
    "capit": "Volume kapitulasi {a:.1f}× rata-rata dengan ekor bawah",
}
_BEAR_TEXT = {
    "div": "Divergensi RSI: harga higher high, RSI lower high ({a:.0f}→{b:.0f})",
    "rsi_exit": "RSI keluar dari overbought ({a:.0f}→{b:.0f})",
    "rsi_ext": "RSI overbought ({a:.0f})",
    "stretch": "Harga {a:.1f}× ATR di atas EMA20{b}",
    "macd": "Histogram MACD positif tapi mengecil 3 candle",
    "pin": "Candle shooting star (ekor atas panjang, penjual menolak harga tinggi)",
    "engulf": "Bearish engulfing",
    "capit": "Volume klimaks {a:.1f}× rata-rata dengan ekor atas",
}


def exhaustion(closed: list[Candle], bearish: bool = False) -> tuple[int, list[RFactor], float, float]:
    """Bullish exhaustion of a downtrend (pass mirrored candles + bearish=True for the
    opposite case). Returns (points, factors, trigger, invalidation)."""
    g = "Kelelahan tren"
    txt = _BEAR_TEXT if bearish else _BULL_TEXT

    def r(v: float) -> float:  # RSI of mirrored prices is 100 - RSI
        return 100 - v if bearish else v

    closes = [c.c for c in closed]
    highs = [c.h for c in closed]
    lows = [c.l for c in closed]
    rsi = rsi_series(closes)
    ema20 = ema_series(closes, 20)[-1]
    atr = atr_series(highs, lows, closes)[-1] or 0.0
    _, _, hist = macd_series(closes)
    f: list[RFactor] = []

    # 1. Regular bullish divergence between the last two confirmed swing lows.
    piv = _pivot_lows(lows, 60)
    if len(piv) >= 2:
        p1, p2 = piv[-2], piv[-1]
        if (
            p2 - p1 >= 5
            and len(closed) - 1 - p2 <= 15
            and lows[p2] < lows[p1]
            and rsi[p1] is not None
            and rsi[p2] is not None
            and rsi[p2] > rsi[p1] + 2
        ):
            f.append(RFactor(g, txt["div"].format(a=r(rsi[p1]), b=r(rsi[p2])), 20))

    # 2. RSI leaving oversold.
    recent_rsi = [r for r in rsi[-5:] if r is not None]
    if recent_rsi:
        low_rsi = min(recent_rsi)
        if low_rsi < 30 and recent_rsi[-1] > low_rsi + 3:
            f.append(RFactor(g, txt["rsi_exit"].format(a=r(low_rsi), b=r(recent_rsi[-1])), 10))
        elif recent_rsi[-1] < 30:
            f.append(RFactor(g, txt["rsi_ext"].format(a=r(recent_rsi[-1])), 5))

    # 3. Price stretched below EMA20.
    if atr and ema20 is not None:
        stretch = (ema20 - closes[-1]) / atr
        if stretch >= 2:
            f.append(RFactor(g, txt["stretch"].format(a=stretch, b=" (terlalu jauh)"), 10))
        elif stretch >= 1.5:
            f.append(RFactor(g, txt["stretch"].format(a=stretch, b=""), 5))

    # 4. Downside momentum fading.
    h = [x for x in hist[-3:] if x is not None]
    if len(h) == 3 and h[2] < 0 and h[2] > h[1] > h[0]:
        f.append(RFactor(g, txt["macd"], 10))

    # 5. Reversal candle on the last closed bar.
    cur, prev = closed[-1], closed[-2]
    rng = cur.h - cur.l
    body = abs(cur.c - cur.o)
    lower_wick = min(cur.o, cur.c) - cur.l
    if rng > 0.6 * atr and lower_wick >= 2 * body and (cur.c - cur.l) / rng >= 0.6:
        f.append(RFactor(g, txt["pin"], 10))
    elif prev.c < prev.o and cur.c > cur.o and cur.c >= prev.o and cur.o <= prev.c:
        f.append(RFactor(g, txt["engulf"], 10))

    # 6. Capitulation volume in the last 3 closed bars.
    if len(closed) > 25:
        avg_vol = fmean(c.v for c in closed[-23:-3])
        for c in closed[-3:]:
            r = c.h - c.l
            if avg_vol > 0 and c.v >= 2.5 * avg_vol and r > 0 and (min(c.o, c.c) - c.l) / r >= 0.4:
                f.append(RFactor(g, txt["capit"].format(a=c.v / avg_vol), 10))
                break

    trigger = max(highs[-3:])
    invalidation = min(lows[-10:]) - 0.1 * atr
    return sum(x.points for x in f), f, trigger, invalidation


def flow_points(flow: OrderFlow, direction: int) -> tuple[int, list[RFactor]]:
    g = "Order flow"
    buy = flow.buy_ratio_recent if direction > 0 else 1 - flow.buy_ratio_recent
    buy_prev = flow.buy_ratio_prev if direction > 0 else 1 - flow.buy_ratio_prev
    imb = flow.imbalance if direction > 0 else (1 / flow.imbalance if flow.imbalance else 1.0)
    side = "beli" if direction > 0 else "jual"
    secs = int(flow.window / 2)
    f: list[RFactor] = []
    if buy >= 0.65:
        f.append(RFactor(g, f"Taker {side} {buy:.0%} dalam ±{secs} dtk terakhir", 12))
    elif buy >= 0.58:
        f.append(RFactor(g, f"Taker {side} {buy:.0%} dalam ±{secs} dtk terakhir", 8))
    elif buy < 0.42:
        f.append(RFactor(g, f"Tekanan {side} lemah ({buy:.0%}), tren lama masih dominan", -5))
    if buy - buy_prev >= 0.10:
        f.append(RFactor(g, f"Tekanan {side} naik {buy - buy_prev:+.0%} dibanding ±{secs} dtk sebelumnya", 3))
    if imb >= 1.5:
        f.append(RFactor(g, f"Order book condong ke {side} ({imb:.1f}×) di ±0,25% harga", 5))
    return sum(x.points for x in f), f


def news_points(score: float, shift: float, has_news: bool, direction: int) -> tuple[int, list[RFactor]]:
    g = "Sentimen"
    if not has_news:
        return 0, []
    s, d = score * direction, shift * direction
    good = "positif" if direction > 0 else "negatif"
    f: list[RFactor] = []
    if d >= 0.3:
        f.append(RFactor(g, f"Sentimen berita berbalik ({shift:+.2f} dalam 3 jam terakhir)", 8))
    if s >= 0.2:
        f.append(RFactor(g, f"Berita terbaru cenderung {good} ({score:+.2f})", 7))
    elif s <= -0.4:
        f.append(RFactor(g, f"Berita masih searah tren lama ({score:+.2f})", -5))
    return sum(x.points for x in f), f


def detect(
    candles: list[Candle],
    tf: Timeframe,
    price: float,
    flow: OrderFlow | None = None,
    news_score: float = 0.0,
    news_shift: float = 0.0,
    has_news: bool = False,
) -> Reversal:
    closed = candles if is_closed(candles[-1], tf.seconds) else candles[:-1]
    trend = trend_direction(closed)
    if trend == 0:
        return Reversal(0, "NONE", 0, [], None, None, "Tidak ada tren jelas untuk dibalik")

    direction = -trend  # a downtrend looks for a bullish reversal and vice versa
    view = closed if direction > 0 else _mirror(closed)
    pts, factors, trigger, invalidation = exhaustion(view, bearish=direction < 0)
    if direction < 0:
        trigger, invalidation = -trigger, -invalidation

    if flow is not None:
        p, f = flow_points(flow, direction)
        pts += p
        factors += f
    p, f = news_points(news_score, news_shift, has_news, direction)
    pts += p
    factors += f

    score = max(0, min(100, pts))
    broke = price > trigger if direction > 0 else price < trigger
    if score >= STAGE_SETUP and broke:
        stage = "TRIGGER"
    elif score >= STAGE_SETUP:
        stage = "SETUP"
    elif score >= STAGE_WATCH:
        stage = "WATCH"
    else:
        stage = "NONE"

    word = "naik" if direction > 0 else "turun"
    trend_word = "turun" if direction > 0 else "naik"
    notes = {
        "TRIGGER": f"Harga menembus {trigger:,.2f}: pembalikan {word} mulai terkonfirmasi",
        "SETUP": f"Kondisi pembalikan {word} lengkap, tunggu harga tembus {trigger:,.2f}",
        "WATCH": f"Tren {trend_word} mulai melemah, pantau",
        "NONE": f"Tren {trend_word} masih sehat, belum ada tanda balik arah",
    }
    return Reversal(direction, stage, score, factors, trigger, invalidation, notes[stage])
