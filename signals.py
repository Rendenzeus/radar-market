"""Closed-candle signal engine with hysteresis and a locked trade plan.

State machine, evaluated once per CLOSED candle (never on the forming one):

  FLAT  --|score| >= ENTER, ADX gate open, plan R:R >= MIN_RR-->  LONG / SHORT
  LONG/SHORT --stop hit / TP2 hit / |score| < EXIT / score flips-->  FLAT

When a position opens, entry (= close of the signal candle), stop and targets
are LOCKED and never recomputed:
  stop  = beyond the most recent swing high/low + 0.25 ATR (structure, not a
          fixed ATR multiple), at least 0.5 ATR away; reject if > 4 ATR
  TP1   = nearest support/resistance in the trade direction, or 2R if none;
          the signal is rejected when TP1 is below MIN_RR (1.5R)
  TP2   = next level beyond TP1, at least TP1 + 1R (3R if none)
Management: half off at TP1 and the stop moves to entry, rest runs to TP2.
Results are measured in R (multiples of the initial risk).

On start-up the engine replays the last REPLAY_BARS closed candles, so the
current state does not depend on when the app was opened, and those replayed
trades double as a rolling performance record.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from analysis import ENTER, EXIT, MIN_CANDLES, STRONG, Evaluation, evaluate
from data import Candle, MarketState, Timeframe
from indicators import pivots

WINDOW = 900  # candles per evaluation (enough for the higher-timeframe EMA50)
REPLAY_BARS = 200
MIN_RR = 1.5
STOP_BUFFER_ATR = 0.25
MIN_RISK_ATR = 0.5
MAX_RISK_ATR = 4.0


@dataclass
class TradePlan:
    direction: int  # +1 long, -1 short
    opened_t: int  # open time of the signal candle
    entry: float
    stop: float  # current stop (moves to entry after TP1)
    initial_stop: float
    tp1: float
    tp2: float
    rr1: float
    rr2: float
    status: str = "OPEN"  # OPEN | TP1 | CLOSED
    closed_t: int | None = None
    exit_reason: str | None = None  # SL | BE | TP2 | SIGNAL
    result_r: float | None = None

    @property
    def risk(self) -> float:
        return abs(self.entry - self.initial_stop)


@dataclass
class SignalState:
    label: str = "WAIT"
    plan: TradePlan | None = None
    rejection: str | None = None  # why the last qualifying candle was not taken
    rejection_t: int | None = None
    history: list[TradePlan] = field(default_factory=list)  # closed trades in the replay window
    last_t: int | None = None  # last processed closed candle
    gated: bool = False

    def stats(self) -> tuple[int, float, float]:
        """(trades, win rate, average R) over closed trades."""
        done = [p for p in self.history if p.result_r is not None]
        if not done:
            return 0, 0.0, 0.0
        wins = sum(1 for p in done if p.result_r > 0)
        return len(done), wins / len(done), sum(p.result_r for p in done) / len(done)


def build_plan(window: list[Candle], ev: Evaluation, direction: int) -> tuple[TradePlan | None, str | None]:
    """Structure-based plan for a signal on the last candle of ``window``."""
    entry = window[-1].c
    atr = ev.atr
    if not atr:
        return None, "ATR tidak tersedia"
    highs = [c.h for c in window]
    lows = [c.l for c in window]
    ph, pl = pivots(highs, lows, k=3, lookback=40)

    if direction < 0:
        swings = [highs[i] for i in reversed(ph) if highs[i] > entry]
        anchor = swings[0] if swings else max(highs[-10:])
        stop = max(anchor + STOP_BUFFER_ATR * atr, entry + MIN_RISK_ATR * atr)
        levels = [s for s in ev.supports if entry - s >= 0.2 * atr]
    else:
        swings = [lows[i] for i in reversed(pl) if lows[i] < entry]
        anchor = swings[0] if swings else min(lows[-10:])
        stop = min(anchor - STOP_BUFFER_ATR * atr, entry - MIN_RISK_ATR * atr)
        levels = [r for r in ev.resistances if r - entry >= 0.2 * atr]

    risk = abs(entry - stop)
    if risk > MAX_RISK_ATR * atr:
        return None, f"Stop struktur terlalu jauh ({risk / atr:.1f}× ATR)"

    tp1 = levels[0] if levels else entry + direction * 2 * risk
    rr1 = abs(tp1 - entry) / risk
    if rr1 < MIN_RR:
        name = "support" if direction < 0 else "resistance"
        return None, f"R:R TP1 cuma {rr1:.1f} (< {MIN_RR}): {name} {tp1:,.2f} terlalu dekat"

    beyond = [lv for lv in levels[1:] if abs(lv - entry) >= abs(tp1 - entry) + risk]
    tp2 = beyond[0] if beyond else entry + direction * max(3 * risk, abs(tp1 - entry) + risk)
    rr2 = abs(tp2 - entry) / risk
    plan = TradePlan(direction, window[-1].t, entry, stop, stop, tp1, tp2, round(rr1, 2), round(rr2, 2))
    return plan, None


def manage(plan: TradePlan, bar: Candle) -> TradePlan:
    """Advance an open plan through one closed candle (stop checked first: conservative)."""
    d = plan.direction
    hit_stop = bar.h >= plan.stop if d < 0 else bar.l <= plan.stop
    if hit_stop:
        if plan.status == "TP1":
            return replace(plan, status="CLOSED", closed_t=bar.t, exit_reason="BE", result_r=round(0.5 * plan.rr1, 2))
        return replace(plan, status="CLOSED", closed_t=bar.t, exit_reason="SL", result_r=-1.0)
    if plan.status == "OPEN" and (bar.l <= plan.tp1 if d < 0 else bar.h >= plan.tp1):
        plan = replace(plan, status="TP1", stop=plan.entry)
    if plan.status == "TP1" and (bar.l <= plan.tp2 if d < 0 else bar.h >= plan.tp2):
        return replace(plan, status="CLOSED", closed_t=bar.t, exit_reason="TP2", result_r=round(0.5 * plan.rr1 + 0.5 * plan.rr2, 2))
    return plan


def close_on_signal(plan: TradePlan, bar: Candle) -> TradePlan:
    move = (bar.c - plan.entry) * plan.direction / plan.risk
    result = 0.5 * plan.rr1 + 0.5 * move if plan.status == "TP1" else move
    return replace(plan, status="CLOSED", closed_t=bar.t, exit_reason="SIGNAL", result_r=round(result, 2))


class SignalEngine:
    def __init__(self, tf: Timeframe, journal: Path | None = None, symbol: str = ""):
        self.tf = tf
        self.state = SignalState()
        self.last_eval: Evaluation | None = None
        self._journal = journal
        self._symbol = symbol

    def _step(self, window: list[Candle]) -> None:
        bar = window[-1]
        ev = evaluate(window, self.tf)
        self.last_eval = ev
        st = self.state
        st.gated = ev.sideways

        if st.plan is not None:
            if bar.t > st.plan.opened_t:
                st.plan = manage(st.plan, bar)
            if st.plan.status != "CLOSED":
                d = st.plan.direction
                if ev.score * d < EXIT:  # weakened below the exit band or flipped
                    st.plan = close_on_signal(st.plan, bar)
            if st.plan.status == "CLOSED":
                st.history.append(st.plan)
                self._record(st.plan)
                st.plan = None

        if st.plan is None and not ev.sideways and abs(ev.score) >= ENTER:
            direction = 1 if ev.score > 0 else -1
            plan, reason = build_plan(window, ev, direction)
            if plan:
                st.plan = plan
                st.rejection = None
            else:
                st.rejection, st.rejection_t = f"{'BUY' if direction > 0 else 'SELL'} ditolak: {reason}", bar.t

        if st.plan is not None:
            strong = ev.score * st.plan.direction >= STRONG
            st.label = ("STRONG " if strong else "") + ("BUY" if st.plan.direction > 0 else "SELL")
        else:
            st.label = "WAIT"
        st.last_t = bar.t

    def update(self, closed: list[Candle]) -> SignalState:
        """Process every closed candle not seen yet."""
        times = [c.t for c in closed]
        st = self.state
        if st.last_t is not None and st.last_t not in set(times):
            self.state = st = SignalState()  # history gap: replay from scratch
        if st.last_t is None:
            start = max(MIN_CANDLES - 1, len(closed) - REPLAY_BARS)
        else:
            start = times.index(st.last_t) + 1
        for i in range(start, len(closed)):
            self._step(closed[max(0, i + 1 - WINDOW) : i + 1])
        st.history = st.history[-100:]
        return st

    # ------------------------------------------------------------ journal
    def _record(self, plan: TradePlan) -> None:
        if self._journal is None:
            return
        try:
            rows = json.loads(self._journal.read_text("utf-8")) if self._journal.exists() else []
        except Exception:
            rows = []
        key = (self._symbol, self.tf.key, plan.opened_t)
        if any((r.get("symbol"), r.get("tf"), r.get("opened_t")) == key for r in rows):
            return
        rows.append({"symbol": self._symbol, "tf": self.tf.key, "recorded_at": int(time.time()), **asdict(plan)})
        try:
            self._journal.parent.mkdir(parents=True, exist_ok=True)
            self._journal.write_text(json.dumps(rows[-2000:], separators=(",", ":")), "utf-8")
        except Exception:
            pass


def journal_stats(path: Path, symbol: str, tf_key: str) -> tuple[int, float, float]:
    try:
        rows = [r for r in json.loads(path.read_text("utf-8")) if r["symbol"] == symbol and r["tf"] == tf_key]
    except Exception:
        return 0, 0.0, 0.0
    if not rows:
        return 0, 0.0, 0.0
    wins = sum(1 for r in rows if r["result_r"] > 0)
    return len(rows), wins / len(rows), sum(r["result_r"] for r in rows) / len(rows)


def engine_for(state: MarketState) -> SignalEngine:
    engine = getattr(state, "engine", None)
    if engine is None:
        engine = SignalEngine(state.tf, state.cache_dir / "journal.json", state.feed.symbol)
        state.engine = engine
    return engine
