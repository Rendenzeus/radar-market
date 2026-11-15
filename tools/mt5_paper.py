"""Forward-test signal v2 on a MetaTrader 5 DEMO account.

Follows the v2 rules from signals.run_strategy on the broker's own candles:

  - safety: the account is re-checked on EVERY pass; if the logged-in account is
    not a demo account, nothing is sent and the script stops
  - entries only when the script itself saw the candle close: the breakout candle
    must have become the last closed candle between two consecutive passes no more
    than FRESH_SECONDS apart, and the broker clock (learned from live ticks) must
    put that close less than FRESH_SECONDS ago. A signal found any later (script started after the
    close, terminal disconnected, laptop asleep) is logged as "missed"
  - one attempt per entry; a rejected order is logged once and then as "missed"
  - the 2 x ATR stop is placed on the order (the broker enforces it)
  - the trailing exit is close-based: the position is closed at market after the
    candle that closes beyond the trailing level
  - our positions are identified by the position ticket stored in the journal,
    never by the order comment; other positions are never touched
  - if the terminal disconnects, the script logs it once, keeps reconnecting and
    logs when it is back
  - repeated identical errors are logged once, not every pass
  - position size risks RISK_PCT of the balance between entry and stop

Every event is appended to an append-only journal (one JSON line per event,
never rewritten). Results use the real fills, swap and commission from the
account history: R = position profit / money at risk.

Needs the MT5 terminal running, logged in to a demo account, "Algo Trading" on.
Usage:
  python tools/mt5_paper.py            # loop
  python tools/mt5_paper.py --status   # print the journal summary
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import MetaTrader5 as mt5  # noqa: E402

from data import Candle  # noqa: E402
from signals import TREND_EMA, run_strategy  # noqa: E402

SYMBOL = "XAUUSD"
TIMEFRAMES = {"4h": (mt5.TIMEFRAME_H4, 4 * 3600, 240004), "1d": (mt5.TIMEFRAME_D1, 86400, 240001)}  # key: (MT5 tf, seconds, magic)
RISK_PCT = 0.005
BARS = 1500
POLL_SECONDS = 20
FRESH_SECONDS = 120  # max gap between the pass before and the pass after a close
JOURNAL = Path.home() / ".market-radar" / "mt5_paper.jsonl"


class NotDemo(Exception):
    pass


# ------------------------------------------------------------------ journal
def rows() -> list[dict]:
    if not JOURNAL.exists():
        return []
    return [json.loads(line) for line in JOURNAL.read_text("utf-8").splitlines() if line.strip()]


def log(event: str, **fields) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    row = {"event": event, "logged_at": int(time.time()), **fields}
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), event, fields, flush=True)


_last_error: dict[tuple, object] = {}


def log_error(key: tuple, **fields) -> None:
    """Log an error only when it differs from the last one with the same key."""
    sig = (fields.get("retcode"), fields.get("message"))
    if _last_error.get(key) == sig:
        return
    _last_error[key] = sig
    log("error", **fields)


def start_time() -> int:
    for r in rows():
        if r["event"] == "start":
            return r["t"]
    t = int(time.time())
    log("start", t=t, symbol=SYMBOL, risk_pct=RISK_PCT, version=2)
    return t


def logged(event: str, tf: str, opened_t: int) -> bool:
    return any(r["event"] == event and r.get("tf") == tf and r.get("opened_t") == opened_t for r in rows())


def open_rows(tf: str) -> list[dict]:
    """Our positions according to the journal: opened and not yet recorded as closed."""
    rs = [r for r in rows() if r.get("tf") == tf]
    done = {r["opened_t"] for r in rs if r["event"] == "close"}
    return [r for r in rs if r["event"] == "open" and r["opened_t"] not in done]


# ------------------------------------------------------------------ MT5
def ensure_connected(state: dict) -> bool:
    """(Re)connect; log a disconnect once and the reconnect once."""
    term = mt5.terminal_info()
    if term is None or not term.connected:
        mt5.shutdown()
        ok = mt5.initialize()
        term = mt5.terminal_info() if ok else None
    up = bool(term and term.connected and mt5.account_info() is not None)
    if not up and state.get("up", True):
        log("disconnected", message=str(mt5.last_error()))
    if up and not state.get("up", True):
        log("reconnected")
    state["up"] = up
    if up:
        mt5.symbol_select(SYMBOL, True)
    return up


def require_demo() -> None:
    account = mt5.account_info()
    if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise NotDemo("akun yang login bukan akun demo" if account else "tidak ada akun yang login")


def trading_enabled() -> bool:
    term = mt5.terminal_info()
    return bool(term and term.trade_allowed)


def server_offset(state: dict) -> int | None:
    """Broker clock minus local clock, rounded to 30 min; learned from a fresh tick.

    Candle times are in the broker's server time. The offset is only (re)learned
    while quotes are live, so a closed market keeps the last known value.
    """
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is not None and tick.time:
        guess = round((tick.time - time.time()) / 1800) * 1800
        if abs(time.time() + guess - tick.time) < 120:
            state["offset"] = guess
    return state.get("offset")


def closed_candles(tf: int, seconds: int, server_now: float | None) -> list[Candle] | None:
    """Closed candles only. With a known server clock, the newest bar counts as closed
    once its period is over (e.g. Friday's bars over a weekend); without it, the newest
    bar is always treated as forming."""
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, BARS + 1)
    if rates is None or not len(rates):
        return None
    candles = [Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["tick_volume"])) for r in rates]
    if server_now is None:
        return candles[:-1]
    return [c for c in candles if c.t + seconds <= server_now]


def position(ticket: int):
    found = mt5.positions_get(ticket=ticket)
    return found[0] if found else None


def lot_size(risk_price: float) -> float:
    info = mt5.symbol_info(SYMBOL)
    balance = mt5.account_info().balance
    money_per_lot = risk_price / info.trade_tick_size * info.trade_tick_value
    lots = math.floor(balance * RISK_PCT / money_per_lot / info.volume_step) * info.volume_step
    return round(min(max(lots, info.volume_min), info.volume_max), 2)


def send(request: dict):
    require_demo()  # checked right before every order
    result = None
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        result = mt5.order_send({**request, "type_filling": filling})
        if result is None or result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            return result
    return result


def open_position(tf_key: str, magic: int, plan) -> None:
    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    buy = plan.direction > 0
    result = send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot_size(plan.risk),
        "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if buy else tick.bid,
        "sl": round(plan.stop, info.digits),
        "deviation": 50,
        "magic": magic,
        "comment": f"v2 {tf_key}",
        "type_time": mt5.ORDER_TIME_GTC,
    })
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log("error", tf=tf_key, opened_t=plan.opened_t, action="open",
            retcode=None if result is None else result.retcode,
            message=str(mt5.last_error() if result is None else result.comment))
        log("missed", tf=tf_key, opened_t=plan.opened_t, direction=plan.direction, note="order ditolak, tidak diulang")
        return
    pos = next((p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.identifier == result.order), None)
    ticket = pos.ticket if pos else result.order
    risk_money = abs(result.price - plan.stop) / info.trade_tick_size * info.trade_tick_value * result.volume
    log("open", tf=tf_key, opened_t=plan.opened_t, position=ticket, direction=plan.direction,
        signal_entry=plan.entry, fill=result.price, stop=plan.stop, volume=result.volume, risk_money=round(risk_money, 2))


def close_position(tf_key: str, row: dict, pos) -> None:
    tick = mt5.symbol_info_tick(SYMBOL)
    buy = pos.type == mt5.POSITION_TYPE_BUY
    result = send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
        "position": pos.ticket,
        "price": tick.bid if buy else tick.ask,
        "deviation": 50,
        "magic": pos.magic,
        "comment": f"v2 {tf_key} TRAIL",
        "type_time": mt5.ORDER_TIME_GTC,
    })
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        # retried every pass (e.g. market closed), but logged once per distinct error
        log_error(("close", pos.ticket), tf=tf_key, opened_t=row["opened_t"], action="close", position=pos.ticket,
                  retcode=None if result is None else result.retcode,
                  message=str(mt5.last_error() if result is None else result.comment))
        return
    log("exit_sent", tf=tf_key, opened_t=row["opened_t"], position=pos.ticket, reason="TRAIL", fill=result.price)


def record_closed(tf_key: str, row: dict) -> bool:
    """Write the 'close' row (real money result) once the position is gone. True if recorded."""
    deals = mt5.history_deals_get(position=row["position"]) or []
    exits = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
    if not exits:
        return False
    profit = sum(d.profit + d.swap + d.commission + d.fee for d in deals)
    reason = "SL" if any(d.reason == mt5.DEAL_REASON_SL for d in exits) else "TRAIL"
    log("close", tf=tf_key, opened_t=row["opened_t"], position=row["position"], reason=reason,
        exit_fill=exits[-1].price, profit=round(profit, 2), result_r=round(profit / row["risk_money"], 3))
    return True


# ------------------------------------------------------------------ loop
def step(start: int, state: dict) -> None:
    now = time.time()
    if not ensure_connected(state):
        state["prev"] = {}  # a gap breaks freshness: closes seen after reconnecting are not "live"
        return
    require_demo()
    prev_pass = state.get("prev_pass", 0.0)
    state["prev_pass"] = now
    contiguous = now - prev_pass <= FRESH_SECONDS
    offset = server_offset(state)
    server_now = None if offset is None else now + offset

    for tf_key, (tf, seconds, magic) in TIMEFRAMES.items():
        candles = closed_candles(tf, seconds, server_now)
        if candles is None:
            log_error(("data", tf_key), tf=tf_key, action="data", message=str(mt5.last_error()))
            state["prev"].pop(tf_key, None)
            continue
        _last_error.pop(("data", tf_key), None)
        last_t = candles[-1].t
        previous_last = state["prev"].get(tf_key)
        state["prev"][tf_key] = last_t
        if len(candles) <= TREND_EMA:
            continue
        _, plan = run_strategy(candles, 0.0)

        # Our positions (by journal ticket). Close the ones the rules no longer hold.
        for row in open_rows(tf_key):
            pos = position(row["position"])
            if pos is None:
                record_closed(tf_key, row)
            elif plan is None or plan.opened_t != row["opened_t"]:
                close_position(tf_key, row, pos)

        # Entry: a breakout that became the last closed candle between two close-together passes.
        if plan is None or plan.opened_t + seconds < start or logged("open", tf_key, plan.opened_t):
            continue
        if logged("missed", tf_key, plan.opened_t) or open_rows(tf_key):
            continue
        # Seen live: the close happened between two close-together passes, and the broker clock
        # confirms it was moments ago (unknown clock: no entries at all).
        seen_live = (
            plan.opened_t == last_t
            and previous_last is not None
            and previous_last != last_t
            and contiguous
            and server_now is not None
            and server_now - (last_t + seconds) <= FRESH_SECONDS
        )
        if not seen_live:
            log("missed", tf=tf_key, opened_t=plan.opened_t, direction=plan.direction,
                note="close candle tidak terlihat langsung oleh skrip (baru jalan, terputus, atau laptop tidur)")
            continue
        if not trading_enabled():
            log("missed", tf=tf_key, opened_t=plan.opened_t, direction=plan.direction, note="Algo Trading di MT5 mati")
            continue
        open_position(tf_key, magic, plan)


def status() -> None:
    rs = rows()
    start = next((r["t"] for r in rs if r["event"] == "start"), None)
    if start is None:
        print("Belum mulai.")
        return
    closes = [r for r in rs if r["event"] == "close"]
    n_open = sum(len(open_rows(tf)) for tf in TIMEFRAMES)
    print(f"Mulai {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))} · {n_open} posisi terbuka · "
          f"{len(closes)} selesai · total {sum(r['result_r'] for r in closes):+.2f}R · "
          f"{sum(1 for r in rs if r['event'] == 'missed')} terlewat · {sum(1 for r in rs if r['event'] == 'error')} error · "
          f"{sum(1 for r in rs if r['event'] == 'disconnected')} kali terputus")
    for r in closes:
        print(f"  {r['tf']} {time.strftime('%Y-%m-%d', time.gmtime(r['opened_t']))} {r['reason']} {r['result_r']:+.2f}R")


def main() -> None:
    if "--status" in sys.argv:
        status()
        return
    if not mt5.initialize():
        raise SystemExit(f"MT5 tidak bisa dihubungi: {mt5.last_error()}")
    try:
        require_demo()
    except NotDemo as exc:
        mt5.shutdown()
        raise SystemExit(f"DIHENTIKAN: {exc}. Skrip ini hanya untuk akun demo.")
    account = mt5.account_info()
    print(f"Terhubung: akun demo {account.login} @ {account.server}", flush=True)
    start = start_time()
    state: dict = {"prev": {}, "up": True}
    while True:
        try:
            step(start, state)
        except NotDemo as exc:
            log("stopped", message=str(exc))
            mt5.shutdown()
            raise SystemExit(f"DIHENTIKAN: {exc}. Tidak ada order yang dikirim.")
        except Exception as exc:
            log_error(("loop",), action="loop", message=repr(exc))
            state["prev"] = {}
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
