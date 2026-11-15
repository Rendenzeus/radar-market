"""Backtest the main BUY/SELL signal exactly as the app runs it.

The SignalEngine is replayed over Binance history candle by candle (closed
candles only, hysteresis, locked structural plan, half off at TP1, stop to
entry, signal exit). Results are in R (multiples of the initial risk).

Baseline: the same number of entries at random candles with a random
direction, the same plan builder (stop beyond structure, R:R >= 1.5 filter)
and the same stop/target management, closed after the median holding time
of the signal trades. Averaged over several seeds.

Usage: python tools/backtest_signal.py [BTCUSDT] [15m,1h] [pages of 1000 candles]
"""

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import MIN_CANDLES, evaluate  # noqa: E402
from data import TIMEFRAMES, BinanceFeed  # noqa: E402
from signals import WINDOW, SignalEngine, build_plan, close_on_signal, manage  # noqa: E402
from tools.backtest_reversal import fetch_history  # noqa: E402


def summarize(name: str, results: list[float]) -> None:
    if not results:
        print(f"  {name:22s} no trades")
        return
    wins = sum(1 for r in results if r > 0)
    exp = statistics.fmean(results)
    sd = statistics.pstdev(results) if len(results) > 1 else 0.0
    se = sd / len(results) ** 0.5 if results else 0.0
    print(
        f"  {name:22s} trades {len(results):4d}  win {wins / len(results):5.0%}  "
        f"expectancy {exp:+.2f}R  (±{1.96 * se:.2f}R 95%)  total {sum(results):+.1f}R"
    )


def run(symbol: str, tf_key: str, pages: int) -> None:
    tf = TIMEFRAMES[tf_key]
    feed = BinanceFeed(symbol, symbol, symbol)
    candles = fetch_history(feed, tf_key, pages)
    engine = SignalEngine(tf)
    # replay everything: pretend the engine starts at the first usable candle
    engine.state.last_t = candles[MIN_CANDLES - 2].t
    engine.update(candles)
    trades = engine.state.history[:]  # history is trimmed to 100 in update(); keep all below
    print(f"{symbol} {tf_key}: {len(candles)} candles")
    signal_r = [t.result_r for t in all_trades(engine)]
    reasons = {}
    for t in all_trades(engine):
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    summarize("Sinyal (aturan app)", signal_r)
    print(f"  exit: {reasons}")

    times = {c.t: i for i, c in enumerate(candles)}
    holds = [times[t.closed_t] - times[t.opened_t] for t in all_trades(engine)] or [10]
    hold = max(1, int(statistics.median(holds)))

    base: list[float] = []
    for seed in range(5):
        rnd = random.Random(seed)
        made = 0
        tries = 0
        while made < len(signal_r) and tries < len(signal_r) * 50:
            tries += 1
            i = rnd.randrange(MIN_CANDLES, len(candles) - hold - 1)
            window = candles[max(0, i + 1 - WINDOW) : i + 1]
            ev = evaluate(window, tf)
            plan, _ = build_plan(window, ev, rnd.choice((1, -1)))
            if plan is None:
                continue
            for bar in candles[i + 1 : i + 1 + hold]:
                plan = manage(plan, bar)
                if plan.status == "CLOSED":
                    break
            if plan.status != "CLOSED":
                plan = close_on_signal(plan, candles[i + hold])
            base.append(plan.result_r)
            made += 1
    summarize("Acak (baseline)", base)
    print(f"  (median lama posisi: {hold} candle)")


_ALL: dict[int, list] = {}


def all_trades(engine: SignalEngine):
    return _ALL.get(id(engine), engine.state.history)


if __name__ == "__main__":
    # keep every closed trade instead of the UI's trimmed history
    import signals

    original = signals.SignalEngine._record

    def keep(self, plan):
        _ALL.setdefault(id(self), []).append(plan)
        original(self, plan)

    signals.SignalEngine._record = keep

    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tfs = sys.argv[2].split(",") if len(sys.argv) > 2 else ["15m", "1h"]
    pages = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    for tf in tfs:
        run(sym, tf, pages)
