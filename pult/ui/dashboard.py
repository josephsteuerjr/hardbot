"""Раздел «Сегодня»: день, а не склад — выезды сегодня/вчера/неделя, столбики,
кто молчит, дошли ли карточки, что с реестром и обогащением."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core.dashboard import (COUNTRIES, SILENT_COLUMNS, SILENT_NUMERIC, TILES, bars,
                              country_pairs, country_today_pairs, delivery_note,
                              delivery_pairs, metric_values, outcome_chips, registry_note,
                              registry_pairs, silent_note, silent_row, tile_values)
from .widgets import Bars, Button, Card, ChipFlow, KVGrid, Note, Page, Table, label


class DashboardSection:
    def build_dash(self) -> Page:
        page = Page()

        strip = QWidget()
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.tiles: dict[str, QLabel] = {}
        for key, title in TILES:
            tile = QFrame()
            tile.setObjectName("Card")
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(13, 10, 13, 10)
            lay.setSpacing(0)
            lay.addWidget(label(title, "tiny"))
            value = label("—", "metric")
            lay.addWidget(value)
            row.addWidget(tile, 1)
            self.tiles[key] = value
        page.add(strip)

        bc = Card("Выезды по дням",
                  "Человеко-дни по всей базе: повторный выезд к тому же должнику в "
                  "другой день считается отдельно. Зелёный столбик — сегодня.")
        self.day_bars = bc.add(Bars())
        page.add(bc)

        sc = Card("Сегодня не выехали",
                  "Сотрудники с портфелем и без единого выезда за сегодня. Самые "
                  "давние простои сверху; кликни по заголовку — пересортируется.")
        self.silent_note = sc.add(Note("", "small"))
        self.silent_tv = sc.add(Table(SILENT_COLUMNS, numeric=SILENT_NUMERIC,
                                      stretch=("emp",), visible_rows=10), spacing_before=4)
        sc.add_row(Button("Ссылка входа выбранному", self._silent_link),
                   Button("Обновить", self.load_today), spacing_before=4)
        page.add(sc)

        two = QWidget()
        two_l = QHBoxLayout(two)
        two_l.setContentsMargins(0, 0, 0, 0)
        two_l.setSpacing(12)
        dc = Card("Доставка сегодня",
                  "Что ушло руководителям и в беседы. «Упало» — Телеграм отказал; "
                  "чаще всего это руководитель, который не нажал /start в боте.")
        self.delivery_kv = dc.add(KVGrid())
        self.delivery_note = dc.add(Note("", "small"), spacing_before=4)
        two_l.addWidget(dc, 1)
        rc = Card("Реестр и обогащение",
                  "Договоры в базе, сколько закреплено, сколько потеряли "
                  "ответственного и когда пульт последний раз доставлял результаты "
                  "в личные книги.")
        self.registry_kv = rc.add(KVGrid())
        self.registry_note = rc.add(Note("", "small"), spacing_before=4)
        two_l.addWidget(rc, 1)
        page.add(two)

        cc = Card("По республикам",
                  "Республика — по стране кейсов сотрудника, как у направлений; "
                  "исходы за день — чипами.")
        grid = QHBoxLayout()
        grid.setSpacing(8)
        self.country_kv: dict[str, KVGrid] = {}
        self.country_results: dict[str, ChipFlow] = {}
        for code, title in COUNTRIES:
            col = QWidget()
            lay = QVBoxLayout(col)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            lay.addWidget(label(title, bold=True))
            kv = KVGrid()
            kv.set_pairs([("договоров", 0), ("с визитом", 0), ("сотрудников", 0)])
            chips = ChipFlow()
            chips.set_chips(["нет закреплённых кейсов"])
            lay.addWidget(kv)
            lay.addWidget(chips)
            grid.addWidget(col, 1)
            self.country_kv[code] = kv
            self.country_results[code] = chips
        cc.add_layout(grid)
        page.add(cc)

        # сохранены ради прежнего протокола render_stats
        self.metrics: dict[str, QLabel] = {}
        self.res_chips = ChipFlow()
        self.period_dash = Note("", "small")
        self._today: dict = {}
        return page

    # --- /admin/today --------------------------------------------------------
    def load_today(self):
        def done(d):
            self._loaded.add("dash")
            self.render_today(d or {})
        self.bg_task(lambda: self.api("GET", "/admin/today", timeout=120), done)

    def render_today(self, d: dict) -> None:
        self._today = d
        for key, value in tile_values(d).items():
            if key in self.tiles:
                self.tiles[key].setText(value)
        self.day_bars.set_series(bars(d))
        self.silent_note.setText(silent_note(d))
        self.silent_tv.set_rows([silent_row(r) for r in d.get("silent") or []])
        self.delivery_kv.set_pairs(delivery_pairs(d), cols=2)
        self.delivery_note.setText(delivery_note(d))
        self.registry_kv.set_pairs(registry_pairs(d), cols=3)
        self.registry_note.setText(registry_note(d))
        for c in d.get("countries") or []:
            if c.get("code") in self.country_kv:
                self.country_kv[c["code"]].set_pairs(country_today_pairs(c), cols=5)

    def _silent_link(self):
        row = self.silent_tv.selected_row()
        if not row:
            self.toast("Выбери сотрудника в таблице", err=True)
            return
        self.issue_link(employee=row[0])

    # --- /admin/stats (справка о реестре по республикам) ---------------------
    def render_stats(self, d: dict) -> None:
        for key, value in metric_values(d).items():
            if key in self.metrics:
                self.metrics[key].setText(value)
        country_rows = {c.get("code"): c for c in (d.get("countries") or [])}
        for code, _title in COUNTRIES:
            c = country_rows.get(code) or {}
            # цифры дня уже стоят из /admin/today — /admin/stats добавляет только
            # исходы; его собственные счётчики по спискам руководителей нули
            if not self._today:
                self.country_kv[code].set_pairs(country_pairs(c))
            self.country_results[code].set_chips(
                outcome_chips(c.get("by_result"), "исходов за день пока нет"))
        self.res_chips.set_chips(outcome_chips(d.get("by_result"),
                                               "визитов с исходом пока нет"))
