"""Кто стучится в пульт: свой браузер на этом ПК или телефон из тоннеля.

Тоннельный адрес публичный. Его знает cloudflare, он мелькает в логах и в
истории браузера, и поддомен можно угадать. Поэтому снаружи пульт закрыт
кодом-пропуском, а внутри — открыт: заставлять руководителя вводить код в
браузере собственного компьютера незачем, он и так уже за этим компьютером.

ГЛАВНАЯ ЛОВУШКА. cloudflared стоит на той же машине и ходит в пульт с
127.0.0.1 — по адресу источника телефон неотличим от своего браузера. Отличает
их заголовок Host: свой браузер открывает `127.0.0.1:<порт>`, телефон —
`что-то.trycloudflare.com`; плюс тоннель добавляет свои заголовки (Cf-Ray,
Cf-Connecting-Ip, X-Forwarded-For). Проверяем всё вместе и закрываемся при
любом сомнении: ошибиться в сторону «спросить код» не страшно, ошибиться в
другую — значит отдать портфель направления тому, кто угадал поддомен.

Заодно отсюда же закрыт старый трюк с перепривязкой имени (чужой сайт с
именем, которое смотрит на 127.0.0.1): Host, которого мы не знаем, — отказ.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime

COOKIE = "pult_ruk"
MAX_FAILS = 5          # шестой попытки не будет: тоннель гаснет на пятой

# Имена, под которыми пульт знает сам себя. Порт не проверяем: он случайный,
# а от подмены имени защищает как раз имя.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Заголовки, которые ставит любой посредник. Если такой есть — запрос пришёл
# не из соседнего окна, а издалека, чем бы ни притворялся адрес источника.
FORWARDED = ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
             "X-Real-Ip", "Forwarded", "Cf-Connecting-Ip", "Cf-Ray",
             "Cf-Ipcountry", "Cf-Visitor")


def make_pin() -> str:
    """Четыре цифры, которые человек продиктует себе же на телефон.

    Четыре — потому что вводить их на телефоне, глядя на экран ПК, и потому что
    больше четырёх никто не перенесёт без ошибки. Слабость длины закрыта не
    длиной, а счётчиком: пять промахов — и тоннеля больше нет.
    """
    return f"{secrets.randbelow(10000):04d}"


def host_name(host: str) -> str:
    """«127.0.0.1:53124» → «127.0.0.1»; «[::1]:8080» → «[::1]»."""
    h = (host or "").strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[:end + 1] if end > 0 else h
    return h.split(":")[0]


class Gate:
    """Пропускной пункт: код, сессии телефонов и счётчик промахов."""

    def __init__(self, on_raid=None, max_fails: int = MAX_FAILS) -> None:
        self.pin = make_pin()
        self.sessions: set[str] = set()
        self.fails = 0
        self.max_fails = max_fails
        self.alarm = ""            # что показать владельцу на экране ПК
        self.locked = False
        self.tunnel_host = ""      # имя живого тоннеля, пока он жив
        self._on_raid = on_raid

    # --- кто пришёл --------------------------------------------------------
    def is_console(self, request) -> bool:
        """Свой браузер на этом же ПК. Любое сомнение — «нет»."""
        if any(request.headers.get(h) for h in FORWARDED):
            return False
        if host_name(request.headers.get("Host") or request.host) not in LOOPBACK:
            return False
        remote = (request.remote or "").strip().lower()
        return remote in {"127.0.0.1", "::1", ""}

    def known_host(self, request) -> bool:
        """Имя, под которым нас открыли, — наше собственное.

        Свой адрес, живой тоннель — и всё. Чужое имя, ведущее на 127.0.0.1,
        означает, что пульт открыли не тем, чем думают.
        """
        host = host_name(request.headers.get("Host") or request.host)
        return host in LOOPBACK or (bool(self.tunnel_host)
                                    and host == host_name(self.tunnel_host))

    def has_session(self, request) -> bool:
        cookie = request.cookies.get(COOKIE) or ""
        if not cookie:
            return False
        # Сравнение постоянного времени: подбор куки по времени ответа — не
        # фантастика, а стандартный приём, а сессий у нас единицы.
        return any(hmac.compare_digest(cookie, live) for live in self.sessions)

    def allowed(self, request) -> bool:
        return self.is_console(request) or self.has_session(request)

    # --- код-пропуск -------------------------------------------------------
    def check(self, value: str) -> tuple[bool, str, str]:
        """(пустили?, текст для человека, ключ сессии).

        Текст пишем сразу здесь: «неверный ПИН» на экране телефона бесполезен,
        человеку нужно знать, сколько попыток осталось и что будет дальше.
        """
        if self.locked:
            return False, ("Слишком много неверных попыток — телефон отключён. "
                           "Включите его заново на компьютере."), ""
        given = "".join(ch for ch in str(value or "") if ch.isdigit())
        if given and hmac.compare_digest(given, self.pin):
            self.fails = 0
            key = secrets.token_urlsafe(32)
            self.sessions.add(key)
            return True, "", key
        self.fails += 1
        left = self.max_fails - self.fails
        if left <= 0:
            self.lock("ввели неверный код пять раз")
            return False, ("Код не подошёл пять раз. Телефон отключён — "
                           "включите его заново на компьютере."), ""
        tail = "попытка" if left == 1 else ("попытки" if left < 5 else "попыток")
        return False, f"Код не подошёл. Осталось {left} {tail}.", ""

    def lock(self, why: str) -> None:
        """Погасить доступ снаружи и сказать владельцу, что происходило."""
        self.locked = True
        self.sessions.clear()
        self.alarm = (f"{datetime.now().strftime('%H:%M')}: в пульт ломились "
                      f"с телефона — {why}. Тоннель погашен.")
        if self._on_raid:
            self._on_raid(self.alarm)

    def reopen(self) -> None:
        """Новый тоннель — новый код и чистый счётчик: старый код мог уже
        уехать не туда, а «включить заново» человек делает именно затем, чтобы
        сломанное перестало действовать."""
        self.pin = make_pin()
        self.fails = 0
        self.locked = False
        self.sessions.clear()

    def close(self) -> None:
        """Тоннель погас — вместе с ним умирают и сессии телефонов."""
        self.sessions.clear()
        self.tunnel_host = ""

    # --- кука --------------------------------------------------------------
    def put_cookie(self, response, key: str) -> None:
        """HttpOnly — чтобы ключ сессии не достался скрипту на странице;
        Secure — потому что снаружи нас открывают только по https через
        тоннель; Lax — чтобы чужая страница не могла ходить сюда от имени
        телефона. Срок не ставим: сессия живёт, пока жив пульт."""
        response.set_cookie(COOKIE, key, httponly=True, secure=True,
                            samesite="Lax", path="/")

    def drop_cookie(self, response) -> None:
        response.del_cookie(COOKIE, path="/")
