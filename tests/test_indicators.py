import random
import time
import unittest

from analysis import evaluate, label_for
from data import Candle
from indicators import atr_series, ema_series, macd_series, rsi_series


def naive_ema(values, period):
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = (v - e) * k + e
    return e


def synthetic_candles(n, drift=0.0, seed=1):
    rnd = random.Random(seed)
    now = int(time.time()) - int(time.time()) % 900 - n * 900
    price, out = 100.0, []
    for i in range(n):
        o = price
        price = max(1.0, price * (1 + drift + rnd.gauss(0, 0.002)))
        out.append(Candle(now + i * 900, o, max(o, price) * 1.001, min(o, price) * 0.999, price, 100 + rnd.random() * 50))
    return out


class IndicatorTests(unittest.TestCase):
    # Classic Wilder RSI example data (StockCharts "RSI" ChartSchool article).
    RSI_DATA = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89,
                46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]

    def test_rsi_matches_reference(self):
        rsi = rsi_series(self.RSI_DATA, 14)
        self.assertIsNone(rsi[13])
        # The article rounds its averages to 2 decimals, so allow ~0.1 drift.
        for got, ref in zip(rsi[14:], [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]):
            self.assertAlmostEqual(got, ref, delta=0.1)

    def test_ema_series_matches_naive(self):
        values = [100 + i * 0.1 + (i % 7) for i in range(300)]
        series = ema_series(values, 20)
        for end in (20, 57, 300):
            self.assertAlmostEqual(series[end - 1], naive_ema(values[:end], 20), places=9)

    def test_macd_signal_is_ema_of_macd(self):
        values = [100 + (i % 13) * 0.7 - (i % 5) for i in range(200)]
        macd, signal, hist = macd_series(values)
        expected_macd = naive_ema(values, 12) - naive_ema(values, 26)
        self.assertAlmostEqual(macd[-1], expected_macd, places=9)
        self.assertAlmostEqual(signal[-1], naive_ema(macd[25:], 9), places=9)
        self.assertAlmostEqual(hist[-1], macd[-1] - signal[-1], places=9)

    def test_atr_positive(self):
        c = synthetic_candles(100)
        atr = atr_series([x.h for x in c], [x.l for x in c], [x.c for x in c])
        self.assertGreater(atr[-1], 0)


class SignalTests(unittest.TestCase):
    def test_uptrend_is_bullish_and_downtrend_bearish(self):
        self.assertGreater(evaluate(synthetic_candles(600, drift=0.0015)).score, 25)
        self.assertLess(evaluate(synthetic_candles(600, drift=-0.0015)).score, -25)

    def test_labels(self):
        self.assertEqual(label_for(70), "STRONG BUY")
        self.assertEqual(label_for(40), "BUY")
        self.assertEqual(label_for(30), "WAIT")  # below the entry band
        self.assertEqual(label_for(-40), "SELL")
        self.assertEqual(label_for(-61), "STRONG SELL")
        self.assertEqual(label_for(-80, gated=True), "WAIT")  # ADX gate

    def test_every_timeframe_evaluates(self):
        from data import TIMEFRAMES
        for tf in TIMEFRAMES.values():
            candles = synthetic_candles(1000, drift=0.001)
            candles = [Candle(i * tf.seconds, c.o, c.h, c.l, c.c, c.v) for i, c in enumerate(candles)]
            ev = evaluate(candles, tf)
            self.assertGreater(ev.score, 0, tf.key)
            self.assertEqual(ev.htf, tf.htf_key)

    def test_resample_aggregates_ohlcv(self):
        from data import resample
        bars = [Candle(0, 1, 5, 0.5, 2, 10), Candle(60, 2, 3, 1, 4, 5), Candle(120, 4, 4, 3, 3, 1)]
        out = resample(bars, 120)
        self.assertEqual(out[0], Candle(0, 1, 5, 0.5, 4, 15))
        self.assertEqual(out[1], Candle(120, 4, 4, 3, 3, 1))

    def test_evaluate_is_fast(self):
        candles = synthetic_candles(1200)
        start = time.perf_counter()
        evaluate(candles)
        self.assertLess(time.perf_counter() - start, 0.2)


if __name__ == "__main__":
    unittest.main()
