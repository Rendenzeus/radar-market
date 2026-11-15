"""Signal v2 rules (docs/signal-v2.md)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from data import TIMEFRAMES, BinanceFeed, Candle, YahooFeed
from signals import SignalEngine, engine_for, paper_start, paper_summary, run_strategy


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


def events(path: Path) -> list[dict]:
    return [r for r in (json.loads(x) for x in path.read_text("utf-8").splitlines()) if r["event"] != "start"]


class JournalTests(unittest.TestCase):
    def test_trades_opened_before_the_paper_start_are_not_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            c = rising(230)
            engine = SignalEngine(TIMEFRAMES["4h"], path, "TEST")
            engine._started = c[-1].t  # paper trading starts near the end
            c.append(bar(c[-1], 329, 329.5, 300, 301))  # closes the old position
            engine.update(c)
            self.assertEqual(events(path), [])

    def test_paper_start_is_written_once_and_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            first = paper_start(path)
            self.assertEqual(SignalEngine(TIMEFRAMES["4h"], path, "A")._started, first)
            self.assertEqual(SignalEngine(TIMEFRAMES["1d"], path, "B")._started, first)
            self.assertEqual(path.read_text("utf-8").count('"start"'), 1)

    def test_trade_missed_while_the_app_was_shut_is_backfilled_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            c = rising(230)
            c.append(bar(c[-1], 329, 329.5, 300, 301))
            path.write_text(json.dumps({"event": "start", "t": c[150].t, "version": 3}) + "\n", "utf-8")
            # the app only opens after the trade has already opened and closed
            for _ in range(2):  # two sessions: nothing is written twice
                SignalEngine(TIMEFRAMES["4h"], path, "TEST").update(c)
            rows = events(path)
            self.assertEqual([r["event"] for r in rows], ["open", "close"])
            self.assertEqual((rows[0]["status"], rows[0]["net_r"], rows[0]["exit_price"]), ("OPEN", None, None))
            self.assertEqual((rows[1]["exit_reason"], rows[1]["exit_price"]), ("TRAIL", 301))
            self.assertTrue(rows[0]["backfilled"] and rows[1]["backfilled"])
            s = paper_summary(path)
            self.assertEqual((s["trades"], s["open"], s["missed"]), (1, [], 1))

    def test_open_position_shows_in_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            c = rising(230)
            path.write_text(json.dumps({"event": "start", "t": c[150].t, "version": 3}) + "\n", "utf-8")
            SignalEngine(TIMEFRAMES["4h"], path, "TEST").update(c)
            s = paper_summary(path)
            self.assertEqual((s["trades"], len(s["open"]), s["open"][0]["direction"]), (0, 1, 1))


    def test_v2_rows_do_not_count_in_the_v3_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            old = [{"event": "start", "t": 1, "version": 2},
                   {"event": "open", "symbol": "X", "tf": "4h", "opened_t": 5, "direction": -1, "version": 2},
                   {"event": "close", "symbol": "X", "tf": "4h", "opened_t": 5, "net_r": -1.0, "version": 2}]
            path.write_text("".join(json.dumps(r) + "\n" for r in old), "utf-8")
            self.assertGreater(paper_start(path), 1)  # v3 starts its own record
            s = paper_summary(path)
            self.assertEqual((s["trades"], s["open"]), (0, []))


class LongOnlyTests(unittest.TestCase):
    def test_app_engine_never_sells(self):
        c = [Candle(i * 4 * 3600, 500 - i, 500.5 - i, 499.5 - i, 500 - i, 1) for i in range(260)]
        st = SignalEngine(TIMEFRAMES["4h"]).update(c)
        self.assertEqual((st.label, st.plan, st.history), ("WAIT", None, []))
        self.assertIsNotNone(run_strategy(c)[1])  # v2 rules with shorts would be in a SELL here


class PaperFeedTests(unittest.TestCase):
    def test_paper_covers_the_five_holdout_coins_on_signal_timeframes(self):
        from data import FEEDS, PAPER_FEEDS

        crypto = {f.symbol for f in (*FEEDS, *PAPER_FEEDS) if isinstance(f, BinanceFeed)}
        self.assertEqual(crypto, {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"})
        for f in (*FEEDS, *PAPER_FEEDS):
            self.assertTrue(f.signal_timeframes)
            for key in f.signal_timeframes:
                self.assertGreaterEqual(TIMEFRAMES[key].seconds, f.min_signal_seconds)


if __name__ == "__main__":
    unittest.main()
