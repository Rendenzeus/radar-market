"""Market Radar dashboard: one independent refresh loop per market."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

import flet as ft

from analysis import build_snapshot
from data import FEEDS, MarketState, default_cache_dir
from views.widgets import BG, BORDER, MUTED, TEXT, MarketCard, signal_color

CACHE_SAVE_SECONDS = 5 * 60
CLOSED_MARKET_REFRESH = 5 * 60


class MarketDashboard(ft.Column):
    def __init__(self):
        cache_dir = default_cache_dir()
        self._states = [MarketState(feed, cache_dir) for feed in FEEDS]
        self._cards = {s.feed.symbol: MarketCard(s.feed.short, s.feed.label, s.feed.source) for s in self._states}
        self._busy: set[str] = set()
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
                        ft.Text("Sinyal teknikal XAU & BTC · timeframe 15m + konfirmasi 1j", size=12, color=MUTED),
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
    def start(self) -> None:
        # Render cached candles immediately so the app is useful before the network answers.
        for state in self._states:
            if state.candles:
                try:
                    self._cards[state.feed.symbol].show(build_snapshot(state))
                except Exception:
                    pass
        self.page.update()
        for state in self._states:
            self._wake[state.feed.symbol] = asyncio.Event()
            self.page.run_task(self._market_loop, state)
        self.page.run_task(self._ticker)

    async def _market_loop(self, state: MarketState) -> None:
        wake = self._wake[state.feed.symbol]
        while True:
            await self._refresh_one(state)
            card = self._cards[state.feed.symbol]
            delay = state.feed.refresh_seconds
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
        if symbol in self._busy:
            return
        self._busy.add(symbol)
        card = self._cards[symbol]
        try:
            await asyncio.to_thread(state.refresh)
            snap = await asyncio.to_thread(build_snapshot, state)
            card.show(snap)
            self._maybe_alert(snap)
        except Exception as exc:
            card.show_error(_friendly_error(exc))
        finally:
            self._busy.discard(symbol)
        card.update()

        if time.time() - self._last_save > CACHE_SAVE_SECONDS:
            self._last_save = time.time()
            for s in self._states:
                await asyncio.to_thread(s.save_cache)

    async def _ticker(self) -> None:
        while True:
            self._clock.value = datetime.now().strftime("%H:%M:%S")
            self._clock.update()
            for card in self._cards.values():
                card.tick()
                card.update()
            await asyncio.sleep(1)

    async def _refresh_all(self, e=None) -> None:
        for state in self._states:
            state.last_full_sync = 0  # force a clean resync on manual refresh
            self._wake[state.feed.symbol].set()

    # --------------------------------------------------------------- alerts
    def _maybe_alert(self, snap) -> None:
        """Alert only on signals confirmed by a closed candle, so they never repaint."""
        symbol = snap.feed.symbol
        signal = snap.confirmed.signal
        previous = self._last_alert.get(symbol)
        self._last_alert[symbol] = signal
        if previous is None or previous == signal or snap.status == "CLOSED":
            return
        if signal in {"STRONG BUY", "STRONG SELL"}:
            self.page.show_dialog(
                ft.SnackBar(
                    ft.Text(f"{snap.feed.label}: {signal} di {snap.price:,.2f} (skor {snap.confirmed.score:+d})", color=BG),
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
