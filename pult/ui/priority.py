"""Раздел «Приоритет выезда»: веса, кластеры городов, топ-40."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout

from ..core.priority import (NUMERIC, PRIORITY_COLUMNS, WEIGHTS, cluster_chips,
                             priority_query, priority_row)
from .widgets import Button, Card, ChipFlow, Page, Slider, Table


class PrioritySection:
    def build_prio(self) -> Page:
        page = Page()
        wcard = Card("Кластер",
                     "Порядок выезда считается по ОЖИДАЕМОМУ СБОРУ: вероятность оплаты × "
                     "сумма, по модели на реальном портфеле. Единственная ручка снаружи — "
                     "«Кластер»: насколько сильно подтягивать соседние адреса ради "
                     "экономии на объезде. Таблица сортируется кликом по заголовку.")
        row = QHBoxLayout()
        row.setSpacing(14)
        self.weights: dict[str, Slider] = {}
        for key, title, init in WEIGHTS:
            s = Slider(title, init)
            self.weights[key] = s
            row.addWidget(s, 1)
        row.addSpacing(16)
        row.addWidget(Button("Пересчитать", self.load_priority, kind="accent"), 0,
                      Qt.AlignmentFlag.AlignBottom)
        wcard.add_layout(row)
        page.add(wcard)

        ccard = Card("Кластеры городов")
        self.cluster_chips = ccard.add(ChipFlow())
        self.cluster_chips.set_chips(["—"])
        page.add(ccard)

        tcard = Card("Куда ехать — топ-40")
        self.prio_tv = tcard.add(Table(PRIORITY_COLUMNS, numeric=NUMERIC,
                                       stretch=("client", "why"), visible_rows=18))
        page.add(tcard)
        return page

    def load_priority(self):
        q = priority_query({k: s.value() for k, s in self.weights.items()})

        def done(d):
            self._loaded.add("prio")
            self.cluster_chips.set_chips(cluster_chips(d.get("cities") or []))
            self.prio_tv.set_rows([priority_row(i, it)
                                   for i, it in enumerate(d.get("items") or [], 1)])
        self.bg_task(lambda: self.api("GET", "/admin/priority" + q), done)
