"""Two-period test of the main signal with costs, frozen parameters.

Period A (older half) and period B (newer half, the out-of-sample period) are
reported separately. Parameters are NOT tuned here; whatever is in analysis.py /
signals.py at the time of the run is what gets tested.

For each period:
  - trades, win rate with exact 95% range
  - expectancy gross and net of costs, bootstrap 95% range of the net mean
  - max drawdown and longest losing streak (net R)
  - random baseline: 1,000 simulations with the same number of trades, random
    entry candle and direction, same plan builder, same costs and same stop/target
    management, holding period drawn from the signal trades' own holding times;
    reports the share of simulations the signal beats

Pass rule for period B (from the review): >= 150 trades, net expectancy >= +0.15R
with bootstrap lower bound > 0, and beating >= 95% of the random simulations.

Usage:
  python tools/backtest_walkforward.py BTCUSDT 15m 730     (Binance, days of history)
  python tools/backtest_walkforward.py GC=F 1h               (Yahoo, ~730 days max)
"""

from __future__ import annotations

import json
from dataclasses import astuple
import random
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import signals  # noqa: E402
from analysis import MIN_CANDLES  # noqa: E402
from data import TIMEFRAMES, BinanceFeed, Candle, YahooFeed  # noqa: E402
from metrics import bootstrap_mean_interval, longest_losing_streak, max_drawdown, win_rate_interval  # noqa: E402

CACHE = Path(__file__).resolve().parent / ".history"
SIMS = 1000


# ------------------------------------------------------------------ data
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


def yahoo_history(ticker: str, tf_key: str) -> list[Candle]:
    feed = YahooFeed(ticker, ticker, ticker, "")
    feed.PARAMS = dict(feed.PARAMS, **{"1h": ("60m", "730d", "5d"), "4h": ("60m", "730d", "5d")})
    candles, _ = feed.fetch(TIMEFRAMES[tf_key], full=True)
    step = TIMEFRAMES[tf_key].seconds
    return [c for c in candles if c.t + step <= time.time()]


# ------------------------------------------------------------------ run
def replay(candles: list[Candle], tf_key: str, cost_pct: float):
    tf = TIMEFRAMES[tf_key]
    trades: list = []
    evs: dict[int, SimpleNamespace] = {}
    original_eval, original_record = signals.evaluate, signals.SignalEngine._record

    def eval_and_keep(window, tf_):
        ev = original_eval(window, tf_)
        evs[window[-1].t] = SimpleNamespace(atr=ev.atr, supports=ev.supports, resistances=ev.resistances)
        return ev

    signals.evaluate = eval_and_keep
    signals.SignalEngine._record = lambda self, plan: trades.append(plan)
    try:
        engine = signals.SignalEngine(tf, cost_pct=cost_pct)
        engine.state.last_t = candles[MIN_CANDLES - 2].t
        engine.update(candles)
    finally:
        signals.evaluate, signals.SignalEngine._record = original_eval, original_record
    return trades, evs


def baseline(candles, idx_range, evs, n_trades, holds, cost_pct, seed=0):
    """Mean net R of SIMS random strategies with the same trade count."""
    rnd = random.Random(seed)
    lo, hi = idx_range
    plans: dict = {}
    outcomes: dict = {}

    def plan_at(i, d):
        key = (i, d)
        if key not in plans:
            ev = evs.get(candles[i].t)
            plans[key] = None if ev is None else signals.build_plan(candles[max(0, i - 60) : i + 1], ev, d, cost_pct)[0]
        return plans[key]

    def outcome(i, d, hold):
        key = (i, d, hold)
        if key not in outcomes:
            plan = plan_at(i, d)
            for bar in candles[i + 1 : i + 1 + hold]:
                plan = signals.manage(plan, bar)
                if plan.status == "CLOSED":
                    break
            if plan.status != "CLOSED":
                plan = signals.close_on_signal(plan, candles[min(i + hold, len(candles) - 1)])
            outcomes[key] = plan.net_r
        return outcomes[key]

    means = []
    for _ in range(SIMS):
        got = []
        while len(got) < n_trades:
            i = rnd.randrange(lo, hi - 1)
            d = rnd.choice((1, -1))
            if plan_at(i, d) is None:
                continue
            got.append(outcome(i, d, rnd.choice(holds)))
        means.append(statistics.fmean(got))
    return means


def report(name, candles, trades, evs, cost_pct, idx_range, out_of_sample):
    index = {c.t: i for i, c in enumerate(candles)}
    a, b = idx_range
    t0, t1 = candles[a].t, candles[b - 1].t
    period = [p for p in trades if t0 <= p.opened_t <= t1]
    day = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t))  # noqa: E731
    print(f"\n== {name}: {day(t0)} .. {day(t1)} ({b - a} candle)")
    if not period:
        print("   tidak ada trade")
        return {}
    net = [p.net_r for p in period]
    gross = [p.result_r for p in period]
    wins = sum(1 for r in net if r > 0)
    wlo, whi = win_rate_interval(wins, len(net))
    exp = statistics.fmean(net)
    blo, bhi = bootstrap_mean_interval(net)
    holds = [max(1, index[p.closed_t] - index[p.opened_t]) for p in period]
    base = baseline(candles, (max(a, MIN_CANDLES), b), evs, len(net), holds, cost_pct)
    beat = sum(1 for m in base if exp > m) / len(base)
    reasons: dict = {}
    for p in period:
        reasons[p.exit_reason] = reasons.get(p.exit_reason, 0) + 1
    res = {
        "period": f"{day(t0)} .. {day(t1)}",
        "trades": len(net),
        "win": wins / len(net),
        "win_lo": wlo,
        "win_hi": whi,
        "gross": statistics.fmean(gross),
        "cost": statistics.fmean(p.cost_r for p in period),
        "net": exp,
        "net_lo": blo,
        "net_hi": bhi,
        "max_dd": max_drawdown(net),
        "streak": longest_losing_streak(net),
        "base_mean": statistics.fmean(base),
        "beat": beat,
        "exits": reasons,
    }
    print(f"   trade {len(net)}  menang {wins / len(net):.0%} (95%: {wlo:.0%}-{whi:.0%})  exit {reasons}")
    print(f"   expectancy kotor {res['gross']:+.3f}R  biaya rata-rata {res['cost']:.3f}R  bersih {exp:+.3f}R (bootstrap 95%: {blo:+.3f} .. {bhi:+.3f})")
    print(f"   max drawdown {res['max_dd']:.1f}R  rentetan kalah terpanjang {res['streak']}  total bersih {sum(net):+.1f}R")
    print(f"   acak (1.000 simulasi): rata-rata {res['base_mean']:+.3f}R, sinyal mengalahkan {beat:.1%} simulasi")
    if out_of_sample:
        checks = {
            ">= 150 trade": len(net) >= 150,
            "expectancy bersih >= +0,15R": exp >= 0.15,
            "batas bawah bootstrap > 0": blo > 0,
            "mengalahkan >= 95% acak": beat >= 0.95,
        }
        for k, ok in checks.items():
            print(f"   [{'LULUS' if ok else 'GAGAL'}] {k}")
        res["checks"] = checks
    return res


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tf_key = sys.argv[2] if len(sys.argv) > 2 else "15m"
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 730
    if "=" in symbol or symbol.startswith("^"):
        candles = yahoo_history(symbol, tf_key)
        cost_pct = YahooFeed(symbol, "", "", "").cost_pct
    else:
        candles = binance_history(symbol, tf_key, days)
        cost_pct = BinanceFeed(symbol, "", "").cost_pct
    print(f"{symbol} {tf_key}: {len(candles)} candle closed, biaya pulang-pergi {cost_pct:.2%}")
    started = time.time()
    trades, evs = replay(candles, tf_key, cost_pct)
    print(f"replay {time.time() - started:.0f} dtk, {len(trades)} trade")
    half = len(candles) // 2
    out = {
        "symbol": symbol,
        "tf": tf_key,
        "candles": len(candles),
        "cost_pct": cost_pct,
        "A": report("Periode A (lama)", candles, trades, evs, cost_pct, (0, half), False),
        "B": report("Periode B (baru, out-of-sample)", candles, trades, evs, cost_pct, (half, len(candles)), True),
    }
    CACHE.mkdir(exist_ok=True)
    (CACHE / f"result_{symbol}_{tf_key}.json").write_text(json.dumps(out, indent=1, default=str), "utf-8")


if __name__ == "__main__":
    main()
