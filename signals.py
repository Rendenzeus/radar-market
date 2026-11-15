"""Signal v2: Donchian breakout with a trend filter on large timeframes.

Spec (fixed before testing, see docs/signal-v2.md):
  long  when close > highest high of the previous 20 candles and close > EMA200
  short when close < lowest low  of the previous 20 candles and close < EMA200
  entry at the close of the breakout candle
  stop  2 x ATR(14) from entry, locked; checked with high/low on later candles,
        filled at the open when a candle gaps through it
  exit  long: close < lowest low of the previous 10 candles (short mirrored),
        filled at that close; no profit target
  one position at a time; a new entry needs a new breakout after the exit

Results are in R (1R = the 2 x ATR stop distance) minus the round-trip cost in R.
The composite score in analysis.py is shown as context only; it no longer trades.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from analysis import MIN_CANDLES, Evaluation, evaluate
from data import Candle, MarketState, Timeframe
from indicators import atr_series, ema_series

ENTRY_N = 20
EXIT_N = 10
TREND_EMA = 200
STOP_ATR = 2.0
EVAL_WINDOW = 900  # candles for the context score
PAPER_MIN_TRADES = 30

SIGNAL_TIMEFRAMES_TEXT = {14400: "4 jam dan 1 hari", 86400: "1 hari"}


@dataclass
class TradePlan:
    direction: int  # +1 long, -1 short
    opened_t: int  # open time of the breakout candle
    entry: float
    stop: float  # initial stop, never moved
    risk: float  # entry-to-stop distance (1R)
    cost_r: float  # round-trip cost in R
    trail: float | None = None  # exit if a candle closes beyond this level
    status: str = "OPEN"  # OPEN | CLOSED
    closed_t: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # SL | TRAIL
    result_r: float | None = None  # gross

    @property
    def net_r(self) -> float | None:
        return None if self.result_r is None else round(self.result_r - self.cost_r, 3)

    def open_r(self, price: float) -> float:
        """Unrealised result at ``price``, net of costs."""
        return (price - self.entry) * self.direction / self.risk - self.cost_r


@dataclass
class SignalState:
    label: str = "WAIT"
    plan: TradePlan | None = None
    history: list[TradePlan] = field(default_factory=list)  # closed trades in the loaded data
    last_t: int | None = None
    disabled: str | None = None
    bars: int = 0  # candles the history covers

    def results(self) -> list[float]:
        return [p.net_r for p in self.history if p.net_r is not None]

    def stats(self) -> tuple[int, int, float]:
        """(trades, wins, average net R)."""
        done = self.results()
        if not done:
            return 0, 0, 0.0
        return len(done), sum(1 for r in done if r > 0), sum(done) / len(done)


def _close(plan: TradePlan, bar: Candle, price: float, reason: str) -> TradePlan:
    plan.status, plan.closed_t, plan.exit_price, plan.exit_reason = "CLOSED", bar.t, price, reason
    plan.result_r = round((price - plan.entry) * plan.direction / plan.risk, 3)
    return plan


def run_strategy(
    candles: list[Candle], cost_pct: float = 0.0, allow_short: bool = True
) -> tuple[list[TradePlan], TradePlan | None]:
    """Run the rules over closed candles. Returns (closed trades, open position)."""
    n = len(candles)
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    closes = [c.c for c in candles]
    ema = ema_series(closes, TREND_EMA)
    atr = atr_series(highs, lows, closes)
    trades: list[TradePlan] = []
    plan: TradePlan | None = None

    for i in range(max(TREND_EMA, ENTRY_N), n):
        bar = candles[i]
        if plan is not None:
            d = plan.direction
            if d > 0 and bar.l <= plan.stop:
                trades.append(_close(plan, bar, min(bar.o, plan.stop), "SL"))
            elif d < 0 and bar.h >= plan.stop:
                trades.append(_close(plan, bar, max(bar.o, plan.stop), "SL"))
            elif (d > 0 and bar.c < min(lows[i - EXIT_N : i])) or (d < 0 and bar.c > max(highs[i - EXIT_N : i])):
                trades.append(_close(plan, bar, bar.c, "TRAIL"))
            else:
                plan.trail = min(lows[i - EXIT_N + 1 : i + 1]) if d > 0 else max(highs[i - EXIT_N + 1 : i + 1])
                continue
            plan = None
            continue  # no entry on the exit candle

        if ema[i] is None or not atr[i]:
            continue
        d = 0
        if bar.c > max(highs[i - ENTRY_N : i]) and bar.c > ema[i]:
            d = 1
        elif allow_short and bar.c < min(lows[i - ENTRY_N : i]) and bar.c < ema[i]:
            d = -1
        if d:
            risk = STOP_ATR * atr[i]
            plan = TradePlan(
                d,
                bar.t,
                bar.c,
                bar.c - d * risk,
                risk,
                round(cost_pct * bar.c / risk, 3),
                trail=min(lows[i - EXIT_N + 1 : i + 1]) if d > 0 else max(highs[i - EXIT_N + 1 : i + 1]),
            )
    return trades, plan


class SignalEngine:
    def __init__(
        self,
        tf: Timeframe,
        journal: Path | None = None,
        symbol: str = "",
        cost_pct: float = 0.0,
        disabled: str | None = None,
    ):
        self.tf = tf
        self.cost_pct = cost_pct
        self.disabled = disabled
        self.state = SignalState(disabled=disabled)
        self.last_eval: Evaluation | None = None
        self._journal = journal
        self._symbol = symbol
        # Paper trading starts once (first run) and is stored in the journal, so trades
        # that open or close while the app is shut are still recorded on the next run.
        self._started = paper_start(journal) if journal is not None else time.time()
        self._logged: set[tuple] = set()

    def update(self, closed: list[Candle]) -> SignalState:
        """Re-run the rules when a new candle has closed."""
        if not closed or closed[-1].t == self.state.last_t:
            return self.state
        if len(closed) >= MIN_CANDLES:
            self.last_eval = evaluate(closed[-EVAL_WINDOW:], self.tf)
        st = SignalState(disabled=self.disabled, last_t=closed[-1].t, bars=len(closed))
        if not self.disabled:
            trades, plan = run_strategy(closed, self.cost_pct)
            st.history, st.plan = trades, plan
            st.label = "WAIT" if plan is None else ("BUY" if plan.direction > 0 else "SELL")
            self._log_live(trades, plan)
        self.state = st
        return st

    # ------------------------------------------------------------ journal
    def _is_live(self, t: int) -> bool:
        """True for an event on a candle that closed after paper trading started."""
        return t + self.tf.seconds >= self._started

    def _log_live(self, trades: list[TradePlan], plan: TradePlan | None) -> None:
        """Record every trade opened since the paper start, including ones missed while the app was shut."""
        for p in trades:
            if self._is_live(p.opened_t):
                self._append("open", p)
                self._append("close", p)
        if plan is not None and self._is_live(plan.opened_t):
            self._append("open", plan)

    def _append(self, event: str, plan: TradePlan) -> None:
        """Append-only log, one JSON object per line, each event written once."""
        key = (event, plan.opened_t)
        if self._journal is None or key in self._logged:
            return
        self._logged.add(key)
        fields = asdict(plan)
        net_r = plan.net_r
        happened = (plan.closed_t if event == "close" else plan.opened_t) + self.tf.seconds
        if event == "open":  # the row describes the trade as it was at entry
            fields.update(status="OPEN", closed_t=None, exit_price=None, exit_reason=None, result_r=None)
            net_r = None
        now = int(time.time())
        row = {
            "event": event,
            "symbol": self._symbol,
            "tf": self.tf.key,
            "logged_at": now,
            # True when the event was written more than one candle after it happened
            # (app was shut): the rule is the same, but nobody could have acted on it live.
            "backfilled": now - happened > self.tf.seconds,
            "version": 2,
            **fields,
            "net_r": net_r,
        }
        try:
            self._journal.parent.mkdir(parents=True, exist_ok=True)
            if not _already_logged(self._journal, row):
                with self._journal.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        except Exception:
            pass


def _rows(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    except Exception:
        return []


def paper_start(path: Path) -> float:
    """Time paper trading started. Written to the journal once and never changed."""
    rows = _rows(path)
    for r in rows:
        if r.get("event") == "start":
            return float(r["t"])
    # A journal from before this field existed: start at its first entry.
    t = min((r["logged_at"] for r in rows if "logged_at" in r), default=int(time.time()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "start", "t": t, "version": 2}, separators=(",", ":")) + "\n")
    except Exception:
        pass
    return float(t)


def paper_summary(path: Path) -> dict:
    """Whole paper record: start time, open positions, closed trades (net R), missed count."""
    rows = _rows(path)
    start = next((float(r["t"]) for r in rows if r.get("event") == "start"), None)
    ident = lambda r: (r.get("symbol"), r.get("tf"), r.get("opened_t"))  # noqa: E731
    opened = {ident(r): r for r in rows if r.get("event") == "open"}
    closed = [r for r in rows if r.get("event") == "close" and ident(r) in opened]
    done = {ident(r) for r in closed}
    net = [r["net_r"] for r in closed]
    return {
        "start": start,
        "open": [r for k, r in opened.items() if k not in done],
        "trades": len(net),
        "wins": sum(1 for x in net if x > 0),
        "total_r": sum(net),
        "missed": sum(1 for r in opened.values() if r.get("backfilled")),
    }


def paper_text(summary: dict) -> str:
    """One line for the dashboard. Never shows an average on a small sample."""
    if not summary.get("start"):
        return "Paper trading belum mulai."
    since = time.strftime("%d %b %Y", time.localtime(summary["start"]))
    name = lambda r: f"{r['symbol'].replace('USDT', '')} {r['tf']} {'BUY' if r['direction'] > 0 else 'SELL'}"  # noqa: E731
    opened = summary["open"]
    parts = [f"Paper trading sejak {since} (5 kripto di 4 jam & 1 hari, Gold di 1 hari)"]
    parts.append(f"{len(opened)} posisi terbuka" + (f" ({', '.join(name(r) for r in opened)})" if opened else ""))
    n = summary["trades"]
    if n:
        parts.append(f"{n} trade selesai, {summary['wins']} untung, total {summary['total_r']:+.1f}R bersih")
    else:
        parts.append("belum ada trade selesai")
    if summary["missed"]:
        parts.append(f"{summary['missed']} entry tercatat belakangan (app tertutup saat sinyal muncul)")
    tail = "" if n >= PAPER_MIN_TRADES else f" Terlalu sedikit untuk dinilai (butuh ≥ {PAPER_MIN_TRADES} trade); pakai ini untuk melatih eksekusi, bukan membuktikan sinyal."
    return " · ".join(parts) + "." + tail


def _key(r: dict) -> tuple:
    return r.get("event"), r.get("symbol"), r.get("tf"), r.get("opened_t")


def _already_logged(path: Path, row: dict) -> bool:
    """Guards against a second app session logging the same event again."""
    if not path.exists():
        return False
    try:
        return any(_key(json.loads(line)) == _key(row) for line in path.read_text("utf-8").splitlines() if line.strip())
    except Exception:
        return False


def journal_stats(path: Path, symbol: str, tf_key: str) -> tuple[int, int, float]:
    """(trades, wins, average net R) of live-logged closed trades."""
    try:
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    except Exception:
        return 0, 0, 0.0
    net = [r["net_r"] for r in rows if r.get("event") == "close" and r.get("symbol") == symbol and r.get("tf") == tf_key]
    if not net:
        return 0, 0, 0.0
    return len(net), sum(1 for r in net if r > 0), sum(net) / len(net)


def engine_for(state: MarketState) -> SignalEngine:
    engine = getattr(state, "engine", None)
    if engine is None:
        feed = state.feed
        disabled = None
        if state.tf.seconds < feed.min_signal_seconds:
            allowed = SIGNAL_TIMEFRAMES_TEXT.get(feed.min_signal_seconds, "timeframe besar")
            disabled = (
                f"Sinyal hanya di timeframe {allowed}. Di timeframe kecil biaya dan noise terlalu "
                "besar: uji v1 di 15m rugi ±0,5R per trade setelah biaya. Chart dan skor konteks tetap tampil."
            )
        engine = SignalEngine(state.tf, state.cache_dir / "journal.jsonl", feed.symbol, feed.cost_pct, disabled)
        state.engine = engine
    return engine
