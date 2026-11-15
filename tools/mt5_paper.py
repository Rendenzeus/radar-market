"""Forward-test signal v2 on a MetaTrader 5 DEMO account.

Refuses to run unless the logged-in account is a demo account. Follows the v2
rules from signals.run_strategy on the broker's own candles:

  - a new entry is sent only when the breakout candle is the candle that has just
    closed (a signal found late, e.g. after the script was off, is logged as
    "missed" and not traded, so fills stay comparable with the rules)
  - the 2 x ATR stop is placed on the order (the broker enforces it)
  - the trailing exit is close-based, so the script closes the position at market
    right after the candle that closes beyond the trailing level
  - position size risks RISK_PCT of the balance between entry and stop

Every event is appended to an append-only journal (one JSON line per event,
never rewritten). Results use the real fills, swap and commission from the
account history: R = position profit / money at risk.

Needs the MT5 terminal running, logged in, with "Algo Trading" switched on.
Usage:
  python tools/mt5_paper.py            # loop: check every 30 s
  python tools/mt5_paper.py --once     # one pass, then exit
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
from signals import run_strategy  # noqa: E402

SYMBOL = "XAUUSD"
TIMEFRAMES = {"4h": (mt5.TIMEFRAME_H4, 4 * 3600, 240004), "1d": (mt5.TIMEFRAME_D1, 86400, 240001)}  # key: (MT5 tf, seconds, magic)
RISK_PCT = 0.005
COST_PCT = 0.0003  # only used for the strategy's own R bookkeeping; real costs come from the account
BARS = 1500
POLL_SECONDS = 30
JOURNAL = Path.home() / ".market-radar" / "mt5_paper.jsonl"


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
    print(time.strftime("%Y-%m-%d %H:%M:%S"), event, {k: v for k, v in fields.items() if k != "plan"})


def start_time() -> int:
    for r in rows():
        if r["event"] == "start":
            return r["t"]
    t = int(time.time())
    log("start", t=t, symbol=SYMBOL, risk_pct=RISK_PCT, version=2)
    return t


def logged(event: str, tf: str, opened_t: int) -> bool:
    return any(r["event"] == event and r.get("tf") == tf and r.get("opened_t") == opened_t for r in rows())


# ------------------------------------------------------------------ MT5
def connect() -> None:
    if not mt5.initialize():
        raise SystemExit(f"MT5 tidak bisa dihubungi: {mt5.last_error()}")
    account = mt5.account_info()
    if account is None:
        raise SystemExit("Belum ada akun yang login di MT5.")
    if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        mt5.shutdown()
        raise SystemExit(f"DIHENTIKAN: akun {account.login} di {account.server} bukan akun demo. Skrip ini hanya untuk demo.")
    if not mt5.symbol_select(SYMBOL, True):
        raise SystemExit(f"Simbol {SYMBOL} tidak tersedia: {mt5.last_error()}")
    print(f"Terhubung: akun demo {account.login} @ {account.server}, saldo {account.balance:,.2f} {account.currency}")


def trading_enabled() -> bool:
    term = mt5.terminal_info()
    return bool(term and term.trade_allowed)


def closed_candles(tf: int) -> list[Candle]:
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 1, BARS)  # position 0 is the forming candle
    if rates is None:
        return []
    return [Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["tick_volume"])) for r in rates]


def my_positions(magic: int) -> list:
    return [p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == magic]


def lot_size(risk_price: float) -> float:
    info = mt5.symbol_info(SYMBOL)
    balance = mt5.account_info().balance
    money_per_lot = risk_price / info.trade_tick_size * info.trade_tick_value
    lots = math.floor(balance * RISK_PCT / money_per_lot / info.volume_step) * info.volume_step
    return round(min(max(lots, info.volume_min), info.volume_max), 2)


def send(request: dict) -> object:
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        result = mt5.order_send({**request, "type_filling": filling})
        if result is None or result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            return result
    return result


def open_position(tf_key: str, magic: int, plan) -> None:
    tick = mt5.symbol_info_tick(SYMBOL)
    buy = plan.direction > 0
    volume = lot_size(plan.risk)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if buy else tick.bid,
        "sl": round(plan.stop, mt5.symbol_info(SYMBOL).digits),
        "deviation": 50,
        "magic": magic,
        "comment": f"v2 {tf_key} {plan.opened_t}",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    result = send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log("error", tf=tf_key, opened_t=plan.opened_t, action="open",
            retcode=None if result is None else result.retcode, message=str(mt5.last_error() if result is None else result.comment))
        return
    risk_money = abs(result.price - plan.stop) / mt5.symbol_info(SYMBOL).trade_tick_size * mt5.symbol_info(SYMBOL).trade_tick_value * result.volume
    log("open", tf=tf_key, opened_t=plan.opened_t, position=result.order, direction=plan.direction,
        signal_entry=plan.entry, fill=result.price, stop=plan.stop, volume=result.volume, risk_money=round(risk_money, 2))


def close_position(tf_key: str, pos, reason: str, opened_t: int | None) -> None:
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
        "comment": f"v2 {tf_key} {reason}",
        "type_time": mt5.ORDER_TIME_GTC,
    })
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log("error", tf=tf_key, opened_t=opened_t, action="close", position=pos.ticket,
            retcode=None if result is None else result.retcode, message=str(mt5.last_error() if result is None else result.comment))
        return
    log("exit_sent", tf=tf_key, opened_t=opened_t, position=pos.ticket, reason=reason, fill=result.price)


def record_closed(tf_key: str) -> None:
    """Write a 'close' row with real money results for positions that are gone."""
    opens = [r for r in rows() if r["event"] == "open" and r["tf"] == tf_key]
    for r in opens:
        if logged("close", tf_key, r["opened_t"]):
            continue
        if any(p.ticket == r["position"] for p in (mt5.positions_get(symbol=SYMBOL) or [])):
            continue
        deals = mt5.history_deals_get(position=r["position"]) or []
        exits = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not exits:
            continue
        profit = sum(d.profit + d.swap + d.commission + d.fee for d in deals)
        reason = "SL" if any(d.reason == mt5.DEAL_REASON_SL for d in exits) else "TRAIL"
        log("close", tf=tf_key, opened_t=r["opened_t"], position=r["position"], reason=reason,
            exit_fill=exits[-1].price, profit=round(profit, 2), result_r=round(profit / r["risk_money"], 3))


# ------------------------------------------------------------------ loop
def step(start: int) -> None:
    for tf_key, (tf, seconds, magic) in TIMEFRAMES.items():
        candles = closed_candles(tf)
        if len(candles) < 260:
            continue
        _, plan = run_strategy(candles, COST_PCT)
        positions = my_positions(magic)
        last_t = candles[-1].t

        # Exit: we hold a position but the rules no longer do (trailing exit on the last close).
        for pos in positions:
            opened_t = int(pos.comment.split()[-1]) if pos.comment.split()[-1].isdigit() else None
            if plan is None or plan.opened_t != opened_t:
                close_position(tf_key, pos, "TRAIL", opened_t)
        record_closed(tf_key)

        # Entry: only a breakout on the candle that just closed, and only after the start.
        if plan is None or plan.opened_t + seconds < start or logged("open", tf_key, plan.opened_t):
            continue
        if my_positions(magic):
            continue
        if plan.opened_t != last_t:
            if not logged("missed", tf_key, plan.opened_t):
                log("missed", tf=tf_key, opened_t=plan.opened_t, direction=plan.direction,
                    note="sinyal ditemukan terlambat (skrip mati saat candle close), tidak dieksekusi")
            continue
        if not trading_enabled():
            if not logged("blocked", tf_key, plan.opened_t):
                log("blocked", tf=tf_key, opened_t=plan.opened_t, note="Algo Trading di MT5 mati; nyalakan tombolnya")
            continue
        open_position(tf_key, magic, plan)


def status() -> None:
    rs = rows()
    start = next((r["t"] for r in rs if r["event"] == "start"), None)
    if start is None:
        print("Belum mulai.")
        return
    closes = [r for r in rs if r["event"] == "close"]
    opens = {(r["tf"], r["opened_t"]) for r in rs if r["event"] == "open"}
    done = {(r["tf"], r["opened_t"]) for r in closes}
    print(f"Mulai {time.strftime('%Y-%m-%d %H:%M', time.localtime(start))} · {len(opens - done)} posisi terbuka · "
          f"{len(closes)} selesai · total {sum(r['result_r'] for r in closes):+.2f}R · "
          f"{sum(1 for r in rs if r['event'] == 'missed')} terlewat · {sum(1 for r in rs if r['event'] == 'error')} error")
    for r in closes:
        print(f"  {r['tf']} {time.strftime('%Y-%m-%d', time.gmtime(r['opened_t']))} {r['reason']} {r['result_r']:+.2f}R")


def main() -> None:
    if "--status" in sys.argv:
        status()
        return
    connect()
    start = start_time()
    if not trading_enabled():
        print("Peringatan: Algo Trading di MT5 sedang mati. Sinyal tetap dicatat, order tidak dikirim.")
    if "--once" in sys.argv:
        step(start)
        return
    last_check = 0
    while True:
        try:
            if time.time() - last_check >= POLL_SECONDS:
                step(start)
                last_check = time.time()
        except Exception as exc:  # keep running; MT5 reconnects on its own
            log("error", action="loop", message=repr(exc))
        time.sleep(5)


if __name__ == "__main__":
    main()
