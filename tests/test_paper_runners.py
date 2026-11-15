"""Paper runners: freshness, demo guard, ticket matching, error de-duplication.

The MT5 tests use a fake MetaTrader5 module and are skipped where the real
package cannot be imported (it only exists for Windows).
"""

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.test_signals import bar, rising
from tools import binance_paper as B


def events(path: Path) -> list[str]:
    return [json.loads(line)["event"] for line in path.read_text("utf-8").splitlines()]


class BinanceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.journal = Path(tempfile.mkdtemp()) / "j.jsonl"
        self.market = {"candles": rising(200), "bid": 300.0, "ask": 300.1}
        self.patches = [
            mock.patch.object(B, "JOURNAL", self.journal),
            mock.patch.object(B, "TF_KEYS", ("4h",)),
            mock.patch.object(B, "closed_candles", lambda s, tf: list(self.market["candles"])),
            mock.patch.object(B, "book", lambda s: (self.market["bid"], self.market["ask"])),
        ]
        for p in self.patches:
            p.start()
        B._last_error.clear()
        self.clock = 1_000_000.0
        self.time = mock.patch.object(B.time, "time", lambda: self.clock)
        self.time.start()
        B.log("start", "BTCUSDT", t=0)
        self.state = {}

    def tearDown(self):
        self.time.stop()
        for p in self.patches:
            p.stop()

    def run_pass(self, seconds_later=15):
        self.clock += seconds_later
        B.step(("BTCUSDT",), {"BTCUSDT": 0}, self.state)

    def test_breakout_seen_live_opens(self):
        self.run_pass()  # 200 candles: no EMA200 yet, nothing seen
        self.market["candles"].append(bar(self.market["candles"][-1], 299, 300.5, 299.5, 300))
        self.run_pass()
        self.assertEqual(events(self.journal), ["start", "open"])

    def test_breakout_found_after_start_is_missed_not_traded(self):
        self.market["candles"] = rising(201)  # breakout already the last closed candle at the first pass
        self.run_pass()
        self.run_pass()
        self.assertEqual(events(self.journal), ["start", "missed"])

    def test_breakout_after_a_long_gap_is_missed(self):
        self.run_pass()
        self.market["candles"].append(bar(self.market["candles"][-1], 299, 300.5, 299.5, 300))
        self.run_pass(seconds_later=3 * 3600)  # laptop asleep across the close
        self.assertEqual(events(self.journal), ["start", "missed"])

    def test_v3_takes_no_short(self):
        down = [B.Candle(i * 4 * 3600, 500 - i, 500.5 - i, 499.5 - i, 500 - i, 1) for i in range(200)]
        self.market.update(candles=down, bid=300.0, ask=300.1)
        self.run_pass()
        down.append(bar(down[-1], 300, 300.5, 290, 290.5))  # 20-candle low breakout under EMA200
        self.run_pass()
        self.assertEqual(events(self.journal), ["start"])

    def test_stop_rule_blocks_new_entries_after_50R_drawdown(self):
        for i in range(51):
            B.log("close", "BTCUSDT", tf="4h", opened_t=i, direction=1, result_r=-1.0)
        self.run_pass()
        self.market["candles"].append(bar(self.market["candles"][-1], 299, 300.5, 299.5, 300))
        self.run_pass()
        evs = events(self.journal)
        self.assertEqual(evs.count("stop_rule"), 1)
        self.assertNotIn("open", evs)

    def test_old_v2_rows_are_ignored(self):
        with self.journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "open", "symbol": "BTCUSDT", "tf": "4h", "opened_t": 1, "direction": -1,
                                 "fill": 1, "stop": 2, "version": 2}) + "\n")
        self.assertEqual(B.open_positions("BTCUSDT", "4h"), [])

    def test_repeated_errors_logged_once(self):
        with mock.patch.object(B, "book", side_effect=ConnectionError("offline")):
            for _ in range(5):
                self.run_pass()
        self.assertEqual(events(self.journal), ["start", "error"])


try:
    importlib.import_module("MetaTrader5")
    HAVE_MT5 = True
except Exception:
    HAVE_MT5 = False


@unittest.skipUnless(HAVE_MT5, "MetaTrader5 package not available")
class MT5RunnerTests(unittest.TestCase):
    DEMO, REAL = 0, 2
    SERVER_OFFSET = 7200  # broker clock = local clock + 2 h

    def setUp(self):
        from tools import mt5_paper as M

        self.M = M
        self.journal = Path(tempfile.mkdtemp()) / "j.jsonl"
        self.account = SimpleNamespace(trade_mode=self.DEMO, balance=100000.0, login=1, server="demo")
        self.candles = rising(200)
        self.positions = []
        self.sent = []
        fake = SimpleNamespace(
            ACCOUNT_TRADE_MODE_DEMO=self.DEMO,
            TRADE_ACTION_DEAL=1, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, ORDER_TIME_GTC=0,
            ORDER_FILLING_IOC=1, ORDER_FILLING_FOK=0, ORDER_FILLING_RETURN=2,
            TRADE_RETCODE_INVALID_FILL=10030, TRADE_RETCODE_DONE=10009, POSITION_TYPE_BUY=0,
            DEAL_ENTRY_OUT=1, DEAL_ENTRY_OUT_BY=3, DEAL_REASON_SL=4,
            terminal_info=lambda: SimpleNamespace(connected=True, trade_allowed=True),
            account_info=lambda: self.account,
            initialize=lambda: True, shutdown=lambda: None, last_error=lambda: (1, "ok"),
            symbol_select=lambda *a: True,
            symbol_info=lambda s: SimpleNamespace(digits=2, trade_tick_size=0.01, trade_tick_value=1.0,
                                                  volume_step=0.01, volume_min=0.01, volume_max=100),
            symbol_info_tick=lambda s: SimpleNamespace(bid=300.0, ask=300.1, time=int(self.clock) + self.SERVER_OFFSET),
            copy_rates_from_pos=lambda *a: [
                {"time": c.t, "open": c.o, "high": c.h, "low": c.l, "close": c.c, "tick_volume": c.v} for c in self.candles
            ],
            positions_get=self.positions_get,
            order_send=self.order_send,
            history_deals_get=lambda **k: [],
        )
        self.patches = [
            mock.patch.object(M, "mt5", fake),
            mock.patch.object(M, "JOURNAL", self.journal),
            mock.patch.object(M, "TIMEFRAMES", {"4h": (0, 4 * 3600, 240004)}),
        ]
        for p in self.patches:
            p.start()
        M._last_error.clear()
        # local clock 40 s before the breakout candle (index 200) closes on the broker's clock
        self.clock = 201 * 4 * 3600 - self.SERVER_OFFSET - 40.0
        self.time = mock.patch.object(M.time, "time", lambda: self.clock)
        self.time.start()
        M.log("start", t=0)
        self.state = {"prev": {}, "up": True}

    def tearDown(self):
        self.time.stop()
        for p in self.patches:
            p.stop()

    def positions_get(self, symbol=None, ticket=None):
        return [p for p in self.positions if ticket is None or p.ticket == ticket]

    def order_send(self, req):
        self.sent.append(req)
        if "position" in req:
            self.positions = [p for p in self.positions if p.ticket != req["position"]]
        else:
            self.positions.append(SimpleNamespace(ticket=555, identifier=555, type=0, volume=req["volume"],
                                                  magic=req["magic"], comment="cut"))
        return SimpleNamespace(retcode=10009, order=555, price=req["price"], volume=req["volume"], comment="done")

    def run_pass(self, seconds_later=20):
        self.clock += seconds_later
        self.M.step(0, self.state)

    def breakout(self):
        self.candles.append(bar(self.candles[-1], 299, 300.5, 299.5, 300))

    def test_switching_to_a_real_account_stops_before_any_order(self):
        self.run_pass()
        self.account.trade_mode = self.REAL
        self.breakout()
        with self.assertRaises(self.M.NotDemo):
            self.run_pass()
        self.assertEqual(self.sent, [])

    def test_late_signal_is_missed(self):
        self.candles = rising(201)
        self.run_pass(seconds_later=100)  # script starts after the breakout candle closed
        self.assertEqual(events(self.journal), ["start", "missed"])
        self.assertEqual(self.sent, [])

    def test_live_breakout_opens_and_position_is_matched_by_ticket_not_comment(self):
        self.run_pass()
        self.breakout()
        self.run_pass()
        self.assertEqual(events(self.journal), ["start", "open"])
        self.run_pass()  # comment was cut by the "broker"; still ours, rules still hold it: no close
        self.assertEqual(len(self.sent), 1)

    def test_other_positions_are_never_closed(self):
        self.positions.append(SimpleNamespace(ticket=9, identifier=9, type=0, volume=1, magic=240004, comment="manual"))
        self.run_pass()
        self.run_pass()
        self.assertEqual(self.sent, [])

    def test_friday_signal_seen_on_monday_is_missed(self):
        self.run_pass()
        self.breakout()
        self.clock += 2 * 86400  # weekend: the script only sees the close two days later
        self.run_pass()
        self.assertEqual(events(self.journal)[-1], "missed")
        self.assertEqual(self.sent, [])

    def test_disconnect_logged_once_and_breaks_freshness(self):
        self.run_pass()
        with mock.patch.object(self.M.mt5, "terminal_info", lambda: None), mock.patch.object(self.M.mt5, "initialize", lambda: False):
            for _ in range(5):
                self.run_pass()
        self.breakout()
        self.run_pass()  # first pass after reconnect: the close was not seen live
        self.assertEqual(events(self.journal), ["start", "disconnected", "reconnected", "missed"])


if __name__ == "__main__":
    unittest.main()
