"""Reusable visual pieces for the dashboard."""

from __future__ import annotations

import time

import flet as ft

from analysis import ENTER, EXIT, Snapshot
from metrics import bootstrap_mean_interval, win_rate_interval
from datetime import datetime
from data import DEFAULT_TIMEFRAME, TIMEFRAMES
from views.chart import EMA20_COLOR, EMA50_COLOR, PriceChart

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


def tf_label(key: str) -> str:
    return key.upper() if key[-1] in "hdw" else key


def stat(label: str) -> tuple[ft.Container, ft.Text, ft.Text]:
    value = ft.Text("-", size=15, weight=ft.FontWeight.W_600, color=TEXT)
    caption = ft.Text(label, size=11, color=MUTED)
    box = ft.Container(
        ft.Column([caption, value], spacing=2, tight=True),
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        bgcolor=CARD_ALT,
        border_radius=10,
        expand=1,
    )
    return box, value, caption


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


MIN_TRACK_TRADES = 30


def track_record_text(sig, cost_note: str) -> str:
    """Rolling record without overstating a small sample.

    Below MIN_TRACK_TRADES the win rate and average are hidden; only the count and
    the exact 95% range of the win rate are shown, so 5/6 reads as "36-100%".
    """
    results = sig.results()
    n = len(results)
    note = f"Hasil dalam R bersih ({cost_note})." if cost_note else ""
    if sig.disabled:
        return note
    if n == 0:
        return f"Belum ada sinyal selesai di ±200 candle terakhir. {note}"
    wins = sum(1 for r in results if r > 0)
    lo, hi = win_rate_interval(wins, n)
    if n < MIN_TRACK_TRADES:
        return (
            f"Rekam jejak: {n} sinyal di ±200 candle terakhir, terlalu sedikit untuk dinilai "
            f"(butuh ≥ {MIN_TRACK_TRADES}). Rentang win rate 95%: {lo:.0%}–{hi:.0%}. {note}"
        )
    avg = sum(results) / n
    a, b = bootstrap_mean_interval(results, resamples=2000)
    return (
        f"Rekam jejak {n} sinyal: menang {wins / n:.0%} (95%: {lo:.0%}–{hi:.0%}), "
        f"rata-rata {avg:+.2f}R (95%: {a:+.2f} s/d {b:+.2f}R). {note}"
    )


class MarketCard(ft.Container):
    def __init__(self, short: str, label: str, source: str, on_timeframe=None, timeframe: str = DEFAULT_TIMEFRAME):
        self.snapshot: Snapshot | None = None
        self.timeframe = timeframe
        self._on_timeframe = on_timeframe
        self.error: str | None = None
        self._label = label

        self._status = ft.Container()
        self._price = ft.Text("—", size=30, weight=ft.FontWeight.BOLD, color=TEXT)
        self._change = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self._chart = PriceChart()
        self._tf_chips: dict[str, ft.Container] = {}
        for key in TIMEFRAMES:
            self._tf_chips[key] = ft.Container(
                ft.Text(tf_label(key), size=12, weight=ft.FontWeight.W_600),
                padding=ft.Padding.symmetric(horizontal=9, vertical=5),
                border_radius=8,
                on_click=lambda e, k=key: self._select_tf(k),
                data=key,
            )
        self._mode_btns = {
            "candle": ft.IconButton(ft.Icons.CANDLESTICK_CHART, icon_size=18, tooltip="Candlestick", on_click=lambda e: self._select_mode("candle")),
            "line": ft.IconButton(ft.Icons.SHOW_CHART, icon_size=18, tooltip="Garis", on_click=lambda e: self._select_mode("line")),
        }
        self._chart_hint = ft.Text("", size=11, color=MUTED)
        self._paint_toolbar()
        toolbar = ft.Row(
            [
                ft.Row(list(self._tf_chips.values()), spacing=2, scroll=ft.ScrollMode.AUTO, expand=True),
                ft.Row(list(self._mode_btns.values()), spacing=0),
            ],
            spacing=4,
        )
        def small_btn(icon, tip, handler):
            return ft.IconButton(icon, icon_size=16, icon_color=MUTED, tooltip=tip, on_click=handler,
                                 style=ft.ButtonStyle(padding=4), width=30, height=30)

        self._reset_btn = small_btn(ft.Icons.RESTART_ALT, "Reset zoom", lambda e: self._chart.reset_view())
        self._reset_btn.disabled = True  # stays in place so the +/- buttons never shift
        self._chart.on_view_change = self._on_chart_view
        legend = ft.Row(
            [
                ft.Container(width=12, height=3, bgcolor=EMA20_COLOR, border_radius=2),
                ft.Text("EMA20", size=11, color=MUTED),
                ft.Container(width=12, height=3, bgcolor=EMA50_COLOR, border_radius=2),
                ft.Text("EMA50", size=11, color=MUTED),
                self._chart_hint,
                ft.Container(expand=True),
                small_btn(ft.Icons.ZOOM_OUT, "Perkecil (lebih banyak candle)", lambda e: self._chart.zoom(1.4)),
                small_btn(ft.Icons.ZOOM_IN, "Perbesar", lambda e: self._chart.zoom(1 / 1.4)),
                self._reset_btn,
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._chart_block = ft.Column([toolbar, self._chart, legend], spacing=6)

        self._signal = ft.Text("Memuat…", size=22, weight=ft.FontWeight.BOLD, color=MUTED)
        self._score = ft.Text("", size=13, color=MUTED)
        self._bar = ScoreBar()
        self._confirm_icon = ft.Icon(ft.Icons.SCHEDULE, size=14, color=MUTED)
        self._confirm = ft.Text("", size=12, color=MUTED, expand=True)
        self._preview = ft.Text("", size=11, color=MUTED)
        self._track = ft.Text("", size=11, color=MUTED)
        self._signal_box = ft.Container(
            ft.Column(
                [
                    ft.Row(
                        [ft.Row([self._signal, badge("EKSPERIMENTAL", AMBER)], spacing=8, tight=True), self._score],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        wrap=True,
                    ),
                    self._bar,
                    ft.Row([self._confirm_icon, self._confirm], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                    self._preview,
                    self._track,
                ],
                spacing=8,
            ),
            padding=14,
            border_radius=12,
            bgcolor=CARD_ALT,
            border=ft.Border.all(1, BORDER),
        )

        self._rev_badge = ft.Container()
        self._rev_note = ft.Text("", size=12, color=TEXT)
        self._rev_bar = ft.ProgressBar(value=0, bar_height=6, border_radius=3, bgcolor=BORDER, color=MUTED)
        self._rev_trigger = ft.Text("-", size=13, weight=ft.FontWeight.W_600, color=TEXT)
        self._rev_invalid = ft.Text("-", size=13, weight=ft.FontWeight.W_600, color=MUTED)
        self._rev_flow = ft.Text("", size=11, color=MUTED)
        self._rev_factors = ft.Column(spacing=4)
        self._rev_levels = ft.Row(
            [
                ft.Column([ft.Text("Pemicu (tembus)", size=11, color=MUTED), self._rev_trigger], spacing=2, expand=1),
                ft.Column([ft.Text("Batal jika lewat", size=11, color=MUTED), self._rev_invalid], spacing=2, expand=1),
            ]
        )
        self._rev_box = ft.Container(
            ft.Column(
                [
                    ft.Row([ft.Text("Potensi balik arah", size=13, weight=ft.FontWeight.W_600, color=TEXT), self._rev_badge],
                           alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    self._rev_note,
                    self._rev_bar,
                    self._rev_levels,
                    self._rev_factors,
                    self._rev_flow,
                    ft.Text(
                        "Skor = jumlah kondisi pembalikan yang terpenuhi, bukan peluang pasti. "
                        "Uji historis BTC (lapisan candle): belum terbukti lebih akurat dari kebetulan.",
                        size=10,
                        color=MUTED,
                        italic=True,
                    ),
                ],
                spacing=8,
            ),
            padding=14,
            border_radius=12,
            border=ft.Border.all(1, BORDER),
        )

        self._news_summary = ft.Text("Memuat berita…", size=11, color=MUTED)
        self._news_list = ft.Column(spacing=10)
        self._news = ft.ExpansionTile(
            title=ft.Text("Berita & sentimen", size=13, color=TEXT),
            subtitle=self._news_summary,
            controls=[ft.Container(self._news_list, padding=ft.Padding.only(left=4, right=4, bottom=8))],
            tile_padding=ft.Padding.symmetric(horizontal=4),
            icon_color=MUTED,
            collapsed_icon_color=MUTED,
            shape=ft.RoundedRectangleBorder(radius=10),
            collapsed_shape=ft.RoundedRectangleBorder(radius=10),
        )

        rsi_box, self._rsi, _ = stat("RSI 14")
        adx_box, self._adx, _ = stat("ADX (kekuatan)")
        atr_box, self._atr, self._atr_caption = stat("ATR")
        trend_box, self._trend, self._trend_caption = stat("Tren TF besar")

        def cell(caption: str, color: str) -> tuple[ft.Column, ft.Text]:
            value = ft.Text("-", size=14, weight=ft.FontWeight.W_600, color=color)
            return ft.Column([ft.Text(caption, size=11, color=MUTED), value], spacing=2, expand=1), value

        entry_col, self._entry = cell("Entry (terkunci)", TEXT)
        stop_col, self._stop = cell("Stop loss", RED)
        tp1_col, self._tp1 = cell("Target 1", GREEN)
        tp2_col, self._tp2 = cell("Target 2", GREEN)
        self._plan_title = ft.Text("", size=12, weight=ft.FontWeight.W_600, color=TEXT)
        self._plan_status = ft.Text("", size=11, color=MUTED)
        self._levels = ft.Container(
            ft.Column(
                [
                    self._plan_title,
                    ft.Row([entry_col, stop_col]),
                    ft.Row([tp1_col, tp2_col]),
                    self._plan_status,
                ],
                spacing=8,
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
            bgcolor="#E8B931" if short == "GC" else "#F7931A",
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
                    self._chart_block,
                    self._signal_box,
                    self._rev_box,
                    ft.Row([rsi_box, adx_box], spacing=8),
                    ft.Row([atr_box, trend_box], spacing=8),
                    self._levels,
                    self._reasons,
                    self._news,
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

        live, conf, sig = snap.live, snap.confirmed, snap.signal
        color = signal_color(sig.label)

        self._price.value = fmt_price(snap.price)
        if prev is not None and snap.price != prev.price:
            self._price.color = GREEN if snap.price > prev.price else RED
        else:
            self._price.color = TEXT
        up = snap.change_24h >= 0
        self._change.value = f"{'▲' if up else '▼'} {abs(snap.change_24h):.2f}%  24j"
        self._change.color = GREEN if up else RED
        rev = snap.reversal
        markers = []
        if sig.plan is not None:
            pl = sig.plan
            markers = [(pl.entry, TEXT, "Entry"), (pl.stop, RED, "Stop"), (pl.tp1, GREEN, "TP1"), (pl.tp2, GREEN, "TP2")]
        elif rev.stage != "NONE" and rev.trigger is not None:
            markers = [(rev.trigger, BLUE, "Pemicu"), (rev.invalidation, MUTED, "Batal")]
        self._chart.set_markers(markers)
        self._chart.set_data(snap.chart, snap.ema20, snap.ema50, snap.price, snap.tf.seconds)
        self._show_reversal(snap)
        self._chart_hint.value = f"Timeframe {tf_label(snap.tf.key)}"
        self._atr_caption.value = f"ATR {tf_label(snap.tf.key)}"
        self._trend_caption.value = f"Tren {tf_label(conf.htf)}"

        closed = snap.status == "CLOSED"
        tf = tf_label(snap.tf.key)
        self._signal.value = sig.label
        self._signal.color = MUTED if closed else color
        self._score.value = f"skor {conf.score:+d}/100 (candle close)"
        self._bar.set_score(conf.score)
        self._signal_box.border = ft.Border.all(1, ft.Colors.with_opacity(0.45, color) if sig.plan and not closed else BORDER)

        def hhmm(t: int) -> str:
            return datetime.fromtimestamp(t + snap.tf.seconds).strftime("%d %b %H:%M")

        if sig.plan is not None:
            word = "BUY" if sig.plan.direction > 0 else "SELL"
            icon, icolor = ft.Icons.CHECK_CIRCLE, color
            text = f"{word} dari close candle {tf} {hhmm(sig.plan.opened_t)}. Keluar bila |skor| < {EXIT} atau kena stop/target."
            if conf.sideways:
                text += f" ADX kini {conf.adx:.0f} < 20: tidak ada entry baru, posisi lama dikelola rencananya."
        elif sig.disabled:
            icon, icolor = ft.Icons.BLOCK, MUTED
            text = sig.disabled
        elif conf.sideways:
            icon, icolor = ft.Icons.BLOCK, MUTED
            text = f"ADX {conf.adx:.0f} < 20: pasar sideways, sinyal tren dimatikan."
        elif sig.rejection and sig.rejection_t == conf.t:
            icon, icolor = ft.Icons.DO_NOT_DISTURB_ON, AMBER
            text = sig.rejection
        else:
            icon, icolor = ft.Icons.SCHEDULE, MUTED
            text = f"Menunggu: sinyal muncul bila skor candle close ≥ +{ENTER} atau ≤ −{ENTER} dan R:R ≥ 1,5."
        if closed:
            icon, icolor = ft.Icons.NIGHTLIGHT_ROUND, MUTED
            text = "Pasar tutup. " + text
        self._confirm_icon.icon, self._confirm_icon.color = icon, icolor
        self._confirm.value = text
        self._preview.value = (
            f"Pratinjau candle berjalan (belum final): skor {live.score:+d}"
            + (" · ADX < 20" if live.sideways else "")
        )
        self._track.value = track_record_text(sig, snap.feed.cost_note)

        self._rsi.value = f"{conf.rsi:.1f}"
        self._rsi.color = RED if conf.rsi > 70 else GREEN if conf.rsi < 30 else TEXT
        self._adx.value = f"{conf.adx:.0f} · {'sideways' if conf.sideways else 'trending'}"
        self._atr.value = fmt_price(conf.atr)
        self._trend.value = {1: "▲ Naik", -1: "▼ Turun", 0: "◆ Datar"}[conf.trend_htf]
        self._trend.color = {1: GREEN, -1: RED, 0: MUTED}[conf.trend_htf]

        plan = sig.plan
        if plan is not None:
            self._levels.visible = True
            word = "BUY" if plan.direction > 0 else "SELL"
            self._plan_title.value = (
                f"Rencana {word} · 1R = {fmt_price(plan.risk)} per unit · biaya ≈ {plan.cost_r:.2f}R"
            )
            self._entry.value = fmt_price(plan.entry)
            self._stop.value = fmt_price(plan.stop) + (" (BE)" if plan.status == "TP1" else "")
            self._tp1.value = f"{fmt_price(plan.tp1)} · {plan.rr1 - plan.cost_r:.1f}R bersih"
            self._tp2.value = f"{fmt_price(plan.tp2)} · {plan.rr2 - plan.cost_r:.1f}R bersih"
            open_r = (snap.price - plan.entry) * plan.direction / plan.risk - plan.cost_r
            status = "TP1 tercapai, setengah posisi diamankan, stop pindah ke entry" if plan.status == "TP1" else "Berjalan"
            self._plan_status.value = (
                f"{status} · posisi sekarang {open_r:+.2f}R setelah biaya. Stop di balik swing terakhir + 0,25 ATR, "
                "level tidak berubah sampai sinyal selesai."
            )
        else:
            self._levels.visible = False

        rows = []
        for f in sorted(conf.factors, key=lambda f: -abs(f.points)):
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

    def _show_reversal(self, snap: Snapshot) -> None:
        rev = snap.reversal
        up = rev.direction > 0
        dir_color = GREEN if up else RED
        stage_label = {"NONE": ("TIDAK ADA", MUTED), "WATCH": ("PANTAU", AMBER), "SETUP": ("SIAP", BLUE), "TRIGGER": ("TERPICU", dir_color)}
        text, color = stage_label[rev.stage]
        if rev.direction and rev.stage != "NONE":
            text = f"{text} {'▲' if up else '▼'}"
        self._rev_badge.content = badge(text, color)
        self._rev_note.value = rev.note
        self._rev_bar.value = rev.score / 100
        self._rev_bar.color = color
        self._rev_levels.visible = rev.stage != "NONE" and rev.trigger is not None
        if rev.trigger is not None:
            self._rev_trigger.value = fmt_price(rev.trigger)
            self._rev_invalid.value = fmt_price(rev.invalidation)
        self._rev_box.border = ft.Border.all(1, ft.Colors.with_opacity(0.6, color) if rev.stage in ("SETUP", "TRIGGER") else BORDER)
        rows = []
        for f in rev.factors:
            c = GREEN if f.points > 0 else RED
            rows.append(
                ft.Row(
                    [
                        ft.Text(f"{f.points:+d}", size=11, weight=ft.FontWeight.BOLD, color=c, width=28),
                        ft.Text(f"{f.group}: {f.detail}", size=11, color=TEXT, expand=True),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            )
        self._rev_factors.controls = rows
        flow = snap.flow
        if flow is not None:
            self._rev_flow.value = (
                f"Order flow 15 menit (dipakai di skor): taker beli {flow.buy_ratio_15m:.0%}, "
                f"5 mnt terakhir {flow.buy_ratio_5m:.0%}. Info saja: {flow.window:.0f} dtk terakhir "
                f"{flow.buy_ratio_60s:.0%}, bid/ask {flow.imbalance:.2f}×."
            )
        else:
            self._rev_flow.value = "Order flow tidak tersedia untuk sumber ini (Yahoo), hanya candle + berita."

    def show_news(self, news) -> None:
        def tone(v: float) -> tuple[str, str]:
            if v >= 0.15:
                return "positif", GREEN
            if v <= -0.15:
                return "negatif", RED
            return "netral", MUTED

        word, color = tone(news.score)
        sources = sorted({h.source for h in news.headlines})
        self._news_summary.value = (
            f"Sentimen {news.score:+.2f} ({word}) · 3 jam terakhir {news.recent:+.2f} · {len(news.headlines)} berita"
        )
        self._news_summary.color = color
        items = []
        now = time.time()
        for h in news.headlines[:10]:
            _, c = tone(h.sentiment)
            items.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Container(width=4, height=34, bgcolor=c, border_radius=2),
                            ft.Column(
                                [
                                    ft.Text(h.title, size=12, color=TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                                    ft.Text(f"{h.source} · {ago(now - h.published)} · {h.sentiment:+.2f}", size=10, color=MUTED),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                    ),
                    url=h.url,
                    ink=True,
                    border_radius=6,
                )
            )
        items.append(
            ft.Text(
                "Sumber: " + ", ".join(sources) + ". Sentimen dibaca dari judul berita (kata kunci), bukan isi artikel.",
                size=10,
                color=MUTED,
                italic=True,
            )
        )
        self._news_list.controls = items

    def show_news_error(self, message: str) -> None:
        self._news_summary.value = f"Berita gagal dimuat: {message}"
        self._news_summary.color = AMBER

    def _on_chart_view(self, zoomed: bool) -> None:
        if self._reset_btn.disabled == zoomed:
            self._reset_btn.disabled = not zoomed
            self._reset_btn.icon_color = BLUE if zoomed else MUTED
            self._reset_btn.update()

    # --------------------------------------------------------------- toolbar
    def _paint_toolbar(self) -> None:
        for key, chip in self._tf_chips.items():
            active = key == self.timeframe
            chip.bgcolor = BLUE if active else None
            chip.content.color = BG if active else MUTED
        for mode, btn in self._mode_btns.items():
            btn.icon_color = TEXT if mode == self._chart.mode else MUTED
            btn.bgcolor = CARD_ALT if mode == self._chart.mode else None

    def _select_tf(self, key: str) -> None:
        if key == self.timeframe:
            return
        self.timeframe = key
        self._paint_toolbar()
        self._chart_hint.value = f"Memuat {tf_label(key)}…"
        self.update()
        if self._on_timeframe:
            self._on_timeframe(self, key)

    def _select_mode(self, mode: str) -> None:
        self._chart.set_mode(mode)
        self._paint_toolbar()
        self.update()

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
        status = snap.status
        if status != "CLOSED" and time.time() - snap.fetched_at > 60:
            status = "DELAYED"  # sync stalled; a closed market keeps its single status
        if status == "LIVE":
            self._status.content = badge("LIVE", GREEN)
        elif status == "CLOSED":
            self._status.content = badge("PASAR TUTUP", MUTED)
        else:
            self._status.content = badge(f"TERTUNDA {int(age // 60)} mnt", AMBER)
        self._footer.value = (
            f"Harga {ago(age)} · disinkron {ago(time.time() - snap.fetched_at)} · {snap.latency_ms} ms"
        )
