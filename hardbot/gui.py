"""
Пульт аналитика «Контур · Выезды» (Tkinter).

Модель без ролей: выездники сами выбирают себя в боте по «ответственному
Сотруднику» из загруженного реестра. Пульт: конфиг, импорт/экспорт реестра,
обзор сотрудников и привязок (кто себя выбрал), запуск/остановка бота, дев-режим.

Запуск: python gui.py  (или двойной клик run.bat)
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from dotenv import dotenv_values

import db
import synth
import theme as T

APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / ".env"


class ControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Контур · Выезды")
        self.configure(bg=T.WINDOW)
        self.geometry("900x780")
        self.minsize(820, 640)

        db.init_db()
        self.proc: subprocess.Popen | None = None
        self.log_q: queue.Queue[str] = queue.Queue()

        self._build()
        self._load_env()
        self._refresh_bindings()
        self._refresh_counts()
        self.after(200, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, lambda: T.apply_titlebar(self))

    # ── каркас ─────────────────────────────────────────────────────────
    def _build(self):
        header = tk.Frame(self, bg=T.WINDOW)
        header.pack(fill="x", padx=24, pady=(18, 4))
        tk.Label(header, text="Контур · Выезды", bg=T.WINDOW, fg=T.TEXT,
                 font=T.serif(20)).pack(anchor="w")
        tk.Label(header, text="Пульт аналитика: настройка, реестр, сотрудники и запуск бота.",
                 bg=T.WINDOW, fg=T.MUTED, font=T.f(10)).pack(anchor="w", pady=(2, 0))

        canvas = tk.Canvas(self, bg=T.WINDOW, highlightthickness=0)
        scroll = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = tk.Frame(canvas, bg=T.WINDOW)
        self.body.bind("<Configure>",
                       lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(24, 0))
        scroll.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        self._build_config()
        self._build_registry()
        self._build_bindings()
        self._build_bot()
        self._build_dev()

    def _card(self, title: str) -> tk.Frame:
        outer = tk.Frame(self.body, bg=T.WINDOW)
        outer.pack(fill="x", pady=(0, 16), padx=(0, 24))
        c = T.card(outer)
        c.pack(fill="x")
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="x", padx=18, pady=16)
        T.caption(inner, title).pack(anchor="w", pady=(0, 10))
        return inner

    # ── 1. Конфигурация ────────────────────────────────────────────────
    def _build_config(self):
        c = self._card("Конфигурация")
        self.e_token = self._field(c, "BOT_TOKEN (от @BotFather)")
        self.e_admin = self._field(c, "ADMIN_ID — ваш telegram-id (всегда доступ)")
        self.e_group = self._field(c, "GROUP_CHAT_ID — беседа (можно оставить пустым: бот подхватит сам)")
        self.lbl_group = T.label(c, "", fg=T.MUTED, font=T.f(9))
        self.lbl_group.pack(anchor="w", pady=(4, 0))
        row = tk.Frame(c, bg=T.CARD)
        row.pack(fill="x", pady=(8, 0))
        T.button(row, "Сохранить конфиг", self._save_env).pack(side="left")
        self.lbl_cfg = T.label(row, "", fg=T.SUCCESS)
        self.lbl_cfg.pack(side="left", padx=12)

    def _field(self, parent, caption_text: str) -> tk.Entry:
        T.label(parent, caption_text, fg=T.MUTED, font=T.f(9)).pack(anchor="w", pady=(6, 2))
        e = T.entry(parent)
        e.pack(fill="x", ipady=5)
        return e

    def _load_env(self):
        vals = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
        for entry, key in ((self.e_token, "BOT_TOKEN"), (self.e_admin, "ADMIN_ID"),
                           (self.e_group, "GROUP_CHAT_ID")):
            entry.delete(0, "end")
            entry.insert(0, vals.get(key, "") or "")
        accepted = db.get_setting("group_chat_id")
        self.lbl_group.config(
            text=f"принятая беседа: {accepted}" if accepted else "беседа ещё не принята")

    def _save_env(self):
        token, admin, group = (self.e_token.get().strip(), self.e_admin.get().strip(),
                               self.e_group.get().strip())
        ENV_PATH.write_text(
            f"# Токен от @BotFather\nBOT_TOKEN={token}\n\n"
            f"# Telegram-id владельца — всегда доступ\nADMIN_ID={admin}\n\n"
            f"# id беседы (необязательно, бот подхватит сам)\nGROUP_CHAT_ID={group}\n",
            encoding="utf-8")
        if admin.isdigit():
            db.ensure_owner(int(admin))
        self.lbl_cfg.config(text="сохранено ✓ (бот перезапустить)")

    # ── 2. Реестр ──────────────────────────────────────────────────────
    def _build_registry(self):
        c = self._card("Реестр (база данных)")
        self.lbl_counts = T.label(c, "", fg=T.MUTED, font=T.f(9))
        self.lbl_counts.pack(anchor="w", pady=(0, 8))
        row = tk.Frame(c, bg=T.CARD)
        row.pack(fill="x")
        T.button(row, "Импорт xlsx…", self._import).pack(side="left")
        T.button(row, "Экспорт xlsx…", self._export, kind="ghost").pack(side="left", padx=6)
        T.button(row, "Координаты (геокодинг)", self._geocode, kind="ghost").pack(side="left")
        T.button(row, "Экспорт меток", self._export_markers, kind="ghost").pack(side="left", padx=6)

    def _refresh_counts(self):
        s = db.stats_all()
        emps = db.employees_in_registry()
        binds = db.list_bindings()
        self.lbl_counts.config(
            text=f"договоров: {s['total']}   ·   сотрудников в реестре: {len(emps)}   ·   "
                 f"выбрали себя: {len(binds)}   ·   с визитом: {s['visited']}")

    def _import(self):
        path = filedialog.askopenfilename(
            title="Выгрузка реестра", filetypes=[("Excel", "*.xlsx *.xlsm"), ("Все", "*.*")])
        if not path:
            return
        try:
            rep = db.import_xlsx(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Контур", f"Не смог разобрать файл:\n{e}")
            return
        self._refresh_counts()
        self._refresh_bindings()
        unknown = ("\nнезнакомые колонки: " + ", ".join(rep["unknown_columns"][:6])) \
            if rep["unknown_columns"] else ""
        messagebox.showinfo("Контур", (
            f"Импорт завершён.\n\nвсего строк: {rep['total_rows']}\n"
            f"добавлено: {rep['inserted']}\nобновлено: {rep['updated']}\n"
            f"без КД: {rep['skipped_no_id']}   дублей: {rep['duplicates']}{unknown}"))

    def _export(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить реестр", defaultextension=".xlsx",
            initialfile="registry_export.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        db.export_xlsx(path)
        messagebox.showinfo("Контур", f"Сохранено:\n{path}")

    def _geocode(self):
        if not messagebox.askyesno("Контур", (
                "Найти координаты по адресам?\n\n"
                "Точные — через OpenStreetMap/Nominatim, аулы — через GeoNames.\n"
                "Точки, отмеченные выездниками на месте, не трогаются.\n"
                "Нужен интернет; прогресс — в логе ниже.")):
            return
        self._log("Геокодинг запущен…")

        def work():
            import geocode
            def prog(i, s):
                if i % 10 == 0 or i == s["total"]:
                    self.log_q.put(f"  …{i}/{s['total']}  точных:{s['precise']} "
                                   f"аулов:{s['aul']} нет:{s['fail']}")
            try:
                st = geocode.geocode_all(progress=prog)
                self.log_q.put(f"Геокодинг готов: точных {st['precise']}, аулов {st['aul']}, "
                               f"без координат {st['fail']}, пропущено {st['skip']}.")
            except Exception as e:  # noqa: BLE001
                self.log_q.put(f"Ошибка геокодинга: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _export_markers(self):
        path = filedialog.asksaveasfilename(
            title="Экспорт меток курьеров", defaultextension=".xlsx",
            initialfile="markers.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        db.export_markers(path)
        messagebox.showinfo("Контур", f"Метки сохранены:\n{path}")

    # ── 3. Сотрудники и привязки ───────────────────────────────────────
    def _build_bindings(self):
        c = self._card("Сотрудники и самовыбор")
        T.label(c, "Сотрудники из реестра (выездники выбирают себя сами в боте):",
                fg=T.MUTED, font=T.f(9)).pack(anchor="w", pady=(0, 4))
        self.lst_emps = tk.Listbox(c, height=4, bg=T.CARD_ALT, fg=T.TEXT, relief="flat",
                                   font=T.mono(9), highlightthickness=1,
                                   highlightbackground=T.BORDER, activestyle="none",
                                   selectbackground=T.ACCENT_SOFT, selectforeground=T.TEXT)
        self.lst_emps.pack(fill="x", pady=(0, 8))

        T.label(c, "Кто себя выбрал (привязки telegram_id → сотрудник):",
                fg=T.MUTED, font=T.f(9)).pack(anchor="w", pady=(4, 4))
        self.lst_binds = tk.Listbox(c, height=4, bg=T.CARD_ALT, fg=T.TEXT, relief="flat",
                                    font=T.mono(9), highlightthickness=1,
                                    highlightbackground=T.BORDER, activestyle="none",
                                    selectbackground=T.ACCENT_SOFT, selectforeground=T.TEXT)
        self.lst_binds.pack(fill="x", pady=(0, 8))
        row = tk.Frame(c, bg=T.CARD)
        row.pack(fill="x")
        T.button(row, "Сбросить выбор", self._unbind, kind="ghost").pack(side="left")
        T.button(row, "Обновить", self._refresh_bindings, kind="ghost").pack(side="left", padx=6)

    def _refresh_bindings(self):
        self.lst_emps.delete(0, "end")
        for e in db.employees_in_registry():
            self.lst_emps.insert("end", f"{e['employee']:<28} {e['contracts']} КД")
        self.lst_binds.delete(0, "end")
        for b in db.list_bindings():
            self.lst_binds.insert("end", f"{b['telegram_id']:<12} → {b['employee']}")
        if hasattr(self, "lbl_counts"):
            self._refresh_counts()

    def _unbind(self):
        sel = self.lst_binds.curselection()
        if not sel:
            return
        uid = int(self.lst_binds.get(sel[0]).split()[0])
        db.unbind(uid)
        self._refresh_bindings()

    # ── 4. Бот ─────────────────────────────────────────────────────────
    def _build_bot(self):
        c = self._card("Бот")
        row = tk.Frame(c, bg=T.CARD)
        row.pack(fill="x")
        T.button(row, "▶ Запустить бота", self._start_bot).pack(side="left")
        T.button(row, "■ Остановить", self._stop_bot, kind="danger").pack(side="left", padx=6)
        self.lbl_status = T.label(row, "● остановлен", fg=T.FAINT)
        self.lbl_status.pack(side="left", padx=12)
        self.txt_log = tk.Text(c, height=9, bg="#1E1C1A", fg="#E8E4DC", relief="flat",
                               font=T.mono(9), highlightthickness=1,
                               highlightbackground=T.BORDER, wrap="word", state="disabled")
        self.txt_log.pack(fill="x", pady=(10, 0))

    def _start_bot(self):
        if self.proc and self.proc.poll() is None:
            return
        if not ENV_PATH.exists() or not dotenv_values(ENV_PATH).get("BOT_TOKEN"):
            messagebox.showwarning("Контур", "Сначала впишите BOT_TOKEN и сохраните конфиг.")
            return
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [sys.executable, "app.py"], cwd=str(APP_DIR), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, encoding="utf-8",
            errors="replace", env=env, creationflags=flags)
        threading.Thread(target=self._read_proc, daemon=True).start()
        self.lbl_status.config(text="● работает", fg=T.SUCCESS)
        self._log("— бот запущен —")

    def _read_proc(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.log_q.put(line.rstrip())
        self.log_q.put("— бот остановлен —")

    def _stop_bot(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.lbl_status.config(text="● остановлен", fg=T.FAINT)

    def _drain_log(self):
        try:
            while True:
                self._log(self.log_q.get_nowait())
        except queue.Empty:
            pass
        if self.proc and self.proc.poll() is not None:
            self.lbl_status.config(text="● остановлен", fg=T.FAINT)
        self.after(300, self._drain_log)

    def _log(self, line: str):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", line + "\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    # ── 5. Дев-режим ───────────────────────────────────────────────────
    def _build_dev(self):
        c = self._card("Дев-режим (для тестов UX)")
        T.label(c, f"Сгенерирует 30 синтетических клиентов на сотрудника «{synth.DEV_EMPLOYEE}» "
                   "и привяжет вас (ADMIN_ID) к нему — сразу тестируете как выездник.",
                fg=T.MUTED, font=T.f(9), wraplength=760, justify="left").pack(
            anchor="w", pady=(0, 8))
        T.button(c, "🧪 Подготовить тестовую среду", self._dev_mode).pack(anchor="w")

    def _dev_mode(self):
        admin = self.e_admin.get().strip()
        if not admin.isdigit():
            messagebox.showwarning("Контур", "Впишите ADMIN_ID и сохраните конфиг.")
            return
        owner = int(admin)
        db.ensure_owner(owner)
        db.import_xlsx(synth.generate(n=30, responsible=synth.DEV_EMPLOYEE))
        db.unbind(owner)                       # чтобы привязка прошла заново
        db.bind_agent(owner, synth.DEV_EMPLOYEE)
        self._refresh_bindings()
        self._refresh_counts()
        messagebox.showinfo("Контур", "Готово: 30 клиентов, вы привязаны как "
                                      f"«{synth.DEV_EMPLOYEE}».\nЗапустите бота и /start в Телеграме.")

    def _on_close(self):
        self._stop_bot()
        self.destroy()


if __name__ == "__main__":
    ControlPanel().mainloop()
