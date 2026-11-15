"""Signal v2 rules (docs/signal-v2.md)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from data import TIMEFRAMES, BinanceFeed, Candle, YahooFeed
from signals import SignalEngine, engine_for, run_strategy


def rising(n=230, step=4 * 3600):
    """Steady uptrend: every close is 1 above the last, range +-0.5, ATR 1.5."""
    out, price = [], 100.0
    for i in range(n):
        out.append(Candle(i * step, price - 1, price + 0.5, price - 0.5, price, 10.0))
        price += 1
    return out


def bar(prev: Candle, o, h, l, c, step=4 * 3600):
    return Candle(prev.t + step, o, h, l, c, 10.0)


class RuleTests(unittest.TestCase):
    def test_long_breakout_above_ema200_with_2_atr_stop(self):
        trades, plan = run_strategy(rising())
        self.assertEqual(trades, [])
        self.assertEqual(plan.direction, 1)
        self.assertEqual(plan.opened_t, rising()[200].t)  # first candle with EMA200
        self.assertAlmostEqual(plan.entry - plan.stop, 3.0, places=1)  # 2 x ATR 1.5

    def test_trailing_exit_at_close(self):
        c = rising()
        c.append(bar(c[-1], 329, 329.5, 300, 301))  # close far below the 10-candle low, above the stop
        trades, plan = run_strategy(c)
        self.assertIsNone(plan)
        # stop sits ~3 below the entry at 300, so this is a trailing exit, not a stop
        self.assertEqual(trades[-1].exit_reason, "TRAIL")
        self.assertEqual(trades[-1].exit_price, 301)

    def test_stop_gap_fills_at_open(self):
        c = rising(205)
        entry = run_strategy(c)[1]
        c.append(bar(c[-1], entry.stop - 5, entry.stop - 4, entry.stop - 8, entry.stop - 6))
        t = run_strategy(c)[0][-1]
        self.assertEqual((t.exit_reason, t.exit_price), ("SL", entry.stop - 5))
        self.assertLess(t.result_r, -1.0)  # a gap costs more than 1R

    def test_cost_in_r(self):
        trades, plan = run_strategy(rising(), cost_pct=0.0026)
        self.assertAlmostEqual(plan.cost_r, 0.0026 * plan.entry / plan.risk, places=3)

    def test_no_long_below_ema200(self):
        c = [Candle(i * 3600, 500 - i, 500.5 - i, 499.5 - i, 500 - i, 1) for i in range(230)]
        c.append(bar(c[-1], 271, 290, 270, 289, 3600))  # breaks the 20-candle high but stays under EMA200
        trades, plan = run_strategy(c, allow_short=False)
        self.assertEqual((trades, plan), ([], None))

    def test_levels_never_move_except_trailing(self):
        c = rising(210)
        p1 = run_strategy(c)[1]
        p2 = run_strategy(rising(229))[1]
        self.assertEqual((p1.entry, p1.stop), (p2.entry, p2.stop))
        self.assertGreater(p2.trail, p1.trail)


class GateTests(unittest.TestCase):
    def check(self, feed, key, disabled):
        with tempfile.TemporaryDirectory() as tmp:
            state = SimpleNamespace(tf=TIMEFRAMES[key], feed=feed, cache_dir=Path(tmp))
            self.assertEqual(engine_for(state).disabled is not None, disabled, (feed.symbol, key))

    def test_signal_timeframes(self):
        btc, gold = BinanceFeed("BTCUSDT", "", ""), YahooFeed("GC=F", "", "", "")
        for key, off in (("15m", True), ("1h", True), ("4h", False), ("1d", False)):
            self.check(btc, key, off)
        for key, off in (("1h", True), ("4h", True), ("1d", False)):
            self.check(gold, key, off)


class JournalTests(unittest.TestCase):
    def test_only_live_events_are_logged_once_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            c = rising(230)
            engine = SignalEngine(TIMEFRAMES["4h"], path, "TEST")
            engine._started = c[-1].t  # pretend the app started near the end
            engine.update(c[:205])  # entry happened long before start: not logged
            self.assertFalse(path.exists())
            c.append(bar(c[-1], 329, 329.5, 300, 301))
            engine.update(c)
            engine.update(c)  # same candle again: no duplicate
            rows = [json.loads(x) for x in path.read_text("utf-8").splitlines()]
            self.assertEqual([r["event"] for r in rows], ["close"])
            self.assertEqual(rows[0]["exit_reason"], "TRAIL")


if __name__ == "__main__":
    unittest.main()
