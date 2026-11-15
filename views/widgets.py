"""Reusable visual pieces for the dashboard."""

from __future__ import annotations

import time

import flet as ft
import flet.canvas as cv

from analysis import Snapshot

BG = "#0A0E15"
CARD = "#111722"
CARD_ALT = "#161E2C"
BORDER = "#1F2937"
TEXT = "#E6EAF2"
MUTED = "#8A94A6"
GREEN = "#22C55E"
RED = "#F05252"
AMBER = "#F5A524"
BLUE = "#5B8DEF"


def signal_color(signal: str) -> str:
    if "BUY" in signal:
        return GREEN
    if "SELL" in signal:
        return RED
    return AMBER


def fmt_price(value: float) -> str:
    return f"{value:,.2f}" if value < 10_000 else f"{value:,.1f}"


def ago(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} dtk lalu"
    if seconds < 3600:
        return f"{seconds // 60} mnt lalu"
    if seconds < 86400:
        return f"{seconds // 3600} jam lalu"
    return f"{seconds // 86400} hari lalu"


class Sparkline(ft.Container):
    """24h close-price line drawn on a canvas, resized to its container."""

    def __init__(self, height: int = 64):
        self._series: list[float] = []
        self._color = BLUE
        self._size = (0.0, float(height))
        self._canvas = cv.Canvas(expand=True, on_resize=self._on_resize, resize_interval=50)
        super().__init__(content=self._canvas, height=height)

    def _on_resize(self, e: cv.CanvasResizeEvent) -> None:
        self._size = (e.width, e.height)
        self._draw()
        self._canvas.update()

    def set_values(self, values: list[float], color: str) -> None:
        self._series = values
        self._color = color
        self._draw()

    def _draw(self) -> None:
        w, h = self._size
        vals = self._series
        if w <= 0 or len(vals) < 2:
            self._canvas.shapes = []
            return
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        pad = 4
        step = w / (len(vals) - 1)
        pts = [(i * step, pad + (hi - v) / span * (h - 2 * pad)) for i, v in enumerate(vals)]

        line = [cv.Path.MoveTo(*pts[0])] + [cv.Path.LineTo(x, y) for x, y in pts[1:]]
        area = line + [cv.Path.LineTo(w, h), cv.Path.LineTo(0, h), cv.Path.Close()]
        self._canvas.shapes = [
            cv.Path(
                area,
                paint=ft.Paint(
                    style=ft.PaintingStyle.FILL,
                    gradient=ft.PaintLinearGradient(
                        (0, 0), (0, h), [ft.Colors.with_opacity(0.28, self._color), ft.Colors.with_opacity(0.0, self._color)]
                    ),
                ),
            ),
            cv.Path(
                line,
                paint=ft.Paint(
                    style=ft.PaintingStyle.STROKE,
                    stroke_width=2,
                    color=self._color,
                    stroke_join=ft.StrokeJoin.ROUND,
                    stroke_cap=ft.StrokeCap.ROUND,
                ),
            ),
            cv.Circle(pts[-1][0], pts[-1][1], 3.5, paint=ft.Paint(color=self._color)),
        ]


class ScoreBar(ft.Container):
    """Diverging bar: fills left (red) for negative scores, right (green) for positive."""

    def __init__(self):
        self._left_fill = ft.Container(bgcolor=RED, border_radius=4, expand=1)
        self._left_gap = ft.Container(expand=1)
        self._right_fill = ft.Container(bgcolor=GREEN, border_radius=4, expand=1)
        self._right_gap = ft.Container(expand=1)
        row = ft.Row(
            [
                ft.Row([self._left_gap, self._left_fill], spacing=0, expand=1),
                ft.Container(width=2, bgcolor=MUTED),
                ft.Row([self._right_fill, self._right_gap], spacing=0, expand=1),
            ],
            spacing=0,
        )
        super().__init__(content=row, height=8, bgcolor=BORDER, border_radius=4)
        self.set_score(0)

    def set_score(self, score: int) -> None:
        mag = min(abs(score), 100)
        neg, pos = (mag, 0) if score < 0 else (0, mag)
        for fill, gap, value in ((self._left_fill, self._left_gap, neg), (self._right_fill, self._right_gap, pos)):
            fill.visible = value > 0
            gap.visible = value < 100
            fill.expand = max(value, 1)
            gap.expand = max(100 - value, 1)


def stat(label: str) -> tuple[ft.Container, ft.Text]:
    value = ft.Text("-", size=15, weight=ft.FontWeight.W_600, color=TEXT)
    box = ft.Container(
        ft.Column([ft.Text(label, size=11, color=MUTED), value], spacing=2, tight=True),
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        bgcolor=CARD_ALT,
        border_radius=10,
        expand=1,
    )
    return box, value


def badge(text: str, color: str) -> ft.Container:
    return ft.Container(
        ft.Row(
            [ft.Container(width=7, height=7, bgcolor=color, border_radius=4), ft.Text(text, size=11, color=color, weight=ft.FontWeight.W_600)],
            spacing=6,
            tight=True,
        ),
        padding=ft.Padding.symmetric(horizontal=9, vertical=4),
        bgcolor=ft.Colors.with_opacity(0.12, color),
        border_radius=20,
    )


class MarketCard(ft.Container):
    def __init__(self, short: str, label: str, source: str):
        self.snapshot: Snapshot | None = None
        self.error: str | None = None
        self._label = label

        self._status = ft.Container()
        self._price = ft.Text("—", size=30, weight=ft.FontWeight.BOLD, color=TEXT)
        self._change = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self._spark = Sparkline()

        self._signal = ft.Text("Memuat…", size=22, weight=ft.FontWeight.BOLD, color=MUTED)
        self._score = ft.Text("", size=13, color=MUTED)
        self._bar = ScoreBar()
        self._confirm_icon = ft.Icon(ft.Icons.SCHEDULE, size=14, color=MUTED)
        self._confirm = ft.Text("", size=12, color=MUTED)
        self._signal_box = ft.Container(
            ft.Column(
                [
                    ft.Row([self._signal, self._score], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    self._bar,
                    ft.Row([self._confirm_icon, self._confirm], spacing=6),
                ],
                spacing=10,
            ),
            padding=14,
            border_radius=12,
            bgcolor=CARD_ALT,
            border=ft.Border.all(1, BORDER),
        )

        rsi_box, self._rsi = stat("RSI 14")
        adx_box, self._adx = stat("ADX (kekuatan)")
        atr_box, self._atr = stat("ATR 15m")
        trend_box, self._trend = stat("Tren 1 jam")

        self._stop = ft.Text("-", size=14, weight=ft.FontWeight.W_600, color=RED)
        self._tp1 = ft.Text("-", size=14, weight=ft.FontWeight.W_600, color=GREEN)
        self._tp2 = ft.Text("-", size=14, weight=ft.FontWeight.W_600, color=GREEN)
        self._levels = ft.Container(
            ft.Row(
                [
                    ft.Column([ft.Text("Stop loss", size=11, color=MUTED), self._stop], spacing=2, expand=1),
                    ft.Column([ft.Text("Target 1", size=11, color=MUTED), self._tp1], spacing=2, expand=1),
                    ft.Column([ft.Text("Target 2", size=11, color=MUTED), self._tp2], spacing=2, expand=1),
                ]
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=10,
            border=ft.Border.all(1, BORDER),
            visible=False,
        )

        self._factors = ft.Column(spacing=6)
        self._reasons = ft.ExpansionTile(
            title=ft.Text("Kenapa sinyal ini?", size=13, color=TEXT),
            controls=[ft.Container(self._factors, padding=ft.Padding.only(left=4, right=4, bottom=8))],
            tile_padding=ft.Padding.symmetric(horizontal=4),
            icon_color=MUTED,
            collapsed_icon_color=MUTED,
            shape=ft.RoundedRectangleBorder(radius=10),
            collapsed_shape=ft.RoundedRectangleBorder(radius=10),
        )

        self._footer = ft.Text("", size=11, color=MUTED)
        self._error = ft.Container(
            ft.Row([ft.Icon(ft.Icons.WIFI_OFF, size=14, color=AMBER), ft.Text("", size=12, color=AMBER, expand=True)], spacing=6),
            visible=False,
        )

        avatar = ft.Container(
            ft.Text(short, size=12, weight=ft.FontWeight.BOLD, color=BG),
            width=40,
            height=40,
            border_radius=20,
            alignment=ft.Alignment.CENTER,
            bgcolor="#E8B931" if short == "XAU" else "#F7931A",
        )
        header = ft.Row(
            [
                avatar,
                ft.Column([ft.Text(label, size=16, weight=ft.FontWeight.W_600, color=TEXT), ft.Text(source, size=11, color=MUTED)], spacing=0, expand=True),
                self._status,
            ],
            spacing=12,
        )

        super().__init__(
            content=ft.Column(
                [
                    header,
                    ft.Row([self._price, self._change], vertical_alignment=ft.CrossAxisAlignment.END, spacing=10, wrap=True),
                    self._spark,
                    self._signal_box,
                    ft.Row([rsi_box, adx_box], spacing=8),
                    ft.Row([atr_box, trend_box], spacing=8),
                    self._levels,
                    self._reasons,
                    self._error,
                    self._footer,
                ],
                spacing=14,
            ),
            padding=18,
            bgcolor=CARD,
            border_radius=18,
            border=ft.Border.all(1, BORDER),
            col={"xs": 12, "md": 6},
        )

    # ------------------------------------------------------------------ data
    def show(self, snap: Snapshot) -> None:
        prev = self.snapshot
        self.snapshot = snap
        self.error = None
        self._error.visible = False

        live, conf = snap.live, snap.confirmed
        color = signal_color(live.signal)

        self._price.value = fmt_price(snap.price)
        if prev is not None and snap.price != prev.price:
            self._price.color = GREEN if snap.price > prev.price else RED
        else:
            self._price.color = TEXT
        up = snap.change_24h >= 0
        self._change.value = f"{'▲' if up else '▼'} {abs(snap.change_24h):.2f}%  24j"
        self._change.color = GREEN if up else RED
        self._spark.set_values(snap.spark, GREEN if up else RED)

        closed = snap.status == "CLOSED"
        self._signal.value = live.signal
        self._signal.color = MUTED if closed else color
        self._score.value = f"skor {live.score:+d}/100"
        self._bar.set_score(live.score)
        self._signal_box.border = ft.Border.all(1, ft.Colors.with_opacity(0.45, color) if not closed else BORDER)

        if closed:
            self._confirm_icon.icon, self._confirm_icon.color = ft.Icons.NIGHTLIGHT_ROUND, MUTED
            self._confirm.value = "Pasar tutup — sinyal dari sesi terakhir"
        elif conf.signal == live.signal:
            self._confirm_icon.icon, self._confirm_icon.color = ft.Icons.CHECK_CIRCLE, color
            self._confirm.value = "Terkonfirmasi di close candle 15m"
        else:
            self._confirm_icon.icon, self._confirm_icon.color = ft.Icons.SCHEDULE, AMBER
            self._confirm.value = f"Belum close — candle terakhir: {conf.signal} ({conf.score:+d})"

        self._rsi.value = f"{live.rsi:.1f}"
        self._rsi.color = RED if live.rsi > 70 else GREEN if live.rsi < 30 else TEXT
        self._adx.value = f"{live.adx:.0f} · {'sideways' if live.sideways else 'trending'}"
        self._atr.value = fmt_price(live.atr)
        self._trend.value = {1: "▲ Naik", -1: "▼ Turun", 0: "◆ Datar"}[live.trend_1h]
        self._trend.color = {1: GREEN, -1: RED, 0: MUTED}[live.trend_1h]

        if snap.levels and not closed:
            self._levels.visible = True
            self._stop.value = fmt_price(snap.levels.stop)
            self._tp1.value = fmt_price(snap.levels.tp1)
            self._tp2.value = fmt_price(snap.levels.tp2)
        else:
            self._levels.visible = False

        rows = []
        for f in sorted(live.factors, key=lambda f: -abs(f.points)):
            c = GREEN if f.points > 0 else RED if f.points < 0 else MUTED
            rows.append(
                ft.Row(
                    [
                        ft.Container(
                            ft.Text(f"{f.points:+.0f}" if f.points else "·", size=11, weight=ft.FontWeight.BOLD, color=c),
                            width=38,
                            alignment=ft.Alignment.CENTER,
                            padding=ft.Padding.symmetric(vertical=2),
                            bgcolor=ft.Colors.with_opacity(0.12, c),
                            border_radius=6,
                        ),
                        ft.Text(f.detail, size=12, color=TEXT, expand=True),
                    ],
                    spacing=10,
                )
            )
        self._factors.controls = rows
        self.tick()

    def show_error(self, message: str) -> None:
        self.error = message
        self._error.visible = True
        self._error.content.controls[1].value = (
            f"{message} — menampilkan data terakhir." if self.snapshot else message
        )
        if self.snapshot is None:
            self._signal.value = "Tidak ada data"
        self.tick()

    def tick(self) -> None:
        """Refresh time-dependent labels (status badge, 'x seconds ago')."""
        snap = self.snapshot
        if snap is None:
            self._status.content = badge("MEMUAT", MUTED)
            return
        age = time.time() - snap.quote_time
        status = snap.status if time.time() - snap.fetched_at < 60 else "DELAYED"
        if status == "LIVE":
            self._status.content = badge("LIVE", GREEN)
        elif status == "CLOSED":
            self._status.content = badge("PASAR TUTUP", MUTED)
        else:
            self._status.content = badge(f"TERTUNDA {int(age // 60)} mnt", AMBER)
        self._footer.value = (
            f"Harga {ago(age)} · disinkron {ago(time.time() - snap.fetched_at)} · {snap.latency_ms} ms"
        )
