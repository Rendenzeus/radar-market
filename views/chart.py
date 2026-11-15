"""Candlestick / line price chart drawn on a Flet canvas."""

from __future__ import annotations

from datetime import datetime

import flet as ft
import flet.canvas as cv

from data import Candle

GREEN = "#22C55E"
RED = "#F05252"
MUTED = "#8A94A6"
GRID = "#1C2433"
EMA20_COLOR = "#5B8DEF"
EMA50_COLOR = "#F5A524"

AXIS_W = 62  # right gutter for price labels
TIME_H = 18  # bottom gutter for time labels
TOP_PAD = 8
MIN_STEP = 7  # px per candle; decides how many candles fit the width


def _fmt(v: float) -> str:
    return f"{v:,.2f}" if v < 10_000 else f"{v:,.0f}"


def _fmt_time(ts: int, seconds: int) -> str:
    dt = datetime.fromtimestamp(ts)
    if seconds >= 86400:
        return dt.strftime("%d %b %y")
    if seconds >= 3600:
        return dt.strftime("%d %b %H:%M")
    return dt.strftime("%H:%M")


class PriceChart(ft.Container):
    def __init__(self, height: int = 250):
        self.mode = "candle"
        self._candles: list[Candle] = []
        self._ema20: list[float | None] = []
        self._ema50: list[float | None] = []
        self._price: float | None = None
        self._tf_seconds = 900
        self._size = (0.0, float(height))
        self._canvas = cv.Canvas(expand=True, on_resize=self._on_resize, resize_interval=50)
        super().__init__(content=self._canvas, height=height)

    def _on_resize(self, e: cv.CanvasResizeEvent) -> None:
        self._size = (e.width, e.height)
        self._draw()
        self._canvas.update()

    def set_data(self, candles, ema20, ema50, price: float, tf_seconds: int) -> None:
        self._candles, self._ema20, self._ema50 = candles, ema20, ema50
        self._price = price
        self._tf_seconds = tf_seconds
        self._draw()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._draw()

    # ----------------------------------------------------------------- drawing
    def _draw(self) -> None:
        w, h = self._size
        plot_w = w - AXIS_W
        plot_h = h - TIME_H - TOP_PAD
        if plot_w <= 40 or len(self._candles) < 2:
            self._canvas.shapes = []
            return

        n = max(20, min(len(self._candles), int(plot_w // MIN_STEP)))
        candles = self._candles[-n:]
        ema20 = self._ema20[-n:]
        ema50 = self._ema50[-n:]
        step = plot_w / n

        if self.mode == "candle":
            hi = max(c.h for c in candles)
            lo = min(c.l for c in candles)
        else:
            hi = max(c.c for c in candles)
            lo = min(c.c for c in candles)
        for series in (ema20, ema50):
            vals = [v for v in series if v is not None]
            if vals:
                hi, lo = max(hi, max(vals)), min(lo, min(vals))
        pad = (hi - lo) * 0.06 or hi * 0.001 or 1.0
        hi, lo = hi + pad, lo - pad
        span = hi - lo

        def y(v: float) -> float:
            return TOP_PAD + (hi - v) / span * plot_h

        def x(i: int) -> float:
            return i * step + step / 2

        shapes: list[cv.Shape] = []
        label_style = ft.TextStyle(size=10, color=MUTED)

        # Grid + price labels
        for k in range(5):
            v = lo + span * k / 4
            yy = y(v)
            shapes.append(cv.Line(0, yy, plot_w, yy, paint=ft.Paint(color=GRID, stroke_width=1)))
            shapes.append(cv.Text(plot_w + 6, yy - 7, _fmt(v), style=label_style))

        # Time labels (start, middle, end)
        for i in (0, n // 2, n - 1):
            shapes.append(
                cv.Text(
                    min(max(x(i) - 28, 0), plot_w - 56),
                    h - TIME_H + 4,
                    _fmt_time(candles[i].t, self._tf_seconds),
                    style=label_style,
                )
            )

        if self.mode == "candle":
            body_w = max(1.0, step * 0.62)
            for i, c in enumerate(candles):
                color = GREEN if c.c >= c.o else RED
                cx = x(i)
                shapes.append(cv.Line(cx, y(c.h), cx, y(c.l), paint=ft.Paint(color=color, stroke_width=1)))
                top, bottom = y(max(c.o, c.c)), y(min(c.o, c.c))
                shapes.append(
                    cv.Rect(
                        cx - body_w / 2,
                        top,
                        body_w,
                        max(bottom - top, 1.0),
                        paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL),
                    )
                )
        else:
            up = candles[-1].c >= candles[0].c
            color = GREEN if up else RED
            pts = [(x(i), y(c.c)) for i, c in enumerate(candles)]
            line = [cv.Path.MoveTo(*pts[0])] + [cv.Path.LineTo(px, py) for px, py in pts[1:]]
            area = line + [cv.Path.LineTo(pts[-1][0], TOP_PAD + plot_h), cv.Path.LineTo(pts[0][0], TOP_PAD + plot_h), cv.Path.Close()]
            shapes.append(
                cv.Path(
                    area,
                    paint=ft.Paint(
                        style=ft.PaintingStyle.FILL,
                        gradient=ft.PaintLinearGradient(
                            (0, TOP_PAD),
                            (0, TOP_PAD + plot_h),
                            [ft.Colors.with_opacity(0.25, color), ft.Colors.with_opacity(0.0, color)],
                        ),
                    ),
                )
            )
            shapes.append(
                cv.Path(
                    line,
                    paint=ft.Paint(style=ft.PaintingStyle.STROKE, stroke_width=2, color=color, stroke_join=ft.StrokeJoin.ROUND),
                )
            )

        # EMA overlays
        for series, color in ((ema20, EMA20_COLOR), (ema50, EMA50_COLOR)):
            pts = [(x(i), y(v)) for i, v in enumerate(series) if v is not None]
            if len(pts) > 1:
                path = [cv.Path.MoveTo(*pts[0])] + [cv.Path.LineTo(px, py) for px, py in pts[1:]]
                shapes.append(
                    cv.Path(path, paint=ft.Paint(style=ft.PaintingStyle.STROKE, stroke_width=1.3, color=ft.Colors.with_opacity(0.85, color)))
                )

        # Last price line + tag
        price = self._price if self._price is not None else candles[-1].c
        if lo <= price <= hi:
            last_color = GREEN if candles[-1].c >= candles[-1].o else RED
            py = y(price)
            shapes.append(
                cv.Line(0, py, plot_w, py, paint=ft.Paint(color=ft.Colors.with_opacity(0.7, last_color), stroke_width=1, stroke_dash_pattern=[4, 3]))
            )
            shapes.append(cv.Rect(plot_w + 2, py - 9, AXIS_W - 2, 18, border_radius=4, paint=ft.Paint(color=last_color)))
            shapes.append(cv.Text(plot_w + 6, py - 7, _fmt(price), style=ft.TextStyle(size=10, color="#0A0E15", weight=ft.FontWeight.BOLD)))

        self._canvas.shapes = shapes
