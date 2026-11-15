"""Test of signal v2 exactly as specified in docs/signal-v2.md.

Primary verdict: pooled holdout = BTC, ETH, BNB, SOL, XRP on 4h, trades opened
from 2023-01-01. Everything else is reported as a supplementary check.

Usage: python tools/backtest_trend.py
"""

from __future__ import annotations

import calendar
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import TIMEFRAMES, BinanceFeed, YahooFeed  # noqa: E402
from indicators import atr_series  # noqa: E402
from metrics import bootstrap_mean_interval, longest_losing_streak, max_drawdown, win_rate_interval  # noqa: E402
from signals import ENTRY_N, EXIT_N, STOP_ATR, TREND_EMA, run_strategy  # noqa: E402
from tools.history import CACHE, binance_history  # noqa: E402

HOLDOUT_START = calendar.timegm((2023, 1, 1, 0, 0, 0))
GOLD_SPLIT = calendar.timegm((2016, 1, 1, 0, 0, 0))
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
DAYS = 3500  # back to Binance's first candles (Aug 2017)
SIMS = 1000
CRYPTO_COST = BinanceFeed("BTCUSDT", "", "").cost_pct
GOLD_COST = YahooFeed("GC=F", "", "", "").cost_pct


def gold_daily():
    feed = YahooFeed("GC=F", "GC=F", "GC", "")
    # "max" comes back as monthly bars; 20y is the longest range that stays daily
    feed.PARAMS = dict(feed.PARAMS, **{"1d": ("1d", "20y", "1mo")})
    candles, _ = feed.fetch(TIMEFRAMES["1d"], full=True)
    return [c for c in candles if c.t + 86400 <= time.time()]


class RandomEntries:
    """Random candle + random direction, same 2 x ATR stop and 10-candle trailing exit."""

    def __init__(self, candles, cost_pct):
        self.c = candles
        self.cost = cost_pct
        self.atr = atr_series([x.h for x in candles], [x.l for x in candles], [x.c for x in candles])
        self.memo: dict = {}

    def outcome(self, i: int, d: int) -> float | None:
        key = (i, d)
        if key in self.memo:
            return self.memo[key]
        c, atr = self.c, self.atr[i]
        if not atr or i < EXIT_N:
            self.memo[key] = None
            return None
        entry, risk = c[i].c, STOP_ATR * atr
        stop = entry - d * risk
        price = c[-1].c
        for j in range(i + 1, len(c)):
            bar = c[j]
            if d > 0 and bar.l <= stop:
                price = min(bar.o, stop)
                break
            if d < 0 and bar.h >= stop:
                price = max(bar.o, stop)
                break
            if (d > 0 and bar.c < min(x.l for x in c[j - EXIT_N : j])) or (
                d < 0 and bar.c > max(x.h for x in c[j - EXIT_N : j])
            ):
                price = bar.c
                break
        r = (price - entry) * d / risk - self.cost * entry / risk
        self.memo[key] = r
        return r


def sample_means(groups, seed=0) -> list[float]:
    """groups: list of (RandomEntries, index range, trade count). Pooled mean per simulation."""
    rnd = random.Random(seed)
    means = []
    for _ in range(SIMS):
        got = []
        for sim, (lo, hi), n in groups:
            k = 0
            while k < n:
                r = sim.outcome(rnd.randrange(lo, hi), rnd.choice((1, -1)))
                if r is not None:
                    got.append(r)
                    k += 1
        means.append(statistics.fmean(got))
    return means


def summarize(title, trades, groups=None, verdict=False):
    trades = sorted(trades, key=lambda p: p.closed_t)
    net = [p.net_r for p in trades]
    print(f"\n== {title}")
    if not net:
        print("   tidak ada trade")
        return {"trades": 0}
    wins = sum(1 for r in net if r > 0)
    wlo, whi = win_rate_interval(wins, len(net))
    exp = statistics.fmean(net)
    blo, bhi = bootstrap_mean_interval(net)
    res = {
        "trades": len(net),
        "win": wins / len(net),
        "win_range": [wlo, whi],
        "gross": statistics.fmean(p.result_r for p in trades),
        "cost": statistics.fmean(p.cost_r for p in trades),
        "net": exp,
        "net_range": [blo, bhi],
        "max_dd": max_drawdown(net),
        "streak": longest_losing_streak(net),
        "total": sum(net),
    }
    print(f"   trade {len(net)}  menang {wins / len(net):.0%} ({wlo:.0%}-{whi:.0%})  total bersih {sum(net):+.1f}R")
    print(f"   kotor {res['gross']:+.3f}R  biaya {res['cost']:.3f}R  bersih {exp:+.3f}R (bootstrap 95%: {blo:+.3f} .. {bhi:+.3f})")
    print(f"   max drawdown {res['max_dd']:.1f}R  rentetan kalah terpanjang {res['streak']}")
    if groups:
        base = sample_means(groups)
        beat = sum(1 for m in base if exp > m) / len(base)
        res.update(base_mean=statistics.fmean(base), beat=beat)
        print(f"   acak ({SIMS} simulasi): rata-rata {res['base_mean']:+.3f}R, sinyal mengalahkan {beat:.1%}")
    if verdict:
        checks = {
            ">= 150 trade": len(net) >= 150,
            "expectancy bersih >= +0,15R": exp >= 0.15,
            "batas bawah bootstrap > 0": blo > 0,
            "mengalahkan >= 95% acak": res.get("beat", 0) >= 0.95,
        }
        for k, ok in checks.items():
            print(f"   [{'LULUS' if ok else 'GAGAL'}] {k}")
        res["checks"] = checks
        res["passed"] = all(checks.values())
    return res


def split(candles, trades, start, end=None):
    """Trades opened in [start, end) and the candle index range for random entries."""
    end = end or 10**12
    picked = [p for p in trades if start <= p.opened_t < end]
    idx = [i for i, c in enumerate(candles) if start <= c.t < end and i >= max(TREND_EMA, ENTRY_N)]
    return picked, (idx[0], idx[-1]) if idx else (0, 0)


def main():
    out = {"spec": "docs/signal-v2.md", "run_at": int(time.time())}
    for tf_key in ("4h", "1d"):
        hold_all, hold_long, groups = [], [], []
        for sym in SYMBOLS:
            candles = binance_history(sym, tf_key, DAYS)
            trades, _ = run_strategy(candles, CRYPTO_COST)
            longs, _ = run_strategy(candles, CRYPTO_COST, allow_short=False)
            first = time.strftime("%Y-%m-%d", time.gmtime(candles[0].t))
            print(f"{sym} {tf_key}: {len(candles)} candle sejak {first}, {len(trades)} trade total")
            sim = RandomEntries(candles, CRYPTO_COST)
            h, rng = split(candles, trades, HOLDOUT_START)
            hl, _ = split(candles, longs, HOLDOUT_START)
            hold_all += h
            hold_long += hl
            if h:
                groups.append((sim, rng, len(h)))
            out[f"{sym}_{tf_key}_holdout"] = summarize(f"{sym} {tf_key} holdout (2023+)", h)
            if sym == "BTCUSDT":
                dev, drng = split(candles, trades, 0, HOLDOUT_START)
                out[f"BTC_{tf_key}_dev"] = summarize(f"BTCUSDT {tf_key} pengembangan (sebelum 2023)", dev, [(sim, drng, len(dev))])
        primary = tf_key == "4h"
        title = f"{'HOLDOUT UTAMA' if primary else 'Cek tambahan'}: 5 kripto {tf_key}, 2023+ (long & short)"
        out[f"pooled_{tf_key}"] = summarize(title, hold_all, groups, verdict=primary)
        out[f"pooled_{tf_key}_long_only"] = summarize(f"Cek tambahan: 5 kripto {tf_key}, 2023+, long saja", hold_long)

    gold = gold_daily()
    gtrades, _ = run_strategy(gold, GOLD_COST)
    gsim = RandomEntries(gold, GOLD_COST)
    print(f"\nGC=F 1d: {len(gold)} candle sejak {time.strftime('%Y-%m-%d', time.gmtime(gold[0].t))}")
    for name, a, b in (("sebelum 2016", 0, GOLD_SPLIT), ("2016+", GOLD_SPLIT, None)):
        t, rng = split(gold, gtrades, a, b)
        out[f"gold_1d_{name}"] = summarize(f"Gold 1d {name}", t, [(gsim, rng, len(t))] if t else None)

    CACHE.mkdir(exist_ok=True)
    (CACHE / "result_trend_v2.json").write_text(json.dumps(out, indent=1, default=str), "utf-8")


if __name__ == "__main__":
    main()
