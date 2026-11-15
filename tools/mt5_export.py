"""Export candles from a MetaTrader 5 terminal to CSV, for tests that run without MT5.

Writes tools/.history/<SYMBOL>_<TF>.csv with columns time,open,high,low,close,volume,spread.
`time` is the candle open in the broker's server time (MetaQuotes-Demo: UTC+2, UTC+3 in
northern-hemisphere summer), as epoch seconds. Only closed candles are written.

Usage: python tools/mt5_export.py [XAUUSD] [H4,D1]
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import MetaTrader5 as mt5

OUT = Path(__file__).resolve().parent / ".history"
FRAMES = {"M15": "TIMEFRAME_M15", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1"}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
    frames = sys.argv[2].split(",") if len(sys.argv) > 2 else ["H4", "D1"]
    if not mt5.initialize():
        raise SystemExit(f"MT5 tidak bisa dihubungi: {mt5.last_error()}")
    mt5.symbol_select(symbol, True)
    OUT.mkdir(exist_ok=True)
    for name in frames:
        rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, FRAMES[name]), 0, 99999)
        seconds = {"M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}[name]
        last_quote = mt5.symbol_info_tick(symbol).time
        if rates is not None and len(rates) and int(rates[-1]["time"]) + seconds > last_quote + 300:
            rates = rates[:-1]  # still forming (5 min grace for the last quote before a session close)
        if rates is None or not len(rates):
            print(f"{symbol} {name}: tidak ada data ({mt5.last_error()})")
            continue
        path = OUT / f"{symbol}_{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "open", "high", "low", "close", "volume", "spread"])
            for r in rates:
                w.writerow([int(r["time"]), r["open"], r["high"], r["low"], r["close"], int(r["tick_volume"]), int(r["spread"])])
        first = time.strftime("%Y-%m-%d", time.gmtime(int(rates[0]["time"])))
        last = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(rates[-1]["time"])))
        print(f"{symbol} {name}: {len(rates)} candle, {first} .. {last} -> {path}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
