"""Клиент сервера «Хард» для пульта руководителя.

Здесь одна мысль: руководитель не инженер. Он не должен увидеть ни трассировку,
ни «HTTP 502», ни английское `employee`. Любая беда превращается в русскую
строку, которую можно прочитать вслух аналитику по телефону, и делится на три
разных случая — потому что лечатся они по-разному:

* «нет связи» — виноват интернет, надо подождать; данные на экране остаются;
* «токен протух» — виновата ссылка входа, нужна новая от аналитика;
* «сервер ответил ошибкой» — виноват «Хард», человеку остаётся сообщить о ней.

Последний удачный ответ каждой ручки лежит в памяти. Пропала связь — экран
показывает вчерашнюю правду и честно подписывает: «связь потеряна в 14:32».
Пустой экран в такой момент хуже устаревшего: по устаревшему хотя бы видно,
что было вчера, а пустой означает «всё пропало».
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from urllib.parse import urlsplit

import aiohttp

from .settings import active_url, server_entry

# Ручки «Харда» за нашими экранами. Больше пульт никуда не ходит.
PATHS = {
    "today": "/api/manager/summary",
    "employee": "/api/manager/employee",
    "promises": "/api/manager/promises",
    "stats": "/api/manager/stats",
    "report": "/api/manager/stats.xlsx",
}

# Код в ссылке входа: 12 случайных символов (server/db.py). Рамки широкие —
# проверяем только то, что это не кусок чужого текста и не путь.
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_URL_RE = re.compile(r"https?://", re.I)

NO_LINK = ("Пульт ещё не знает, кто вы. Вставьте ссылку входа — ту, "
           "что выдал аналитик.")
OFFLINE = ("Нет связи с сервером «Хард». Проверьте интернет — как только он "
           "появится, пульт догонит сам.")
DEAD_TOKEN = ("Ссылка входа больше не действует. Попросите у аналитика новую "
              "и вставьте её здесь.")


def now_hhmm() -> str:
    """Время для строки «связь потеряна в 14:32» — местное, как у человека."""
    return datetime.now().strftime("%H:%M")


class HardTrouble(Exception):
    """Беда со стороны «Харда», уже переведённая на русский.

    `kind` — для пульта (что делать), `text` — для человека (что случилось).
    """

    def __init__(self, kind: str, text: str, status: int = 0) -> None:
        super().__init__(text)
        self.kind = kind          # no_link | offline | auth | server
        self.text = text
        self.status = status


def split_link(raw: str) -> tuple[str, str]:
    """Ссылка входа → (адрес сервера, код). Пустой адрес — «код без адреса».

    Человек вставляет что придётся: полную ссылку из сообщения, ссылку с
    решёткой из старой рассылки или один код, продиктованный по телефону.
    Ругаться на форму — значит оставить его перед экраном, на котором ничего
    не происходит; разбираем все три вида.
    """
    text = (raw or "").strip().strip("<>")
    text = text.strip('"').strip("'").strip()
    if not text:
        raise HardTrouble("no_link", "Ссылка пустая — вставьте её целиком.")
    if _URL_RE.match(text):
        parts = urlsplit(text)
        base = f"{parts.scheme}://{parts.netloc}"
        code = ""
        for sep in ("/i/", "/#link=", "/#t="):
            i = text.find(sep)
            if i > 0:
                code = text[i + len(sep):]
                break
        if not code:
            frag = parts.fragment or ""
            for sep in ("link=", "t="):
                if frag.startswith(sep):
                    code = frag[len(sep):]
                    break
        code = code.split("&")[0].split("#")[0].split("?")[0].strip("/")
        if not CODE_RE.match(code):
            raise HardTrouble(
                "no_link",
                "В этой ссылке нет кода входа. Нужна та, что заканчивается "
                "на «/i/…» — попросите её у аналитика.")
        return base, code
    if CODE_RE.match(text):
        return "", text
    raise HardTrouble(
        "no_link",
        "Это не похоже на ссылку входа. Вставьте её целиком, вместе "
        "с «https://».")


def normalize(payload: dict) -> dict:
    """Ответ «Харда» → форма, обещанная странице.

    Сервер отдаёт дату четырьмя плоскими полями (`date`, `today`, `prev_date`,
    `next_date`), а контракт страницы просит одним предметом `date`. Сводим
    здесь, а не в браузере: страница одна, а ручек, отдающих конверт, четыре, и
    разбирать конверт четырьмя способами — это четыре места, где он разъедется.
    Исходные поля остаются на месте, ничего не выбрасываем.
    """
    if not isinstance(payload, dict):
        return {"error": "«Хард» ответил не тем, чего пульт ждал."}
    out = dict(payload)
    iso = out.get("date")
    if not isinstance(iso, dict):
        out["date_iso"] = iso or ""
        out["date"] = {"iso": iso or "", "today": out.get("today") or "",
                       "prev": out.get("prev_date") or "",
                       "next": out.get("next_date") or ""}
    mgr = out.get("manager") if isinstance(out.get("manager"), dict) else {}
    if not out.get("manager_name"):
        out["manager_name"] = mgr.get("name") or ""
    if not out.get("scope"):
        out["scope"] = mgr.get("scope") or ""
    return out


class Hard:
    """Один клиент на весь пульт: своя сессия, свой кэш, своя память о беде."""

    def __init__(self, settings, timeout: float = 25.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, dict] = {}     # ключ ручки → {"data":…, "at":…}
        self._lost_at: str | None = None      # когда связь пропала, «14:32»
        self._dead = False                    # токен отвергнут сервером
        mgr = server_entry(settings).get("manager")
        self.manager = dict(mgr) if isinstance(mgr, dict) else {"name": "", "scope": ""}

    # --- состояние ---------------------------------------------------------
    @property
    def base(self) -> str:
        return active_url(self.settings)

    @property
    def token(self) -> str:
        return str(server_entry(self.settings).get("token") or "")

    @property
    def ready(self) -> bool:
        """Пульт знает, кто мы, и сервер этого ещё не опроверг."""
        return bool(self.base and self.token and not self._dead)

    @property
    def lost_at(self) -> str:
        return self._lost_at or ""

    def forget(self) -> None:
        self.settings.forget()
        self._cache.clear()
        self._lost_at = None
        self._dead = False
        self.manager = {"name": "", "scope": ""}

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # --- сеть --------------------------------------------------------------
    async def _http(self) -> aiohttp.ClientSession:
        """Сессия создаётся внутри работающего цикла событий, а не в __init__:
        пульт собирается до старта сервера, и сессия, рождённая не в том цикле,
        падает на первом же запросе."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _trouble(self, status: int, body: bytes) -> HardTrouble:
        """Код ответа → то, что человек может прочитать и понять, что делать."""
        if status == 403:
            # 403 у «Харда» значит «эта учётка не руководитель» — отдельный
            # случай: новая ссылка тут не поможет, помогает только аналитик.
            return HardTrouble(
                "auth",
                "Сервер не считает вас руководителем направления. "
                "Это чинит аналитик — попросите выдать роль.", status)
        if status == 401:
            return HardTrouble("auth", DEAD_TOKEN, status)
        if status == 410:
            return HardTrouble(
                "auth",
                "Ссылка уже использована или просрочена. Попросите "
                "у аналитика новую.", status)
        if status == 404:
            return HardTrouble(
                "server",
                "Сервер не нашёл, что показать: сотрудника нет в вашей "
                "команде или такой ручки у сервера нет.", status)
        if status == 400:
            return HardTrouble(
                "server", "Сервер не понял запрос пульта — сообщите аналитику.",
                status)
        if status == 429:
            return HardTrouble(
                "server", "Сервер просит подождать: слишком много запросов "
                "подряд. Попробуйте через минуту.", status)
        if status >= 500:
            return HardTrouble(
                "server",
                f"Сервер «Хард» сломался на этом запросе (код {status}). "
                "Сообщите аналитику — сам он об этом не узнает.", status)
        detail = (body or b"")[:120].decode("utf-8", "replace").strip()
        return HardTrouble(
            "server",
            f"Сервер ответил отказом (код {status}). {detail}".strip(), status)

    async def _request(self, method: str, url: str, *, params: dict | None = None,
                       payload: dict | None = None, auth: bool = True,
                       timeout: float | None = None) -> dict:
        if auth and not self.token:
            raise HardTrouble("no_link", NO_LINK)
        headers = {"Accept": "application/json"}
        if auth:
            headers["X-App-Token"] = self.token
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        session = await self._http()
        try:
            async with session.request(
                    method, url, params=clean or None, json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout or self.timeout)) as resp:
                body = await resp.read()
                status = resp.status
        except asyncio.TimeoutError:
            raise HardTrouble(
                "offline",
                "Сервер «Хард» не ответил вовремя. Связь плохая или сервер "
                "занят — на экране то, что удалось получить в прошлый раз.")
        except aiohttp.ClientError:
            raise HardTrouble("offline", OFFLINE)
        if status >= 400:
            raise self._trouble(status, body)
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HardTrouble(
                "server",
                "Сервер «Хард» ответил не по-нашему — возможно, вместо него "
                "отвечает чужая страница входа. Проверьте адрес.")
        if not isinstance(data, dict):
            raise HardTrouble("server", "Сервер «Хард» ответил не тем, чего ждал пульт.")
        return data

    # --- вход --------------------------------------------------------------
    async def redeem(self, raw: str) -> dict:
        """Ссылка входа → токен. Возвращает {ok, days}; беда — HardTrouble."""
        base, code = split_link(raw)
        base = (base or self.base).rstrip("/")
        if not base:
            raise HardTrouble(
                "no_link",
                "Пульт не знает адреса сервера. Вставьте ссылку целиком, "
                "вместе с «https://».")
        body: dict = {"code": code, "standalone": True}
        # Свой ещё живой токен отдаём серверу обратно: если ссылка принадлежит
        # тому же человеку, обмен не потратится, и повторная вставка ссылки
        # (а её вставляют повторно почти всегда) не сожжёт последнее открытие.
        if self.token and base == self.base:
            body["token"] = self.token
        data = await self._request("POST", base + "/api/redeem",
                                   payload=body, auth=False, timeout=20)
        token = str(data.get("token") or "")
        if not token:
            raise HardTrouble("auth", "Сервер не дал доступа по этой ссылке.")
        # Ссылка сама называет сервер: заводим под него запись и делаем
        # активным. Токен прежнего сервера остаётся при нём — переключение
        # обратно не потребует новой ссылки.
        entry = self.settings["servers"].setdefault(base, {})
        entry["token"] = token
        entry["days"] = data.get("days")
        self.settings["url"] = base
        self.settings.save()
        self._dead = False
        self._cache.clear()
        return {"ok": True, "days": data.get("days")}

    def switch(self, url: str) -> None:
        """Переключиться на уже известный сервер. Новый заводит только ссылка."""
        url = str(url or "").rstrip("/")
        if url not in (self.settings.get("servers") or {}):
            raise HardTrouble("no_link",
                              "Этот сервер пульту не знаком — войдите в него "
                              "ссылкой входа.")
        self.settings["url"] = url
        self.settings.save()
        self._cache.clear()
        self._lost_at = None
        self._dead = False
        mgr = server_entry(self.settings).get("manager")
        self.manager = dict(mgr) if isinstance(mgr, dict) else {"name": "", "scope": ""}

    # --- данные ------------------------------------------------------------
    def _remember_manager(self, data: dict) -> None:
        """Имя и охват руководителя приезжают в конверте любой ручки. Запоминаем
        их и в файл: иначе экран «Я и телефон» пуст до первого ответа сервера,
        а это первое, что руководитель открывает после запуска."""
        mgr = data.get("manager") if isinstance(data.get("manager"), dict) else {}
        name = str(mgr.get("name") or data.get("manager_name") or "")
        scope = str(mgr.get("scope") or data.get("scope") or "")
        if not name and not scope:
            return
        fresh = {"name": name, "scope": scope}
        if fresh != self.manager:
            self.manager = fresh
            server_entry(self.settings)["manager"] = fresh
            self.settings.save()

    async def call(self, screen: str, params: dict | None = None) -> dict:
        """Данные экрана. Всегда возвращает словарь для страницы — с данными,
        с устаревшими данными и подписью или (когда показать нечего) с `error`.
        """
        path = PATHS[screen]
        params = params or {}
        key = screen + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        try:
            data = await self._request("GET", self.base + path, params=params)
        except HardTrouble as trouble:
            if trouble.kind == "auth":
                self._dead = True      # страница сама покажет экран входа
            elif trouble.kind == "offline" and not self._lost_at:
                self._lost_at = now_hhmm()
            kept = self._cache.get(key)
            if kept is None:
                return {"error": trouble.text, "kind": trouble.kind,
                        "stale": False, "ready": self.ready}
            return {**kept["data"], "error": trouble.text, "kind": trouble.kind,
                    "stale": True, "lost_at": self._lost_at or kept["at"],
                    "fresh_at": kept["at"], "ready": self.ready}
        self._lost_at = None
        self._dead = False
        self._remember_manager(data)
        fresh = normalize(data)
        self._cache[key] = {"data": fresh, "at": now_hhmm()}
        return {**fresh, "stale": False, "lost_at": "", "fresh_at": now_hhmm()}

    async def book(self, params: dict | None = None):
        """Книга Excel за период. Отдаёт открытый ответ «Харда» — пульт
        переливает его в браузер кусками и не держит книгу в памяти целиком:
        за месяц по большому направлению это десятки мегабайт, а пульт живёт
        на рабочем ноутбуке рядом с Excel и почтой.

        Вызывающий ОБЯЗАН закрыть ответ (`resp.release()`).
        """
        if not self.ready:
            raise HardTrouble("no_link", NO_LINK)
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        session = await self._http()
        try:
            resp = await session.get(
                self.base + PATHS["report"], params=clean or None,
                headers={"X-App-Token": self.token},
                timeout=aiohttp.ClientTimeout(total=300, sock_read=120))
        except asyncio.TimeoutError:
            raise HardTrouble("offline", "Сервер «Хард» не успел собрать книгу.")
        except aiohttp.ClientError:
            raise HardTrouble("offline", OFFLINE)
        if resp.status >= 400:
            body = await resp.read()
            resp.release()
            trouble = self._trouble(resp.status, body)
            if trouble.kind == "auth":
                self._dead = True
            raise trouble
        return resp
