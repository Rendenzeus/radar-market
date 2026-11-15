"""Forward paper trading of signal v2 on live Binance prices (simulated fills).

No orders are sent anywhere. The script follows BTCUSDT (or the symbol given)
on 4h and 1D with the v2 rules from signals.run_strategy and simulates fills at
the live best bid/ask:

  - entry: only when the breakout candle is the candle that has just closed and
    it closed after the paper start; filled at the live ask (BUY) or bid (SELL)
    right after the close. A signal found late (script was off) is logged as
    "missed" and not traded.
  - stop: the locked 2 x ATR stop, checked against the live bid/ask every poll;
    if the script was off, a closed candle that crossed the stop closes the trade
    at the stop price (marked "from_candle").
  - trailing exit: when the rules exit on a candle close, filled at the live
    bid/ask right after that close.
  - fee: FEE_PER_SIDE on entry and exit (Binance spot Regular User). Spread and
    slippage come from the real bid/ask at fill time.
  - SELL trades are simulated as if shorting were possible (futures/margin);
    on spot only BUY can be executed, so results are also reported long-only.

Results in R: (exit - entry) * direction / initial risk - fees / risk, where
initial risk = distance from the actual entry fill to the stop.

Journal: ~/.market-radar/binance_paper.jsonl, append-only, one JSON line per
event (start, open, close, missed, error), never rewritten.

Usage:
  python tools/binance_paper.py              # loop, checks every POLL_SECONDS
  python tools/binance_paper.py --once
  python tools/binance_paper.py --status
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import TIMEFRAMES, BinanceFeed, Candle  # noqa: E402
from signals import TREND_EMA, run_strategy  # noqa: E402

SYMBOL = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "BTCUSDT"
TF_KEYS = ("4h", "1d")
FEE_PER_SIDE = 0.001
POLL_SECONDS = 15
JOURNAL = Path.home() / ".market-radar" / "binance_paper.jsonl"

feed = BinanceFeed(SYMBOL, SYMBOL, SYMBOL)


# ------------------------------------------------------------------ journal
def rows() -> list[dict]:
    if not JOURNAL.exists():
        return []
    return [json.loads(line) for line in JOURNAL.read_text("utf-8").splitlines() if line.strip()]


def log(event: str, **fields) -> dict:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    row = {"event": event, "logged_at": int(time.time()), "symbol": SYMBOL, **fields}
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), event, fields, flush=True)
    return row


def start_time() -> int:
    for r in rows():
        if r["event"] == "start" and r["symbol"] == SYMBOL:
            return r["t"]
    t = int(time.time())
    log("start", t=t, timeframes=list(TF_KEYS), fee_per_side=FEE_PER_SIDE, version=2)
    return t


def has(event: str, tf: str, opened_t: int) -> bool:
    return any(
        r["event"] == event and r["symbol"] == SYMBOL and r.get("tf") == tf and r.get("opened_t") == opened_t
        for r in rows()
    )


def open_positions(tf: str) -> list[dict]:
    rs = [r for r in rows() if r["symbol"] == SYMBOL and r.get("tf") == tf]
    closed = {r["opened_t"] for r in rs if r["event"] == "close"}
    return [r for r in rs if r["event"] == "open" and r["opened_t"] not in closed]


# ------------------------------------------------------------------ market
def closed_candles(tf_key: str) -> list[Candle]:
    seconds = TIMEFRAMES[tf_key].seconds
    raw = feed._get(f"/api/v3/klines?symbol={SYMBOL}&interval={tf_key}&limit=1000")
    candles = [Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in raw]
    return [c for c in candles if c.t + seconds <= time.time()]


def book() -> tuple[float, float]:
    b = feed._get(f"/api/v3/ticker/bookTicker?symbol={SYMBOL}")
    return float(b["bidPrice"]), float(b["askPrice"])


def result_r(pos: dict, exit_price: float) -> float:
    d, entry = pos["direction"], pos["fill"]
    risk = abs(entry - pos["stop"])
    fees = FEE_PER_SIDE * (entry + exit_price)
    return round(((exit_price - entry) * d - fees) / risk, 3)


def close(tf: str, pos: dict, price: float, reason: str, **extra) -> None:
    log("close", tf=tf, opened_t=pos["opened_t"], direction=pos["direction"], reason=reason,
        fill=price, entry_fill=pos["fill"], stop=pos["stop"], result_r=result_r(pos, price), **extra)


# ------------------------------------------------------------------ loop
def step(start: int) -> None:
    bid, ask = book()
    for tf in TF_KEYS:
        # 1. live stop check
        for pos in open_positions(tf):
            if pos["direction"] > 0 and bid <= pos["stop"]:
                close(tf, pos, bid, "SL")
            elif pos["direction"] < 0 and ask >= pos["stop"]:
                close(tf, pos, ask, "SL")

        candles = closed_candles(tf)
        if len(candles) <= TREND_EMA:
            continue
        last_t = candles[-1].t
        trades, plan = run_strategy(candles, 0.0)

        # 2. positions the rules no longer hold: stop crossed while we were away, or trailing exit
        for pos in open_positions(tf):
            if plan is not None and plan.opened_t == pos["opened_t"]:
                continue
            after = [c for c in candles if c.t > pos["opened_t"]]
            crossed = next((c for c in after if (c.l <= pos["stop"] if pos["direction"] > 0 else c.h >= pos["stop"])), None)
            if crossed is not None:
                close(tf, pos, pos["stop"], "SL", from_candle=crossed.t)
            else:
                rule = next((t for t in trades if t.opened_t == pos["opened_t"]), None)
                close(tf, pos, bid if pos["direction"] > 0 else ask, "TRAIL",
                      rule_exit_price=None if rule is None else rule.exit_price)

        # 3. fresh entries only
        if plan is None or plan.opened_t + TIMEFRAMES[tf].seconds < start or has("open", tf, plan.opened_t):
            continue
        if open_positions(tf):
            continue
        if plan.opened_t != last_t:
            if not has("missed", tf, plan.opened_t):
                log("missed", tf=tf, opened_t=plan.opened_t, direction=plan.direction,
                    note="sinyal ditemukan terlambat, tidak disimulasikan")
            continue
        fill = ask if plan.direction > 0 else bid
        log("open", tf=tf, opened_t=plan.opened_t, direction=plan.direction, signal_entry=plan.entry,
            fill=fill, stop=plan.stop, risk=round(abs(fill - plan.stop), 2),
            spot_executable=plan.direction > 0)


def status() -> None:
    rs = [r for r in rows() if r["symbol"] == SYMBOL]
    start = next((r["t"] for r in rs if r["event"] == "start"), None)
    if start is None:
        print("Belum mulai.")
        return
    closes = [r for r in rs if r["event"] == "close"]
    longs = [r for r in closes if r["direction"] > 0]
    n_open = sum(len(open_positions(tf)) for tf in TF_KEYS)
    print(f"{SYMBOL} mulai {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))} · {n_open} posisi terbuka · "
          f"{len(closes)} selesai, total {sum(r['result_r'] for r in closes):+.2f}R "
          f"(BUY saja {len(longs)}: {sum(r['result_r'] for r in longs):+.2f}R) · "
          f"{sum(1 for r in rs if r['event'] == 'missed')} terlewat · {sum(1 for r in rs if r['event'] == 'error')} error")
    for tf in TF_KEYS:
        for p in open_positions(tf):
            print(f"  terbuka {tf} {'BUY' if p['direction'] > 0 else 'SELL'} fill {p['fill']:,.2f} stop {p['stop']:,.2f}")
    for r in closes:
        print(f"  {r['tf']} {time.strftime('%Y-%m-%d', time.gmtime(r['opened_t']))} {'BUY' if r['direction'] > 0 else 'SELL'} {r['reason']} {r['result_r']:+.2f}R")


def main() -> None:
    if "--status" in sys.argv:
        status()
        return
    start = start_time()
    print(f"Paper trading {SYMBOL} {', '.join(TF_KEYS)} di harga live Binance (simulasi, tanpa order)", flush=True)
    if "--once" in sys.argv:
        step(start)
        return
    while True:
        try:
            step(start)
        except Exception as exc:  # network hiccup: log and keep going
            log("error", message=repr(exc))
            time.sleep(60)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
