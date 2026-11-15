"""Candlestick / line price chart drawn on a Flet canvas."""

from __future__ import annotations

import time
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
MIN_STEP = 7  # px per candle; decides how many candles fit the width by default
MIN_VISIBLE = 15
REDRAW_INTERVAL = 0.04  # seconds; throttles redraws while pinching/dragging


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
    """Zoom: pinch, mouse wheel or the +/- buttons. Pan: drag sideways."""

    def __init__(self, height: int = 250):
        self.mode = "candle"
        self._candles: list[Candle] = []
        self._ema20: list[float | None] = []
        self._ema50: list[float | None] = []
        self._price: float | None = None
        self._tf_seconds = 900
        self._markers: list[tuple[float, str, str]] = []
        self._size = (0.0, float(height))
        self._visible: float | None = None  # None = fit to width
        self._offset = 0.0  # candles scrolled back from the latest one
        self._pinch_base = 0.0
        self._last_redraw = 0.0
        self.on_view_change = None  # callback(zoomed: bool)
        self._canvas = cv.Canvas(expand=True, on_resize=self._on_resize, resize_interval=50)
        gestures = ft.GestureDetector(
            content=self._canvas,
            on_scale_start=self._on_scale_start,
            on_scale_update=self._on_scale_update,
            on_scroll=self._on_scroll,
            drag_interval=16,
        )
        super().__init__(content=gestures, height=height)

    def _on_resize(self, e: cv.CanvasResizeEvent) -> None:
        self._size = (e.width, e.height)
        self._draw()
        self._canvas.update()

    def set_data(self, candles, ema20, ema50, price: float, tf_seconds: int) -> None:
        if tf_seconds != self._tf_seconds:
            self.reset_view(redraw=False)
        self._candles, self._ema20, self._ema50 = candles, ema20, ema50
        self._price = price
        self._tf_seconds = tf_seconds
        self._draw()

    def set_markers(self, markers: list[tuple[float, str, str]]) -> None:
        """Horizontal reference lines: (price, color, label)."""
        self._markers = markers

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._draw()

    # ------------------------------------------------------------ zoom / pan
    @property
    def zoomed(self) -> bool:
        return self._visible is not None or self._offset >= 1

    def _default_visible(self) -> float:
        plot_w = max(self._size[0] - AXIS_W, 1)
        return max(20, min(len(self._candles), plot_w // MIN_STEP))

    def _clamp(self) -> None:
        total = max(len(self._candles), MIN_VISIBLE)
        if self._visible is not None:
            self._visible = max(MIN_VISIBLE, min(total, self._visible))
        n = self._visible or self._default_visible()
        self._offset = max(0.0, min(total - n, self._offset))

    def zoom(self, factor: float) -> None:
        """factor < 1 zooms in (fewer candles), > 1 zooms out."""
        self._visible = (self._visible or self._default_visible()) * factor
        self._clamp()
        self._redraw(force=True)

    def reset_view(self, redraw: bool = True) -> None:
        self._visible = None
        self._offset = 0.0
        if redraw:
            self._redraw(force=True)

    def _on_scale_start(self, e: ft.ScaleStartEvent) -> None:
        self._pinch_base = self._visible or self._default_visible()

    def _on_scale_update(self, e: ft.ScaleUpdateEvent) -> None:
        if e.pointer_count >= 2 and e.scale and abs(e.scale - 1) > 0.01:
            self._visible = self._pinch_base / e.scale
        plot_w = max(self._size[0] - AXIS_W, 1)
        step = plot_w / (self._visible or self._default_visible())
        if e.focal_point_delta:
            self._offset += e.focal_point_delta.x / step  # drag right -> older candles
        self._clamp()
        self._redraw()

    def _on_scroll(self, e: ft.ScrollEvent) -> None:
        dy = e.scroll_delta.y if e.scroll_delta else 0
        if dy:
            self.zoom(1.15 if dy > 0 else 1 / 1.15)

    def _redraw(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_redraw < REDRAW_INTERVAL:
            return
        self._last_redraw = now
        self._draw()
        self._canvas.update()
        if self.on_view_change:
            self.on_view_change(self.zoomed)

    # ----------------------------------------------------------------- drawing
    def _draw(self) -> None:
        w, h = self._size
        plot_w = w - AXIS_W
        plot_h = h - TIME_H - TOP_PAD
        if plot_w <= 40 or len(self._candles) < 2:
            self._canvas.shapes = []
            return

        self._clamp()
        n = max(2, min(len(self._candles), int(round(self._visible or self._default_visible()))))
        end = len(self._candles) - int(self._offset)
        start = max(0, end - n)
        candles = self._candles[start:end]
        ema20 = self._ema20[start:end]
        ema50 = self._ema50[start:end]
        n = len(candles)
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

        # Reference levels (e.g. reversal trigger / invalidation)
        for level, color, label in self._markers:
            if lo <= level <= hi:
                ly = y(level)
                shapes.append(cv.Line(0, ly, plot_w, ly, paint=ft.Paint(color=color, stroke_width=1, stroke_dash_pattern=[2, 4])))
                shapes.append(cv.Text(4, ly - 14, f"{label} {_fmt(level)}", style=ft.TextStyle(size=10, color=color)))

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
