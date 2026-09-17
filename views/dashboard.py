"""Flet dashboard for automatic XAU and BTC signal monitoring."""

import asyncio

import flet as ft

from models import MARKETS, MarketSignal, analyze_market


class MarketDashboard(ft.Column):
    def __init__(self):
        self._loading = False
        self._last_notified: dict[str, str] = {}
        self._status = ft.Text("Menunggu pembaruan market...")
        self._refresh_button = ft.Button(
            content="Perbarui sekarang",
            on_click=self._refresh,
        )
        self._market_controls: dict[str, dict[str, ft.Text]] = {}

        panels = [self._build_market_panel(symbol, label) for symbol, label in MARKETS]
        super().__init__(
            controls=[
                ft.Text("Analisis XAU & BTC", size=20, weight=ft.FontWeight.BOLD),
                self._status,
                *panels,
                self._refresh_button,
                ft.Text(
                    "Sinyal adalah analisis teknikal edukatif, bukan rekomendasi finansial."
                ),
            ],
            spacing=20,
        )

    def _build_market_panel(self, symbol: str, label: str) -> ft.Column:
        price = ft.Text(f"{label}: Harga -", size=18, weight=ft.FontWeight.BOLD)
        signal = ft.Text("Sinyal: menunggu...")
        indicator = ft.Text("Skor: - | RSI: -")
        details = ft.Text("Indikator: -")
        self._market_controls[symbol] = {
            "price": price,
            "signal": signal,
            "indicator": indicator,
            "details": details,
        }
        return ft.Column(controls=[price, signal, indicator, details])

    def start(self) -> None:
        self.page.appbar = ft.AppBar(title=ft.Text("Market Radar"))
        asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        while True:
            await self._refresh()
            await asyncio.sleep(30)

    async def _refresh(self, e=None) -> None:
        if self._loading:
            return

        self._loading = True
        self._refresh_button.disabled = True
        self._status.value = "Menganalisis XAU dan BTC..."
        self.page.update()

        try:
            results = await asyncio.gather(
                *(asyncio.to_thread(analyze_market, symbol, label) for symbol, label in MARKETS)
            )
            for result in results:
                self._show_result(result)
            self._status.value = "Data diperbarui setiap 30 detik."
        except Exception:
            self._status.value = "Tidak dapat mengambil data. Periksa koneksi internet lalu coba lagi."
        finally:
            self._loading = False
            self._refresh_button.disabled = False
            self.page.update()

    def _show_result(self, result: MarketSignal) -> None:
        controls = self._market_controls[result.symbol]
        controls["price"].value = f"{result.label}: {result.price:,.2f}"
        controls["signal"].value = f"Sinyal: {result.signal}"
        controls["signal"].color = self._signal_color(result.signal)
        controls["indicator"].value = f"Skor konsensus: {result.score:+d}/5 | RSI(14): {result.rsi:.1f}"
        controls["details"].value = f"Indikator: {result.details}"

        strong_signal = result.signal in {"STRONG BUY", "STRONG SELL"}
        if strong_signal and self._last_notified.get(result.symbol) != result.signal:
            self.page.show_dialog(
                ft.SnackBar(
                    content=ft.Text(
                        f"{result.label}: {result.signal} pada {result.price:,.2f}"
                    )
                )
            )
        self._last_notified[result.symbol] = result.signal

    @staticmethod
    def _signal_color(signal: str):
        if "BUY" in signal:
            return ft.Colors.GREEN
        if "SELL" in signal:
            return ft.Colors.RED
        return ft.Colors.AMBER
