import time
import unittest

from data import TIMEFRAMES, Candle, OrderFlow
from news import headline_sentiment
from reversal import _mirror, detect, flow_points


def downtrend_with_hammer(n=300):
    """Steady downtrend, then a long-wick hammer on heavy volume."""
    tf = TIMEFRAMES["15m"]
    start = int(time.time()) - int(time.time()) % tf.seconds - n * tf.seconds
    out, price = [], 200.0
    for i in range(n - 1):
        o = price
        price *= 0.997 if i % 4 else 1.001
        out.append(Candle(start + i * tf.seconds, o, max(o, price) * 1.0005, min(o, price) * 0.9995, price, 100))
    o = price
    out.append(Candle(start + (n - 1) * tf.seconds, o, o * 1.001, o * 0.97, o * 1.0008, 400))
    # forming candle
    out.append(Candle(start + n * tf.seconds, o * 1.0008, o * 1.002, o * 1.0, o * 1.0015, 10))
    return out, tf


class ReversalTests(unittest.TestCase):
    def test_downtrend_looks_for_bullish_reversal(self):
        candles, tf = downtrend_with_hammer()
        rev = detect(candles, tf, candles[-1].c)
        self.assertEqual(rev.direction, 1)
        details = " ".join(f.detail for f in rev.factors)
        self.assertIn("hammer", details)
        self.assertGreater(rev.score, 0)
        self.assertLess(rev.invalidation, rev.trigger)

    def test_uptrend_is_mirrored(self):
        candles, tf = downtrend_with_hammer()
        rev = detect(_mirror(candles), tf, -candles[-1].c)
        self.assertEqual(rev.direction, -1)
        self.assertIn("shooting star", " ".join(f.detail for f in rev.factors))
        self.assertGreater(rev.invalidation, rev.trigger)

    def test_trigger_stage_requires_break(self):
        candles, tf = downtrend_with_hammer()
        flow = OrderFlow(60, 0.7, 0.72, 0.5, 2.0, 1000, time.time())
        below = detect(candles, tf, candles[-1].c, flow=flow, news_score=0.5, news_shift=0.5, has_news=True)
        self.assertIn(below.stage, {"SETUP", "WATCH"})
        above = detect(candles, tf, below.trigger * 1.01, flow=flow, news_score=0.5, news_shift=0.5, has_news=True)
        if below.stage == "SETUP":
            self.assertEqual(above.stage, "TRIGGER")

    def test_flow_points_are_symmetric(self):
        flow = OrderFlow(60, 0.3, 0.3, 0.3, 0.5, 1000, time.time())
        bull, _ = flow_points(flow, +1)
        bear, _ = flow_points(flow, -1)
        self.assertLess(bull, 0)
        self.assertGreater(bear, 0)


class SentimentTests(unittest.TestCase):
    def test_direction_of_typical_headlines(self):
        cases = [
            ("GC=F", "Gold rises as dollar weakens on rate cut hopes", 1),
            ("GC=F", "Gold posts steep weekly loss as Treasury yields rise", -1),
            ("GC=F", "Gold rebounds from 13-month low", 1),
            ("BTCUSDT", "Bitcoin tumbles to three-month low as liquidations accelerate", -1),
            ("BTCUSDT", "Bitcoin jumps above $70,000 as ETF inflows return", 1),
        ]
        for symbol, title, sign in cases:
            self.assertEqual((headline_sentiment(symbol, title) > 0) - (headline_sentiment(symbol, title) < 0), sign, title)

    def test_verbs_far_from_the_asset_are_ignored(self):
        # "rise" belongs to yields, not gold
        self.assertLessEqual(headline_sentiment("GC=F", "Gold steady while Treasury yields rise"), 0)


if __name__ == "__main__":
    unittest.main()
