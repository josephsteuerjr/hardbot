"""Раздел «Сводка»: метрики, республики, покрытие, исходы, период."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core.dashboard import (COUNTRIES, COUNTRIES_DESC, METRICS, country_pairs,
                              coverage, metric_values, outcome_chips)
from .widgets import Card, ChipFlow, KVGrid, Meter, Note, Page, Pane, label


class DashboardSection:
    def build_dash(self) -> Page:
        page = Page()

        strip = QWidget()
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.metrics: dict[str, QLabel] = {}
        for key, title in METRICS:
            tile = QFrame()
            tile.setObjectName("Card")
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(13, 10, 13, 10)
            lay.setSpacing(0)
            lay.addWidget(label(title, "tiny"))
            value = label("—", "metric")
            lay.addWidget(value)
            row.addWidget(tile, 1)
            self.metrics[key] = value
        page.add(strip)

        countries = Card("По республикам", COUNTRIES_DESC)
        grid = QHBoxLayout()
        grid.setSpacing(8)
        self.country_kv: dict[str, KVGrid] = {}
        self.country_results: dict[str, ChipFlow] = {}
        for code, title in COUNTRIES:
            pane = Pane(margins=12)
            pane.body.setSpacing(6)
            pane.body.addWidget(label(title, bold=True))
            kv = KVGrid()
            kv.set_pairs([("договоров", 0), ("с визитом", 0), ("сотрудников", 0)])
            chips = ChipFlow()
            chips.set_chips(["нет закреплённых кейсов"])
            pane.body.addWidget(kv)
            pane.body.addWidget(chips)
            grid.addWidget(pane, 1)
            self.country_kv[code] = kv
            self.country_results[code] = chips
        countries.add_layout(grid)
        page.add(countries)

        cov = Card("Покрытие визитами")
        self.cov_bar = cov.add(Meter())
        self.cov_txt = cov.add(Note("нет данных — подключись к серверу", "small"))
        page.add(cov)

        res = Card("Исходы визитов")
        self.res_chips = res.add(ChipFlow())
        self.res_chips.set_chips(["—"])
        page.add(res)

        per = Card("Период выгрузки")
        self.period_dash = per.add(Note("—", "small"))
        page.add(per)
        return page

    def render_stats(self, d: dict) -> None:
        for key, value in metric_values(d).items():
            self.metrics[key].setText(value)
        country_rows = {c.get("code"): c for c in (d.get("countries") or [])}
        for code, _title in COUNTRIES:
            c = country_rows.get(code) or {}
            self.country_kv[code].set_pairs(country_pairs(c))
            self.country_results[code].set_chips(
                outcome_chips(c.get("by_result"), "нет закреплённых кейсов"))
        pc, text = coverage(d)
        self.cov_bar.set_percent(pc)
        self.cov_txt.setText(text)
        self.res_chips.set_chips(outcome_chips(d.get("by_result"),
                                               "визитов с исходом пока нет"))
