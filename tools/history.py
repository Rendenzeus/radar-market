"""Download and cache long Binance kline history for the test tools."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import astuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import TIMEFRAMES, BinanceFeed, Candle  # noqa: E402

CACHE = Path(__file__).resolve().parent / ".history"


def binance_history(symbol: str, tf_key: str, days: int) -> list[Candle]:
    path = CACHE / f"{symbol}_{tf_key}_{days}d.json"
    if path.exists():
        return [Candle(*r) for r in json.loads(path.read_text("utf-8"))]
    feed = BinanceFeed(symbol, symbol, symbol)
    step = TIMEFRAMES[tf_key].seconds
    start = (int(time.time()) - days * 86400) * 1000
    rows: list = []
    while True:
        batch = feed._get(f"/api/v3/klines?symbol={symbol}&interval={tf_key}&limit=1000&startTime={start}")
        if not batch:
            break
        rows += batch
        start = batch[-1][0] + step * 1000
        if len(batch) < 1000:
            break
    candles = [Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
    candles = [c for c in candles if c.t + step <= time.time()]  # closed only
    CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps([astuple(c) for c in candles]), "utf-8")
    return candles
