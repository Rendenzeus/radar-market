"""Backtest the candle-based part of the reversal detector on Binance history.

Order flow and news have no history here, so this measures only the
exhaustion layer. An event fires when a trend is present, the exhaustion
score reaches the threshold and the next candle breaks the trigger level.
Outcome: from the trigger price, does price move +1.5 ATR in the reversal
direction before it moves 1 ATR against it (within 20 candles)?
Baseline: every trigger-level break inside a trend, regardless of score.

Usage: python tools/backtest_reversal.py [BTCUSDT] [5m,15m,1h]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import TIMEFRAMES, BinanceFeed, Candle  # noqa: E402
from indicators import atr_series  # noqa: E402
from reversal import _mirror, exhaustion, trend_direction  # noqa: E402

HORIZON = 20
TARGET_ATR = 1.5
STOP_ATR = 1.0


def fetch_history(feed: BinanceFeed, tf_key: str, pages: int = 5) -> list[Candle]:
    rows, end = [], None
    for _ in range(pages):
        q = f"/api/v3/klines?symbol={feed.symbol}&interval={tf_key}&limit=1000" + (f"&endTime={end}" if end else "")
        batch = feed._get(q)
        if not batch:
            break
        rows = batch + rows
        end = batch[0][0] - 1
    return [Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def outcome(candles, i, direction, entry, atr) -> int | None:
    """+1 target first, -1 stop first, None neither within horizon."""
    target = entry + direction * TARGET_ATR * atr
    stop = entry - direction * STOP_ATR * atr
    for c in candles[i : i + HORIZON]:
        hit_stop = c.l <= stop if direction > 0 else c.h >= stop
        hit_target = c.h >= target if direction > 0 else c.l <= target
        if hit_stop:
            return -1  # conservative: stop wins when both hit in one candle
        if hit_target:
            return 1
    return None


def run(symbol: str, tf_key: str, threshold: int) -> None:
    feed = BinanceFeed(symbol, symbol, symbol)
    candles = fetch_history(feed, tf_key)
    atr = atr_series([c.h for c in candles], [c.l for c in candles], [c.c for c in candles])
    stats = {"signal": [0, 0, 0], "baseline": [0, 0, 0]}  # wins, losses, open
    last_event = -10
    for i in range(300, len(candles) - HORIZON - 1):
        window = candles[i - 299 : i + 1]  # closed candles up to i
        trend = trend_direction(window)
        if trend == 0:
            continue
        direction = -trend
        view = window if direction > 0 else _mirror(window)
        score, _, trigger, _ = exhaustion(view, bearish=direction < 0)
        if direction < 0:
            trigger = -trigger
        nxt = candles[i + 1]
        broke = nxt.h > trigger if direction > 0 else nxt.l < trigger
        if not broke or atr[i] is None:
            continue
        res = outcome(candles, i + 2, direction, trigger, atr[i])
        idx = {1: 0, -1: 1, None: 2}[res]
        stats["baseline"][idx] += 1
        if score >= threshold and i - last_event >= 5:
            stats["signal"][idx] += 1
            last_event = i

    print(f"{symbol} {tf_key}  {len(candles)} candles  threshold {threshold}")
    for name, (w, l, o) in stats.items():
        decided = w + l
        rate = w / decided if decided else 0
        print(f"  {name:8s} events {w + l + o:4d}  win {w:4d}  loss {l:4d}  open {o:3d}  win-rate {rate:.0%}")
    print(f"  (break-even win-rate for 1.5:1 reward/risk = {STOP_ATR / (STOP_ATR + TARGET_ATR):.0%})")


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tfs = sys.argv[2].split(",") if len(sys.argv) > 2 else ["5m", "15m", "1h"]
    for tf in tfs:
        assert tf in TIMEFRAMES
        for th in (30, 40):
            run(sym, tf, th)
