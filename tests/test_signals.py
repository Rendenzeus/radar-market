import unittest

from data import Candle
from signals import TradePlan, close_on_signal, manage


def short_plan():
    # entry 100, stop 102 (risk 2), TP1 96 (2R), TP2 94 (3R)
    return TradePlan(-1, 0, 100.0, 102.0, 102.0, 96.0, 94.0, 2.0, 3.0)


class ManageTests(unittest.TestCase):
    def test_stop_is_checked_before_target_in_the_same_candle(self):
        p = manage(short_plan(), Candle(900, 100, 103, 95, 99, 1))
        self.assertEqual((p.status, p.exit_reason, p.result_r), ("CLOSED", "SL", -1.0))

    def test_tp1_moves_stop_to_entry_then_breakeven(self):
        p = manage(short_plan(), Candle(900, 100, 101, 95.5, 96, 1))
        self.assertEqual((p.status, p.stop), ("TP1", 100.0))
        p = manage(p, Candle(1800, 96, 100.5, 96, 100, 1))
        self.assertEqual((p.exit_reason, p.result_r), ("BE", 1.0))  # half closed at +2R

    def test_tp2_full_target(self):
        p = manage(short_plan(), Candle(900, 100, 101, 95.5, 96, 1))
        p = manage(p, Candle(1800, 96, 97, 93, 94, 1))
        self.assertEqual((p.exit_reason, p.result_r), ("TP2", 2.5))

    def test_levels_never_move_except_breakeven(self):
        p = short_plan()
        for t in range(1, 10):
            p = manage(p, Candle(t * 900, 100, 101, 99, 100, 1))
        self.assertEqual((p.stop, p.tp1, p.tp2, p.status), (102.0, 96.0, 94.0, "OPEN"))

    def test_signal_exit_in_r(self):
        p = close_on_signal(short_plan(), Candle(900, 100, 100, 98, 99, 1))
        self.assertEqual((p.exit_reason, p.result_r), ("SIGNAL", 0.5))


if __name__ == "__main__":
    unittest.main()
