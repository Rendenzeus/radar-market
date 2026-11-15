"""Fixes from the second review: volume sign, costs, small-sample display, Gold gate."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from analysis import evaluate
from data import TIMEFRAMES, Candle, YahooFeed
from metrics import longest_losing_streak, max_drawdown, win_rate_interval
from signals import TradePlan, engine_for, manage


def downtrend_with_green_volume_bar() -> list[Candle]:
    candles, price = [], 1000.0
    for i in range(400):
        o = price
        price -= 1.0 + (i % 5) * 0.2
        candles.append(Candle(i * 900, o, o + 0.6, price - 0.6, price, 100.0))
    last = candles[-1]
    candles.append(Candle(last.t + 900, last.c, last.c + 2.5, last.c - 0.2, last.c + 2.0, 400.0))
    return candles


class VolumeSignTests(unittest.TestCase):
    def test_high_volume_green_candle_counts_as_bullish_even_in_a_downtrend(self):
        ev = evaluate(downtrend_with_green_volume_bar(), TIMEFRAMES["15m"])
        self.assertLess(ev.score, 0)  # still a bearish read overall
        vol = next(f for f in ev.factors if f.name == "Volume")
        self.assertEqual(vol.points, 10)
        self.assertIn("melawan arah skor", vol.detail)


class CostTests(unittest.TestCase):
    def test_net_r_subtracts_round_trip_cost(self):
        plan = TradePlan(-1, 0, 100.0, 102.0, 102.0, 96.0, 94.0, 2.0, 3.0, cost_r=0.3)
        p = manage(plan, Candle(900, 100, 103, 99, 101, 1))
        self.assertEqual((p.result_r, p.net_r), (-1.0, -1.3))

    def test_binance_cost_matches_fee_schedule(self):
        from data import BinanceFeed

        self.assertAlmostEqual(BinanceFeed("BTCUSDT", "", "").cost_pct, 0.0026)


class SmallSampleTests(unittest.TestCase):
    def test_exact_interval_matches_review_numbers(self):
        lo, hi = win_rate_interval(5, 6)
        self.assertEqual((round(lo, 2), round(hi, 2)), (0.36, 1.0))
        lo, hi = win_rate_interval(3, 7)
        self.assertEqual((round(lo, 2), round(hi, 2)), (0.10, 0.82))

    def test_track_text_hides_win_rate_below_30_trades(self):
        from views.widgets import track_record_text

        sig = SimpleNamespace(disabled=None, results=lambda: [1.0, 1.2, 0.5, 2.0, 0.8, -1.0])
        text = track_record_text(sig, "biaya")
        self.assertIn("36%–100%", text)
        self.assertNotIn("83%", text)

    def test_drawdown_and_streak(self):
        r = [1, -1, -1, -1, 2, -1]
        self.assertEqual((max_drawdown(r), longest_losing_streak(r)), (3, 3))


class GoldGateTests(unittest.TestCase):
    def test_gold_intraday_signals_disabled_but_1h_allowed(self):
        feed = YahooFeed("GC=F", "Gold", "GC", "")
        with tempfile.TemporaryDirectory() as tmp:
            for key, disabled in (("15m", True), ("1h", False)):
                state = SimpleNamespace(tf=TIMEFRAMES[key], feed=feed, cache_dir=Path(tmp))
                engine = engine_for(state)
                self.assertEqual(engine.disabled is not None, disabled, key)
                st = engine.update(downtrend_with_green_volume_bar()[:-1])
                if disabled:
                    self.assertIsNone(st.plan)
                    self.assertEqual(st.label, "WAIT")


if __name__ == "__main__":
    unittest.main()
