"""Technical indicators computed in a single O(n) pass each.

Every ``*_series`` function returns a list the same length as its input, with
``None`` for the warm-up bars where the indicator is not defined yet.
"""

from __future__ import annotations

from statistics import fmean, pstdev
from typing import Sequence

Series = list[float | None]


def sma(values: Sequence[float], period: int) -> float:
    return fmean(values[-period:])


def ema_series(values: Sequence[float], period: int) -> Series:
    """EMA seeded with the SMA of the first ``period`` values (standard seed)."""
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    ema = fmean(values[:period])
    out[period - 1] = ema
    for i in range(period, len(values)):
        ema = (values[i] - ema) * k + ema
        out[i] = ema
    return out


def _wilder(values: Sequence[float], period: int, start: int = 0) -> Series:
    """Wilder smoothing (RMA) of ``values[start:]``, seeded with a plain average."""
    out: Series = [None] * len(values)
    if len(values) - start < period:
        return out
    avg = fmean(values[start : start + period])
    out[start + period - 1] = avg
    for i in range(start + period, len(values)):
        avg = (avg * (period - 1) + values[i]) / period
        out[i] = avg
    return out


def rsi_series(closes: Sequence[float], period: int = 14) -> Series:
    """Wilder RSI. ``out[i]`` uses the price changes up to and including bar ``i``."""
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = _wilder(gains, period, start=1)
    avg_loss = _wilder(losses, period, start=1)
    for i, (g, l) in enumerate(zip(avg_gain, avg_loss)):
        if g is None or l is None:
            continue
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def macd_series(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Series, Series, Series]:
    """Return (macd, signal, histogram) series."""
    n = len(closes)
    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    macd: Series = [
        (f - s) if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema)
    ]
    first = slow - 1
    signal_line: Series = [None] * n
    if n > first:
        signal_tail = ema_series([m for m in macd[first:]], signal)  # type: ignore[misc]
        signal_line[first:] = signal_tail
    hist: Series = [
        (m - s) if m is not None and s is not None else None for m, s in zip(macd, signal_line)
    ]
    return macd, signal_line, hist


def bollinger(closes: Sequence[float], period: int = 20, width: float = 2.0) -> tuple[float, float, float]:
    """Return (lower, middle, upper) band for the latest bar."""
    window = closes[-period:]
    middle = fmean(window)
    dev = pstdev(window)
    return middle - width * dev, middle, middle + width * dev


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        tr.append(max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev)))
    return tr


def atr_series(highs, lows, closes, period: int = 14) -> Series:
    return _wilder(true_range(highs, lows, closes), period, start=1)


def adx(highs, lows, closes, period: int = 14) -> tuple[float, float, float]:
    """Return (ADX, +DI, -DI) for the latest bar using Wilder's method."""
    n = len(closes)
    if n < period * 2 + 1:
        raise ValueError("Not enough bars for ADX")
    tr = true_range(highs, lows, closes)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    # Wilder's running sums (equivalent to RMA * period, ratios are identical).
    tr_s = _wilder(tr, period, start=1)
    pdm_s = _wilder(plus_dm, period, start=1)
    mdm_s = _wilder(minus_dm, period, start=1)

    dx: list[float] = []
    plus_di = minus_di = 0.0
    for t, p, m in zip(tr_s, pdm_s, mdm_s):
        if t is None or p is None or m is None:
            continue
        plus_di = 100 * p / t if t else 0.0
        minus_di = 100 * m / t if t else 0.0
        total = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / total if total else 0.0)

    adx_s = _wilder(dx, period)
    return float(adx_s[-1]), plus_di, minus_di  # type: ignore[arg-type]
