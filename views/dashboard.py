"""Market Radar dashboard: one independent refresh loop per market."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

import flet as ft

from analysis import build_snapshot
from data import FEEDS, TIMEFRAMES, Feed, MarketState, default_cache_dir
from views.widgets import BG, BORDER, MUTED, TEXT, MarketCard, signal_color

CACHE_SAVE_SECONDS = 5 * 60
CLOSED_MARKET_REFRESH = 5 * 60


class MarketDashboard(ft.Column):
    def __init__(self):
        self._cache_dir = default_cache_dir()
        self._feeds = {feed.symbol: feed for feed in FEEDS}
        self._states: dict[tuple[str, str], MarketState] = {}
        self._cards = {
            feed.symbol: MarketCard(feed.short, feed.label, feed.source, on_timeframe=self._on_timeframe)
            for feed in FEEDS
        }
        self._busy: set[tuple[str, str]] = set()
        self._last_alert: dict[str, str] = {}
        self._last_save = time.time()
        self._wake: dict[str, asyncio.Event] = {}

        self._clock = ft.Text("", size=12, color=MUTED)
        self._refresh_btn = ft.IconButton(ft.Icons.REFRESH, icon_color=TEXT, tooltip="Perbarui sekarang", on_click=self._refresh_all)

        header = ft.Row(
            [
                ft.Column(
                    [
                        ft.Text("Market Radar", size=22, weight=ft.FontWeight.BOLD, color=TEXT),
                        ft.Text("Sinyal teknikal XAU & BTC · pilih timeframe 1m–1D di tiap kartu", size=12, color=MUTED),
                    ],
                    spacing=2,
                    expand=True,
                ),
                self._clock,
                self._refresh_btn,
            ]
        )
        disclaimer = ft.Container(
            ft.Text(
                "Analisis teknikal edukatif, bukan rekomendasi finansial. Gold memakai harga futures COMEX "
                "(GC=F) dari Yahoo yang bisa tertunda ±10 menit; BTC real-time dari Binance. "
                "Stop/target dihitung dari ATR (1,5× dan 3×).",
                size=11,
                color=MUTED,
            ),
            padding=ft.Padding.only(top=6, bottom=24),
            border=ft.Border.only(top=ft.BorderSide(1, BORDER)),
        )
        super().__init__(
            controls=[header, ft.ResponsiveRow(list(self._cards.values()), spacing=16, run_spacing=16), disclaimer],
            spacing=18,
        )

    # ----------------------------------------------------------- lifecycle
    def _state(self, symbol: str) -> MarketState:
        """MarketState for the timeframe currently selected on the symbol's card."""
        key = (symbol, self._cards[symbol].timeframe)
        if key not in self._states:
            self._states[key] = MarketState(self._feeds[symbol], TIMEFRAMES[key[1]], self._cache_dir)
        return self._states[key]

    def _show_cached(self, symbol: str) -> None:
        state = self._state(symbol)
        if state.candles:
            try:
                self._cards[symbol].show(build_snapshot(state))
            except Exception:
                pass

    def start(self) -> None:
        # Render cached candles immediately so the app is useful before the network answers.
        for symbol in self._feeds:
            self._show_cached(symbol)
        self.page.update()
        self._loop = asyncio.get_running_loop()
        for symbol in self._feeds:
            self._wake[symbol] = asyncio.Event()
            self.page.run_task(self._market_loop, symbol)
        self.page.run_task(self._ticker)

    def _on_timeframe(self, card: MarketCard, key: str) -> None:
        symbol = next(s for s, c in self._cards.items() if c is card)
        self._show_cached(symbol)
        card.update()
        # Click handlers may run on a worker thread; asyncio.Event is not thread-safe.
        self._loop.call_soon_threadsafe(self._wake[symbol].set)

    async def _market_loop(self, symbol: str) -> None:
        wake = self._wake[symbol]
        feed: Feed = self._feeds[symbol]
        card = self._cards[symbol]
        while True:
            await self._refresh_one(self._state(symbol))
            delay = feed.refresh_seconds
            if card.snapshot and card.snapshot.status == "CLOSED":
                delay = CLOSED_MARKET_REFRESH
            elif card.error:
                delay = min(30, delay * 3)
            try:
                await asyncio.wait_for(wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            wake.clear()

    async def _refresh_one(self, state: MarketState) -> None:
        symbol = state.feed.symbol
        key = (symbol, state.tf.key)
        if key in self._busy:
            return
        self._busy.add(key)
        card = self._cards[symbol]
        try:
            await asyncio.to_thread(state.refresh)
            snap = await asyncio.to_thread(build_snapshot, state)
            if card.timeframe == state.tf.key:  # user may have switched meanwhile
                card.show(snap)
            self._maybe_alert(snap)
        except Exception as exc:
            if card.timeframe == state.tf.key:
                card.show_error(_friendly_error(exc))
        finally:
            self._busy.discard(key)
        card.update()

        if time.time() - self._last_save > CACHE_SAVE_SECONDS:
            self._last_save = time.time()
            for s in list(self._states.values()):
                await asyncio.to_thread(s.save_cache)

    async def _ticker(self) -> None:
        while True:
            self._clock.value = datetime.now().strftime("%H:%M:%S")
            self._clock.update()
            for card in self._cards.values():
                card.tick()
                card.update()
            await asyncio.sleep(1)

    def _refresh_all(self, e=None) -> None:
        for symbol in self._feeds:
            self._state(symbol).last_full_sync = 0  # force a clean resync on manual refresh
            self._loop.call_soon_threadsafe(self._wake[symbol].set)

    # --------------------------------------------------------------- alerts
    def _maybe_alert(self, snap) -> None:
        """Alert only on signals confirmed by a closed candle, so they never repaint."""
        symbol = (snap.feed.symbol, snap.tf.key)
        signal = snap.confirmed.signal
        previous = self._last_alert.get(symbol)
        self._last_alert[symbol] = signal
        if previous is None or previous == signal or snap.status == "CLOSED":
            return
        if signal in {"STRONG BUY", "STRONG SELL"}:
            self.page.show_dialog(
                ft.SnackBar(
                    ft.Text(f"{snap.feed.label} {snap.tf.key}: {signal} di {snap.price:,.2f} (skor {snap.confirmed.score:+d})", color=BG),
                    bgcolor=signal_color(signal),
                    duration=ft.Duration(seconds=8),
                )
            )


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "429" in text:
        return "Sumber data membatasi permintaan (429)"
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "Koneksi timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "Tidak ada koneksi ke sumber data"
    return text or exc.__class__.__name__
