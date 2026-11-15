"""Signal v2, rules unchanged, on XAUUSD history from a MetaTrader 5 terminal.

This data (spot gold, 4h since 2004) was never used to design or test the signal,
so it is a fresh out-of-sample check. It is NOT part of the pre-registered spec
in docs/signal-v2.md; the same four pass criteria are applied anyway.

Costs: 0.03% round trip (the app's gold assumption; recent broker spreads are
lower) plus overnight swap from the terminal's symbol settings, charged per night
held (triple on the broker's rollover day). The random baseline uses the same
round-trip cost but no swap, so the comparison is made before swap.

Needs a running, logged-in MT5 terminal and `pip install MetaTrader5` (Windows).
Usage: python tools/mt5_gold.py [XAUUSD]
"""

from __future__ import annotations

import calendar
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import MetaTrader5 as mt5  # noqa: E402

from data import Candle  # noqa: E402
from metrics import bootstrap_mean_interval, longest_losing_streak, max_drawdown, win_rate_interval  # noqa: E402
from signals import TREND_EMA, run_strategy  # noqa: E402
from tools.backtest_trend import RandomEntries, sample_means  # noqa: E402

COST_PCT = 0.0003


def load(symbol: str, timeframe: int, count: int) -> list[Candle]:
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)  # position 0 is the forming candle
    if rates is None or not len(rates):
        raise SystemExit(f"Tidak ada data {symbol}: {mt5.last_error()}")
    return [Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["tick_volume"])) for r in rates]


def swap_r(plan, info) -> float:
    """Overnight swap in R for one trade (swap mode 1 = points per night)."""
    if info.swap_mode != mt5.SYMBOL_SWAP_MODE_POINTS:
        return 0.0
    per_night = (info.swap_long if plan.direction > 0 else info.swap_short) * info.point  # price units, signed
    nights = 0
    day = plan.opened_t // 86400
    while day < plan.closed_t // 86400:
        day += 1
        weekday = time.gmtime(day * 86400).tm_wday  # 0 = Monday
        if weekday < 5:
            nights += 3 if weekday == info.swap_rollover3days else 1
    return per_night * nights / plan.risk  # negative = cost


def report(title, trades, info, groups=None, verdict=False):
    print(f"\n== {title}")
    if not trades:
        print("   tidak ada trade")
        return
    trades = sorted(trades, key=lambda p: p.closed_t)
    before_swap = [p.net_r for p in trades]
    net = [p.net_r + swap_r(p, info) for p in trades]
    wins = sum(1 for r in net if r > 0)
    lo, hi = win_rate_interval(wins, len(net))
    exp = statistics.fmean(net)
    blo, bhi = bootstrap_mean_interval(net)
    print(f"   trade {len(net)}  menang {wins / len(net):.0%} ({lo:.0%}-{hi:.0%})  total {sum(net):+.1f}R")
    print(
        f"   kotor {statistics.fmean(p.result_r for p in trades):+.3f}R  biaya {statistics.fmean(p.cost_r for p in trades):.3f}R  "
        f"swap {statistics.fmean(net) - statistics.fmean(before_swap):+.3f}R  bersih {exp:+.3f}R (95%: {blo:+.3f} .. {bhi:+.3f})"
    )
    print(f"   max drawdown {max_drawdown(net):.1f}R  rentetan kalah terpanjang {longest_losing_streak(net)}")
    beat = None
    if groups:
        base = sample_means(groups)
        mean_before = statistics.fmean(before_swap)
        beat = sum(1 for m in base if mean_before > m) / len(base)
        print(f"   acak (1.000 simulasi, sebelum swap): {statistics.fmean(base):+.3f}R vs sinyal {mean_before:+.3f}R, mengalahkan {beat:.1%}")
    if verdict:
        for name, ok in (
            (">= 150 trade", len(net) >= 150),
            ("expectancy bersih >= +0,15R", exp >= 0.15),
            ("batas bawah bootstrap > 0", blo > 0),
            ("mengalahkan >= 95% acak", (beat or 0) >= 0.95),
        ):
            print(f"   [{'LULUS' if ok else 'GAGAL'}] {name}")
    by_year: dict = {}
    for p, r in zip(trades, net):
        by_year.setdefault(time.gmtime(p.opened_t).tm_year, []).append(r)
    if len(by_year) > 3:
        print("   per tahun: " + ", ".join(f"{y} {statistics.fmean(v):+.2f}R ({len(v)})" for y, v in sorted(by_year.items())))
        months: dict = {}
        for p, r in zip(trades, net):
            months.setdefault(time.strftime("%Y-%m", time.gmtime(p.opened_t)), []).append(r)
        blocks = list(months.values())
        rnd = random.Random(1)
        means = sorted(
            statistics.fmean([x for b in (rnd.choice(blocks) for _ in blocks) for x in b]) for _ in range(5000)
        )
        print(f"   bootstrap per bulan 95%: {means[125]:+.3f} .. {means[4874]:+.3f}")


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"
    if not mt5.initialize():
        raise SystemExit(f"MT5 tidak bisa dihubungi: {mt5.last_error()}")
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    print(f"{symbol} dari {mt5.account_info().server}; swap long {info.swap_long} / short {info.swap_short} poin per malam")
    for tf, name, count in ((mt5.TIMEFRAME_H4, "4h", 99999), (mt5.TIMEFRAME_D1, "1d", 99999)):
        candles = load(symbol, tf, count)
        print(f"\n##### {name}: {len(candles)} candle sejak {time.strftime('%Y-%m-%d', time.gmtime(candles[0].t))}")
        trades, _ = run_strategy(candles, COST_PCT)
        longs, _ = run_strategy(candles, COST_PCT, allow_short=False)
        sim = RandomEntries(candles, COST_PCT)
        lo = max(TREND_EMA, 20)
        report(f"{symbol} {name}, seluruh periode", trades, info, [(sim, (lo, len(candles) - 1), len(trades))], verdict=True)
        for a, b in ((2004, 2016), (2016, 2023), (2023, 2100)):
            ta, tb = calendar.timegm((a, 1, 1, 0, 0, 0)), calendar.timegm((b, 1, 1, 0, 0, 0))
            report(f"{symbol} {name}, {a}–{min(b - 1, 2026)}", [p for p in trades if ta <= p.opened_t < tb], info)
        report(f"{symbol} {name}, long saja", longs, info)
    mt5.shutdown()


if __name__ == "__main__":
    main()
