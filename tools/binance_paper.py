"""Forward test of signal v3 (v2 long-only, docs/signal-v3.md) on live Binance prices.

Simulated fills; 
no orders are sent anywhere. Follows the five holdout coins (or the symbols
given) on 4h and 1D with signals.run_strategy(allow_short=False) and simulates
fills at the live best bid/ask:

  - entry: only when the script itself saw the breakout candle close, i.e. the
    candle became the last closed candle between two consecutive passes no more
    than FRESH_SECONDS apart. Filled at the live ask (BUY) or bid (SELL). A signal
    found any later (script started after the close, network gap, laptop asleep)
    is logged as "missed"
  - stop: the locked 2 x ATR stop, checked against the live bid/ask every pass;
    if a closed candle crossed the stop while the script was not watching, the
    trade closes at the stop price (marked "from_candle")
  - trailing exit: when the rules exit on a candle close, filled at the live
    bid/ask right after that close
  - fee: FEE_PER_SIDE on entry and exit (Binance spot Regular User); spread comes
    from the real bid/ask at fill time
  - stop rule (registered before the start): when the combined v3 equity curve
    falls more than STOP_DRAWDOWN_R from its peak, no new entries are taken and
    "stop_rule" is logged; open trades still run to their exits
  - repeated identical errors are logged once

R = ((exit - entry) * direction - fees) / initial risk, initial risk measured
from the actual entry fill to the stop.

Journal: ~/.market-radar/binance_paper.jsonl, append-only, one JSON line per
event (start, open, close, missed, error, stop_rule), never rewritten. Rows carry
"version"; the earlier v2 rows (long and short) stay in the file but are ignored.

Usage:
  python tools/binance_paper.py                       # all five coins, loop
  python tools/binance_paper.py BTCUSDT,ETHUSDT       # chosen symbols
  python tools/binance_paper.py --status
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import TIMEFRAMES, BinanceFeed, Candle  # noqa: E402
from metrics import max_drawdown  # noqa: E402
from signals import TREND_EMA, run_strategy  # noqa: E402

VERSION = 3
ALLOW_SHORT = False
STOP_DRAWDOWN_R = 50.0

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
TF_KEYS = ("4h", "1d")
FEE_PER_SIDE = 0.001
POLL_SECONDS = 15
FRESH_SECONDS = 120
JOURNAL = Path.home() / ".market-radar" / "binance_paper.jsonl"

_feed = BinanceFeed("BTCUSDT", "", "")


# ------------------------------------------------------------------ journal
def rows(symbol: str | None = None) -> list[dict]:
    if not JOURNAL.exists():
        return []
    rs = [json.loads(line) for line in JOURNAL.read_text("utf-8").splitlines() if line.strip()]
    rs = [r for r in rs if r.get("version", 2) == VERSION]
    return [r for r in rs if symbol is None or r.get("symbol") == symbol]


def log(event: str, symbol: str, **fields) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    row = {"event": event, "logged_at": int(time.time()), "symbol": symbol, "version": VERSION, **fields}
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), symbol, event, fields, flush=True)


_last_error: dict[tuple, str] = {}


def log_error(key: tuple, symbol: str, message: str) -> None:
    if _last_error.get(key) == message:
        return
    _last_error[key] = message
    log("error", symbol, message=message)


def start_time(symbol: str) -> int:
    for r in rows(symbol):
        if r["event"] == "start":
            return r["t"]
    t = int(time.time())
    log("start", symbol, t=t, timeframes=list(TF_KEYS), fee_per_side=FEE_PER_SIDE, allow_short=ALLOW_SHORT)
    return t


def has(symbol: str, event: str, tf: str, opened_t: int) -> bool:
    return any(r["event"] == event and r.get("tf") == tf and r.get("opened_t") == opened_t for r in rows(symbol))


def open_positions(symbol: str, tf: str) -> list[dict]:
    rs = [r for r in rows(symbol) if r.get("tf") == tf]
    closed = {r["opened_t"] for r in rs if r["event"] == "close"}
    return [r for r in rs if r["event"] == "open" and r["opened_t"] not in closed]


# ------------------------------------------------------------------ market
def closed_candles(symbol: str, tf_key: str) -> list[Candle]:
    seconds = TIMEFRAMES[tf_key].seconds
    raw = _feed._get(f"/api/v3/klines?symbol={symbol}&interval={tf_key}&limit=1000")
    candles = [Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in raw]
    return [c for c in candles if c.t + seconds <= time.time()]


def book(symbol: str) -> tuple[float, float]:
    b = _feed._get(f"/api/v3/ticker/bookTicker?symbol={symbol}")
    return float(b["bidPrice"]), float(b["askPrice"])


def result_r(pos: dict, exit_price: float) -> float:
    d, entry = pos["direction"], pos["fill"]
    risk = abs(entry - pos["stop"])
    fees = FEE_PER_SIDE * (entry + exit_price)
    return round(((exit_price - entry) * d - fees) / risk, 3)


def close(symbol: str, tf: str, pos: dict, price: float, reason: str, **extra) -> None:
    log("close", symbol, tf=tf, opened_t=pos["opened_t"], direction=pos["direction"], reason=reason,
        fill=price, entry_fill=pos["fill"], stop=pos["stop"], result_r=result_r(pos, price), **extra)


# ------------------------------------------------------------------ loop
def step_symbol(symbol: str, start: int, state: dict, contiguous: bool) -> None:
    bid, ask = book(symbol)
    for tf in TF_KEYS:
        # 1. live stop check
        for pos in open_positions(symbol, tf):
            if pos["direction"] > 0 and bid <= pos["stop"]:
                close(symbol, tf, pos, bid, "SL")
            elif pos["direction"] < 0 and ask >= pos["stop"]:
                close(symbol, tf, pos, ask, "SL")

        candles = closed_candles(symbol, tf)
        if not candles:
            continue
        last_t = candles[-1].t
        previous_last = state.get((symbol, tf))
        state[(symbol, tf)] = last_t
        if len(candles) <= TREND_EMA:
            continue
        trades, plan = run_strategy(candles, 0.0, allow_short=ALLOW_SHORT)

        # 2. positions the rules no longer hold: stop crossed while unwatched, or trailing exit
        for pos in open_positions(symbol, tf):
            if plan is not None and plan.opened_t == pos["opened_t"]:
                continue
            after = [c for c in candles if c.t > pos["opened_t"]]
            crossed = next((c for c in after if (c.l <= pos["stop"] if pos["direction"] > 0 else c.h >= pos["stop"])), None)
            if crossed is not None:
                close(symbol, tf, pos, pos["stop"], "SL", from_candle=crossed.t)
            else:
                rule = next((t for t in trades if t.opened_t == pos["opened_t"]), None)
                close(symbol, tf, pos, bid if pos["direction"] > 0 else ask, "TRAIL",
                      rule_exit_price=None if rule is None else rule.exit_price)

        # 3. entries the script saw happen (none once the stop rule has fired)
        if state.get("stopped") or plan is None or plan.opened_t + TIMEFRAMES[tf].seconds < start:
            continue
        if has(symbol, "open", tf, plan.opened_t) or has(symbol, "missed", tf, plan.opened_t) or open_positions(symbol, tf):
            continue
        seen_live = (
            plan.opened_t == last_t
            and previous_last is not None
            and previous_last != last_t
            and contiguous
            and time.time() - (last_t + TIMEFRAMES[tf].seconds) <= FRESH_SECONDS
        )
        if not seen_live:
            log("missed", symbol, tf=tf, opened_t=plan.opened_t, direction=plan.direction,
                note="close candle tidak terlihat langsung oleh skrip (baru jalan, terputus, atau laptop tidur)")
            continue
        fill = ask if plan.direction > 0 else bid
        log("open", symbol, tf=tf, opened_t=plan.opened_t, direction=plan.direction, signal_entry=plan.entry,
            fill=fill, stop=plan.stop, risk=abs(fill - plan.stop), spot_executable=plan.direction > 0)


def drawdown_r() -> float:
    """Peak-to-trough fall of the combined v3 equity curve (all symbols and timeframes)."""
    closes = sorted((r for r in rows() if r["event"] == "close"), key=lambda r: r["logged_at"])
    return max_drawdown([r["result_r"] for r in closes])


def check_stop_rule(state: dict) -> None:
    if any(r["event"] == "stop_rule" for r in rows()):
        state["stopped"] = True
        return
    dd = drawdown_r()
    if dd > STOP_DRAWDOWN_R:
        state["stopped"] = True
        log("stop_rule", "ALL", drawdown_r=round(dd, 2), limit_r=STOP_DRAWDOWN_R,
            note="drawdown lewat batas: tidak ada entry baru, posisi terbuka dibiarkan selesai")


def step(symbols, starts: dict, state: dict) -> None:
    check_stop_rule(state)
    now = time.time()
    contiguous = now - state.get("prev_pass", 0.0) <= FRESH_SECONDS
    state["prev_pass"] = now
    for symbol in symbols:
        try:
            step_symbol(symbol, starts[symbol], state, contiguous)
            _last_error.pop((symbol,), None)
        except Exception as exc:  # network hiccup: log once, forget what we saw so no late entry slips in
            log_error((symbol,), symbol, repr(exc))
            for tf in TF_KEYS:
                state.pop((symbol, tf), None)


def status(symbols) -> None:
    for symbol in symbols:
        rs = rows(symbol)
        start = next((r["t"] for r in rs if r["event"] == "start"), None)
        if start is None:
            print(f"{symbol}: belum mulai.")
            continue
        closes = [r for r in rs if r["event"] == "close"]
        n_open = sum(len(open_positions(symbol, tf)) for tf in TF_KEYS)
        print(f"{symbol} mulai {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))} · {n_open} terbuka · "
              f"{len(closes)} selesai, total {sum(r['result_r'] for r in closes):+.2f}R · "
              f"{sum(1 for r in rs if r['event'] == 'missed')} terlewat · {sum(1 for r in rs if r['event'] == 'error')} error")
        for tf in TF_KEYS:
            for p in open_positions(symbol, tf):
                print(f"   terbuka {tf} {'BUY' if p['direction'] > 0 else 'SELL'} fill {p['fill']:,.4f} stop {p['stop']:,.4f}")
        for r in closes:
            print(f"   {r['tf']} {time.strftime('%Y-%m-%d', time.gmtime(r['opened_t']))} "
                  f"{'BUY' if r['direction'] > 0 else 'SELL'} {r['reason']} {r['result_r']:+.2f}R")


def summary() -> None:
    allc = [r for r in rows() if r["event"] == "close"]
    four = [r for r in allc if r["tf"] == "4h"]
    missed = sum(1 for r in rows() if r["event"] == "missed")
    opened = sum(1 for r in rows() if r["event"] == "open")
    stopped = any(r["event"] == "stop_rule" for r in rows())
    print(f"v3 gabungan: {len(four)}/150 trade 4h selesai, {len(allc)} total, {sum(r['result_r'] for r in allc):+.2f}R · "
          f"drawdown {drawdown_r():.1f}R dari batas {STOP_DRAWDOWN_R:.0f}R{' (BERHENTI)' if stopped else ''} · "
          f"missed {missed}/{missed + opened} sinyal")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    symbols = tuple(args[0].split(",")) if args else DEFAULT_SYMBOLS
    if "--status" in sys.argv:
        status(symbols)
        summary()
        return
    starts = {s: start_time(s) for s in symbols}
    print(f"Paper trading {', '.join(symbols)} di {', '.join(TF_KEYS)}, harga live Binance (simulasi, tanpa order)", flush=True)
    state: dict = {}
    while True:
        step(symbols, starts, state)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
