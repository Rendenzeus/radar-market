import flet as ft

from views.dashboard import MarketDashboard


async def main(page: ft.Page):
    page.title = "Market Radar"
    page.theme_mode = ft.ThemeMode.DARK

    dashboard = MarketDashboard()
    page.add(dashboard)
    dashboard.start()


ft.run(main)
