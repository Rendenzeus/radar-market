import flet as ft

from views.dashboard import MarketDashboard
from views.widgets import BG


async def main(page: ft.Page):
    page.title = "Market Radar"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.theme = ft.Theme(color_scheme_seed="#5B8DEF")
    page.dark_theme = ft.Theme(color_scheme_seed="#5B8DEF")
    page.padding = 0

    dashboard = MarketDashboard()
    page.add(
        ft.SafeArea(
            ft.ListView(
                [ft.Container(dashboard, padding=ft.Padding.symmetric(horizontal=16, vertical=12))],
                expand=True,
            ),
            expand=True,
        )
    )
    dashboard.start()


ft.run(main)
