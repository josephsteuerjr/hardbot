"""Раздел «Вход»: персональные ссылки PWA — выдать и дождаться входа.

В контуре компании Telegram нет, и телефон сотрудника попадает в систему
личной ссылкой аналитика: ссылка уже знает ФИО, самовыбора не будет.
Здесь ссылку выдают (окно с QR и копированием) и видят, кто ещё не вошёл."""

from __future__ import annotations

from ..core import activity as act
from ..core.people import KIND_LABELS, kind_from_label, kind_index
from ..core.text import fmt_n
from .widgets import Button, Card, Combo, Note, Page, Rows, label


class LinksSection:
    def build_links(self) -> Page:
        page = Page()

        give = Card("Выдать ссылку входа",
                    "ФИО берутся из реестра. Ссылка персональная: открывший "
                    "попадает сразу в свой портфель, без выбора имени. Покажи QR "
                    "с экрана или перешли ссылку — на телефоне попроси «Добавить "
                    "на экран “Домой”», дальше человек работает сам, даже без связи.")
        self.ln_emp = Combo([], width_chars=42)
        # синхронизация типа — на любое изменение выбора, не только «человеком»:
        # и запуск load_links, и клик мышью должны подтянуть тип из реестра
        self.ln_emp.currentIndexChanged.connect(lambda _i: self._ln_sync_kind())
        self.ln_kind = Combo(KIND_LABELS, width_chars=12)
        give.add_row(self.ln_emp, self.ln_kind,
                     Button("Выдать ссылку", self.ln_issue, kind="accent"),
                     stretch=False, spacing_before=2)
        self.ln_state = give.add(Note("", "small"))
        page.add(give)
        self._ln_emps: list[dict] = []

        wait = Card("Ожидают первого входа",
                    "Привязанные сотрудники, которые ни разу не открыли "
                    "приложение. Выдай ссылку — строка уйдёт после первого входа.")
        self.ln_summary = wait.add(Note("", "small"))
        self.ln_frame = wait.add(Rows(spacing=4))
        page.add(wait)
        return page

    def load_links(self):
        def work():
            emps = self.api("GET", "/admin/employees").get("employees") or []
            try:
                workers = self.api("GET", "/admin/activity").get("workers") or []
            except RuntimeError:
                workers = []       # старый сервер про активность не знает
            return emps, workers

        def done(res):
            self._loaded.add("links")
            emps, workers = res
            self._ln_emps = emps
            self.ln_emp.set_values([e["employee"] for e in emps])
            if emps:
                self.ln_emp.set_current(0)
                self._ln_sync_kind()
                self.ln_state.setText(f"в реестре {len(emps)} ответственных")
            else:
                self.ln_state.setText("Реестр пуст — импортируй xlsx в разделе «Реестр»")
            self._ln_render_waiting(workers)

        self.bg_task(work, done)

    def _ln_sync_kind(self):
        """Тип из реестра подставляется при выборе, но остаётся правимым:
        реестр знает не всех (курьеры ГПХ в списке не всегда)."""
        name = self.ln_emp.get()
        for e in self._ln_emps:
            if e.get("employee") == name:
                self.ln_kind.set_current(kind_index(e))
                return

    def _ln_render_waiting(self, workers):
        waiting = [w for w in workers if act.matches(w, "never", 0)]
        entered = len(workers) - len(waiting)
        frame = self.ln_frame
        frame.clear()
        if not workers:
            self.ln_summary.setText("привязанных ещё нет")
            frame.set_empty("Выдай первую ссылку — человек появится здесь")
            return
        self.ln_summary.setText(
            f"зашли {entered} из {len(workers)} · не заходили {len(waiting)}")
        if not waiting:
            frame.set_empty("Все привязанные уже заходили")
            return
        for w in waiting:
            frame.add_row(
                label(w.get("employee", "")),
                Note("курьер" if w.get("kind") == "courier" else "выездник",
                     "badge", wrap=False),
                Note(f"договоров {fmt_n(w.get('portfolio_contracts'))}",
                     "small", wrap=False),
                None,
                Button("Выдать ссылку",
                       lambda e=w.get("employee", ""): self.issue_link(employee=e)),
                stretch=False)

    def ln_issue(self):
        emp = (self.ln_emp.get() or "").strip()
        if not emp:
            self.toast("Выбери сотрудника из реестра", err=True)
            return
        self.issue_link(employee=emp, kind=kind_from_label(self.ln_kind.get()))
