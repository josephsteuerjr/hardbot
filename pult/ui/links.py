"""Раздел «Вход»: кто уже в системе, кому выдали ссылку и что с ней стало.

В контуре компании Telegram нет: человек попадает в приложение по короткой
персональной ссылке — открыл и оказался внутри, без бота и без выбора имени.

Экран собран вокруг ОДНОЙ таблицы, и идёт она от реестра, а не от привязок:
сотрудник, которому вход ещё не выдавали, обязан быть виден — иначе в первый
день теста экран пуст ровно тогда, когда нужен больше всего. Выдача — одно
движение: выбрал строку → «Выдать вход» → QR появляется тут же, под таблицей.
Вся арифметика состояний живёт в `core/links.py` и проверяется без экрана.
"""

from __future__ import annotations

from ..core import links as lk
from . import dialogs, theme
from .widgets import (Button, Card, ChipFlow, Combo, Field, KVGrid, Note, Page, Rows,
                      Table)

# Роль темы → цвет. Своих цветов у раздела нет: состояние красится теми же
# SUCCESS/WARN/DANGER/MUTED, что и подписи во всём пульте.
ROLE_COLOR = {"muted": theme.MUTED, "warn": theme.WARN,
              "success": theme.SUCCESS, "danger": theme.DANGER}

PANEL_EMPTY = ("Выбери человека в таблице и нажми «Выдать вход» — здесь появятся "
               "QR и короткий адрес.")
NO_URL_TEXT = ("Адрес этой ссылки знает только сервер: в списке он отдаёт код, а "
               "не ссылку. Нажми «Выдать заново» — получишь новый QR.")
OLD_SERVER_TEXT = ("Сервер старой версии: состояния ссылок он не отдаёт, видно "
                   "только, кто уже заходил. Выдача работает как прежде.")


def state_color(row: tuple) -> str:
    """Цвет строки таблицы по скрытому ключу состояния."""
    return ROLE_COLOR.get(lk.STATE_ROLE.get(lk.row_state(row), ""), "")


class LinksSection:
    def build_links(self) -> Page:
        page = Page()
        self._ln_people: list[dict] = []
        self._ln_by_name: dict[str, dict] = {}
        self._ln_filter = "all"
        self._ln_pick_name = ""          # кого держать выбранным после перезагрузки
        self._ln_shown: dict = {}        # чья ссылка сейчас в панели
        self._ln_revoked: set[str] = set()
        self._ln_fresh = True            # сервер знает /admin/app_links.json
        # Публичный адрес приложения. В списке сервер отдаёт код, а не ссылку, и
        # собрать адрес можно только зная базу; адрес админки не годится — пульт
        # ходит на сервер служебным адресом, телефон открывает публичный.
        self._ln_base = lk.base_of(self.settings.get("miniapp", ""))

        who = Card("Кто входит",
                   "Ссылка персональная и короткая: открыл — и сразу в своём "
                   "портфеле, без выбора имени и без бота. Ярлык «Домой» "
                   "уносит вход с собой, а второе открытие — запас: на случай, "
                   "если ярлык всё-таки откроется без доступа. «Вошёл» — это "
                   "живой вход в приложение, а не старая отметка активности.")
        self.ln_tiles = who.add(KVGrid())
        self.ln_summary = who.add(Note("", "small"), spacing_before=2)
        self.ln_note = who.add(Note("", "warn"))
        self.ln_note.setVisible(False)
        page.add(who)

        table = Card("Ссылки входа",
                     "Все из реестра и все, кому вход уже выдавали. Клик по "
                     "заголовку сортирует, поле ищет по любой колонке, чипы "
                     "фильтруют. Выбери строку — кнопки под таблицей действуют "
                     "на неё.")
        self.ln_search = Field(width_chars=30, placeholder="поиск по ФИО, состоянию…")
        self.ln_search.textChanged.connect(lambda t: self.ln_tv.set_filter(t))
        self.ln_filters = ChipFlow()
        table.add_row(self.ln_search, 8, self.ln_filters, spacing_before=4, stretch=False)
        self.ln_tv = table.add(Table(lk.LINK_COLUMNS, numeric=lk.LINK_NUMERIC,
                                     stretch=("emp",), visible_rows=12,
                                     tint=state_color, tint_cols=("state",)),
                               spacing_before=4)
        self.ln_tv.on_select(self._ln_selected)

        self.ln_uses = Combo(lk.USES_LABELS, width_chars=5)
        self.ln_uses.set_current(lk.DEFAULT_USES_INDEX)
        self.ln_issue_btn = Button("Выдать вход", self.ln_issue, kind="accent")
        self.ln_again_btn = Button("Выдать заново", self.ln_reissue)
        self.ln_pick = Note("", "tiny", wrap=False)
        # Два ряда, а не один: пять кнопок с комбобоксом в строку не влезают в
        # окно минимальной ширины. Сверху — выдача, снизу — что делать с уже
        # выданной ссылкой.
        table.add_row(self.ln_issue_btn, Note("открытий:", "small", wrap=False),
                      self.ln_uses, None, self.ln_pick,
                      Button("Обновить", self.load_links),
                      spacing_before=6, stretch=False)

        self.ln_qr_btn = Button("Показать QR", self._ln_show_qr)
        self.ln_copy_btn = Button("Скопировать ссылку", self._ln_copy)
        self.ln_revoke_btn = Button("Отозвать вход", self._ln_revoke, kind="danger")
        table.add_row(self.ln_qr_btn, self.ln_copy_btn, self.ln_again_btn, 16,
                      self.ln_revoke_btn, None, spacing_before=2, stretch=False)
        # Ошибку выдачи держим на экране, а не тостом: единственная, которая на
        # свежей машине встречает оператора гарантированно (не задан MINIAPP_URL),
        # и прочитать её за три секунды он не успеет.
        self.ln_err = table.add(Note("", "danger"), spacing_before=2)
        self.ln_err.setVisible(False)
        page.add(table)

        self.ln_link_card = Card("Выданная ссылка",
                                 "QR для камеры телефона: покажи с экрана — "
                                 "сотрудник наведёт камеру и окажется внутри. "
                                 "Ту же панель открывает кнопка «Показать QR».")
        self.ln_link_body = self.ln_link_card.add(Rows(spacing=8))
        self.ln_link_body.set_empty(PANEL_EMPTY)
        page.add(self.ln_link_card)

        self._ln_selected(None)
        return page

    # ── загрузка ────────────────────────────────────────────────────────
    def load_links(self):
        def work():
            emps = self.api("GET", "/admin/employees").get("employees") or []
            try:
                links = self.api("GET", "/admin/app_links.json").get("links") or []
                fresh = True
            except RuntimeError:
                links, fresh = [], False   # старый сервер про ссылки не знает
            try:
                workers = self.api("GET", "/admin/activity").get("workers") or []
            except RuntimeError:
                workers = []               # ... и про активность тоже
            return emps, links, workers, fresh

        def done(res):
            self._loaded.add("links")
            emps, links, workers, self._ln_fresh = res
            self._ln_people = lk.merge_rows(emps, links, workers, self._ln_revoked)
            self._ln_by_name = {p["employee"]: p for p in self._ln_people}
            self._ln_render()

        self.bg_task(work, done)

    def _ln_render(self):
        people = self._ln_people
        counts = lk.filter_counts(people)
        self.ln_filters.set_chips(
            [(f"{title} · {counts.get(key, 0)}", key == self._ln_filter,
              lambda k=key: self._ln_set_filter(k)) for key, title in lk.LINK_FILTERS])
        self.ln_tv.set_rows([lk.link_row(p) for p in people
                             if lk.matches(p, self._ln_filter)])
        self.ln_tv.set_filter(self.ln_search.get())
        self.ln_tiles.set_pairs(lk.summary_tiles(people), cols=4)
        self.ln_summary.setText(lk.link_summary(people))
        self._ln_say(self.ln_note, "" if self._ln_fresh else OLD_SERVER_TEXT)
        # Выбор не должен убегать из-под руки: оператор выдаёт вход подряд
        # пятерым, и после каждой выдачи список перечитывается.
        self._ln_selected(None)
        if self._ln_pick_name and not self.ln_tv.select_key(self._ln_pick_name):
            self._ln_pick_name = ""

    def _ln_set_filter(self, key):
        self._ln_filter = key
        self._ln_render()

    @staticmethod
    def _ln_say(note: Note, text: str) -> None:
        """Подпись, которой не видно, когда сказать нечего."""
        note.setText(text)
        note.setVisible(bool(text))

    # ── выбранная строка ────────────────────────────────────────────────
    def _ln_selected(self, row):
        data = self._ln_by_name.get(row[0]) if row else None
        self._ln_pick_name = data["employee"] if data else self._ln_pick_name
        on = bool(data)
        has_url = on and bool(lk.link_url(data, self._ln_base))
        alive = on and bool(data.get("alive"))
        # «Выдать вход» и «Выдать заново» — одно действие, но разные вопросы:
        # пока живой ссылки нет, перевыдавать нечего и спрашивать не о чем.
        self.ln_issue_btn.setEnabled(on and not alive)
        self.ln_again_btn.setEnabled(on and bool(data.get("code")))
        self.ln_qr_btn.setEnabled(has_url)
        self.ln_copy_btn.setEnabled(has_url)
        self.ln_revoke_btn.setEnabled(
            on and bool(data.get("telegram_id"))
            and bool(data.get("code") or data.get("entered")))
        self.ln_pick.setText(f"выбран: {data['employee']}" if data else "")

    def _ln_current(self) -> dict:
        row = self.ln_tv.selected_row()
        return self._ln_by_name.get(row[0], {}) if row else {}

    # ── действия на выбранную строку ────────────────────────────────────
    def ln_issue(self, again: bool = False):
        """Выдать вход выбранному человеку. `again` — с вопросом о перевыдаче."""
        data = self._ln_current()
        if not data:
            self.toast("Выбери строку в таблице", err=True)
            return
        if again and not self.ask_yes_no("Выдать заново", lk.reissue_text(data)):
            return
        kind = data.get("kind") or "agent"
        body = {"employee": data["employee"], "kind": kind,
                "uses": lk.USES_CHOICES[max(self.ln_uses.current(), 0)]}
        # Привязанному шлём его id: иначе сервер заведёт вторую служебную
        # личность на то же ФИО, и портфель разъедется на два аккаунта.
        if data.get("telegram_id"):
            body["telegram_id"] = data["telegram_id"]

        def done(d):
            if not isinstance(d, dict) or not d.get("url"):
                text = str((d or {}).get("error") or "Сервер не выдал ссылку")
                self._ln_say(self.ln_err, text)
                self.toast(text, err=True)
                return
            self._ln_say(self.ln_err, "")
            self._ln_base = lk.base_of(d["url"]) or self._ln_base
            self.settings["miniapp"] = self._ln_base
            self._ln_revoked -= {str(d.get("telegram_id")), data["employee"]}
            self._ln_pick_name = data["employee"]
            self._ln_show_panel(lk.fresh_row(d, employee=data["employee"], kind=kind))
            self.toast(f"Вход выдан: {data['employee']}")
            # Точечно свой раздел, а не общий refresh: тот схлопывает экран и
            # теряет и выбранную строку, и только что показанный QR.
            self.load_links()

        self.bg_task(lambda: self.api("POST", "/admin/app_link.json", body), done)

    def ln_reissue(self):
        self.ln_issue(again=True)

    def _ln_show_qr(self):
        data = self._ln_current()
        url = lk.link_url(data, self._ln_base)
        if not url:
            self.toast(NO_URL_TEXT, err=True)
            return
        self._ln_show_panel(data)
        dialogs.show_link(f"Ссылка входа · {data['employee']}", url,
                          lk.issue_note(data), parent=self)

    def _ln_copy(self):
        data = self._ln_current()
        url = lk.link_url(data, self._ln_base)
        if not url:
            self.toast(NO_URL_TEXT, err=True)
            return
        dialogs.copy_text(url)
        self._ln_show_panel(data)
        self.toast(f"Ссылка скопирована: {data['employee']}")

    def _ln_revoke(self):
        data = self._ln_current()
        tid = data.get("telegram_id") if data else None
        if not tid:
            self.toast("У этого человека нет ни ссылки, ни входа — отзывать нечего",
                       err=True)
            return
        if not self.ask_yes_no("Отозвать вход", lk.revoke_text(data)):
            return

        def done(_d):
            # Сервер отозванные ссылки удаляет, поэтому помним об отзыве сами:
            # иначе строка сразу после отзыва скажет «не выдавали». Помним и по
            # имени: у человека может не остаться ни ссылки, ни привязки.
            self._ln_revoked |= {str(tid), data["employee"]}
            self._ln_pick_name = data["employee"]
            if self._ln_shown.get("employee") == data["employee"]:
                self._ln_show_panel({})
            self.toast(f"Вход отозван: {data['employee']}")
            self.load_links()

        self.bg_task(lambda: self.api("POST", "/admin/app_revoke",
                                      {"telegram_id": tid, "scope": "all"}), done)

    # ── панель выданной ссылки ──────────────────────────────────────────
    def _ln_show_panel(self, data: dict):
        body = self.ln_link_body
        body.clear()
        url = lk.link_url(data or {}, self._ln_base)
        if not url:
            self._ln_shown = {}
            body.set_empty(PANEL_EMPTY)
            return
        self._ln_shown = data
        body.add(Note(f"{data.get('employee') or '—'} · {lk.state_text(data)}", "title"))
        body.add(dialogs.link_panel(url, lk.issue_lines(data), size=320, side=True))
        page = self.pages.get("links")
        if page is not None:
            page.scroll_to(self.ln_link_card)
