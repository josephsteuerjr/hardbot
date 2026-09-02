"""Раздел «Активность»: сводка по работникам."""

from __future__ import annotations

from ..core.activity import ACTIVITY_COLUMNS, NUMERIC, activity_row
from .widgets import Card, Page, Table


class ActivitySection:
    def build_activity(self) -> Page:
        page = Page()
        ac = Card("Активность работников",
                  "Каждый визит и метка пишутся в БД с датой — здесь сводка. Простой — "
                  "полных дней с последней активности; самые простаивающие сверху.")
        self.act_tv = ac.add(Table(ACTIVITY_COLUMNS, numeric=NUMERIC,
                                   stretch=("emp", "status"), visible_rows=14))
        page.add(ac)
        return page

    def load_activity(self):
        def done(d):
            self._loaded.add("activity")
            self.act_tv.set_rows([activity_row(w) for w in d.get("workers") or []])
        self.bg_task(lambda: self.api("GET", "/admin/activity"), done)
