"""Browser access to the primary registry for the designated Analyst."""

import argparse
from contextlib import closing, contextmanager
from hashlib import sha256, scrypt
import hmac
from html import escape
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode

from aiohttp import web

if __package__:
    from .analyst_registry_write import Catalog, RegistryConflict, card, change
else:
    from analyst_registry_write import Catalog, RegistryConflict, card, change


PREFIX = "/admin/analyst/"
COOKIE = "__Secure-rimborso-analyst"
PAGE_SIZE = 50
SESSION_SECONDS = 8 * 3600
FIELDS = {
    "contract_id": "Договор",
    "client": "Клиент",
    "partner": "Кредитор",
    "city": "Город",
    "responsible_soft": "Софт",
    "responsible": "Хард",
    "debt_today": "Долг на сегодня",
}
DETAIL = {**FIELDS, "debt_total": "Общий долг", "phone": "Телефон"}
SEARCH = ("contract_id", "client", "partner", "city", "responsible_soft", "responsible")


def password_hash(password, salt=None):
    salt = secrets.token_hex(16) if salt is None else salt
    verifier = scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 << 20)
    return salt + ":" + verifier.hex()


def matches(password, verifier):
    return hmac.compare_digest(password_hash(password, verifier.split(":", 1)[0]), verifier)


def html(value):
    return escape(str(value if value is not None else ""), quote=True)


def link(path="", **params):
    return PREFIX + path + ("?" + urlencode(params) if params else "")


def page(title, body, *, signed_in=False, status=200):
    nav = ""
    if signed_in:
        nav = (
            f'<nav><a href="{PREFIX}">Реестр</a><a href="{link("summary")}">Сводка</a>'
            f'<a href="{link("password")}">Пароль</a>'
            f'<form method="post" action="{link("logout")}"><button>Выйти</button></form></nav>'
        )
    return web.Response(
        text='<!doctype html><html lang="ru"><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width, initial-scale=1">'
             f'<title>{html(title)} · Аналитик</title><link rel="stylesheet" href="{PREFIX}style.css">'
             f'<header><a href="{PREFIX}">Аналитик</a>{nav}</header>'
             f'<main><h1>{html(title)}</h1>{body}</main></html>',
        content_type="text/html", status=status,
    )


class AnalystSite:
    def __init__(self, primary, state, origin, username, account_id, operator=None):
        self.primary = primary.resolve()
        self.state = state.resolve()
        self.origin = origin.rstrip("/")
        self.username = username
        self.account_id = account_id
        self.operator = operator
        self.catalog = Catalog() if operator else None

    @contextmanager
    def authority(self):
        with closing(sqlite3.connect(self.primary.as_uri() + "?mode=ro", uri=True, timeout=4)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.create_function("fold", 1, lambda value: str(value or "").casefold(), deterministic=True)
            conn.execute("BEGIN")
            yield conn

    @contextmanager
    def sessions(self):
        with closing(sqlite3.connect(self.state, timeout=4)) as conn, conn:
            yield conn

    def initialize(self, verifier):
        with self.sessions() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS credential (id INTEGER PRIMARY KEY CHECK (id=1), verifier TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS failures (at INTEGER NOT NULL);
            """)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO credential VALUES (1,?)", (verifier,))
            columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
            if "username" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN username TEXT")
                conn.execute("UPDATE sessions SET username=?", (self.username,))
            conn.execute("CREATE TABLE IF NOT EXISTS operator_credentials(username TEXT PRIMARY KEY, verifier TEXT NOT NULL)")
            if self.operator:
                conn.execute("INSERT OR IGNORE INTO operator_credentials VALUES (?,?)",
                             (self.operator["username"], self.operator["verifier"]))

    def authorized(self, conn, username):
        if self.operator and username == self.operator["username"]:
            row = conn.execute("SELECT name,role,direction FROM managers WHERE telegram_id=?",
                               (self.operator["uid"],)).fetchone()
            return bool(row and row["name"] == self.operator["name"] and
                        row["role"] == self.operator["directory_role"] and row["direction"] == "all")
        if username != self.username:
            return False
        return conn.execute(
            "SELECT 1 FROM accounts a JOIN account_admin_grants g ON g.account_id=a.id "
            "WHERE a.id=? AND a.username=? AND a.role='analyst' AND a.status='active' "
            "AND a.must_change_password=0 AND g.granted_by='ssh-root'",
            (self.account_id, self.username),
        ).fetchone() is not None

    def session(self, request):
        token = request.cookies.get(COOKIE, "")
        if len(token) != 43 or not token.isascii():
            return None
        digest = sha256(token.encode()).hexdigest()
        with self.sessions() as conn:
            row = conn.execute("SELECT username FROM sessions WHERE digest=? AND expires>?", (digest, time.time())).fetchone()
        return (digest, row[0]) if row else None

    def require_origin(self, request):
        if request.headers.get("Origin") != self.origin:
            raise web.HTTPForbidden(text="Запрос должен быть отправлен со страницы Аналитика.")

    def require_user(self, request, primary):
        session = self.session(request)
        if not session or not self.authorized(primary, session[1]):
            raise web.HTTPSeeOther(location=link("login"))
        return session

    def can_write(self, username):
        return bool(self.operator and username == self.operator["username"])

    def get_verifier(self, conn, username):
        owner = conn.execute("SELECT verifier FROM credential WHERE id=1").fetchone()[0]
        if self.can_write(username):
            return conn.execute("SELECT verifier FROM operator_credentials WHERE username=?", (username,)).fetchone()[0]
        return owner

    def login_form(self, error="", status=200):
        alert = f'<p role="alert">{html(error)}</p>' if error else ""
        return page("Вход", alert + f"""
            <form class="panel" method="post" action="{link('login')}">
              <label>Логин<input name="username" autocomplete="username" maxlength="64" required></label>
              <label>Веб-пароль<input name="password" type="password" autocomplete="current-password"
                    required maxlength="128"></label>
              <button>Войти</button>
            </form>
            """, status=status)

    async def login(self, request):
        if request.method == "GET":
            return self.login_form()
        self.require_origin(request)
        is_pwa = request.content_type == "application/json"
        data = await (request.json() if is_pwa else request.post())
        def feedback(message, status):
            return web.json_response({"text":message}, status=status) if is_pwa else self.login_form(message, status)
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 128:
            return feedback("Введите логин и пароль.", 400)
        username = username.strip().casefold()
        with self.sessions() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            conn.execute("DELETE FROM failures WHERE at<=?", (now - 300,))
            if conn.execute("SELECT count(*) FROM failures").fetchone()[0] >= 8:
                return feedback("Слишком много попыток. Повторите через пять минут.", 429)
            verifier = self.get_verifier(conn, username)
            with self.authority() as primary:
                allowed = self.authorized(primary, username)
            password_ok = matches(password, verifier)
            if not (password_ok and allowed):
                conn.execute("INSERT INTO failures VALUES (?)", (now,))
                return feedback("Проверьте логин и веб-пароль.", 401)
            token = self.new_session(conn, now, username)
        return self.redirect_with_session(token, is_pwa)

    @staticmethod
    def new_session(conn, now, username):
        token = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM failures")
        conn.execute("DELETE FROM sessions WHERE expires<=?", (now,))
        conn.execute("INSERT INTO sessions(digest,expires,username) VALUES (?,?,?)",
                     (sha256(token.encode()).hexdigest(), now + SESSION_SECONDS, username))
        return token

    @staticmethod
    def redirect_with_session(token, is_pwa=False):
        response = web.json_response({"ok":True}) if is_pwa else web.HTTPSeeOther(location=PREFIX)
        response.set_cookie(COOKIE, token, path=PREFIX, secure=True, httponly=True, samesite="Strict", max_age=SESSION_SECONDS)
        return response

    async def logout(self, request):
        self.require_origin(request)
        if session := self.session(request):
            with self.sessions() as conn:
                conn.execute("DELETE FROM sessions WHERE digest=?", (session[0],))
        response = (web.json_response({"ok":True}) if request.content_type == "application/json"
                    else web.HTTPSeeOther(location=link("login")))
        response.set_cookie(COOKIE, "", max_age=0, path=PREFIX, secure=True, httponly=True, samesite="Strict")
        return response

    @staticmethod
    def password_form(error="", status=200):
        alert = f'<p role="alert">{html(error)}</p>' if error else ""
        return page("Веб-пароль", alert + f"""
            <form class="panel" method="post" action="{link('password')}">
              <label>Текущий веб-пароль<input type="password" name="current" autocomplete="current-password"
                    maxlength="128" required></label>
              <label>Новый пароль, от 14 символов<input type="password" name="new" autocomplete="new-password"
                    minlength="14" maxlength="128" required></label>
              <button>Сохранить</button>
            </form>
            """, signed_in=True, status=status)

    async def change_password(self, request):
        data = None
        is_pwa = request.content_type == "application/json"
        def feedback(message, status):
            return web.json_response({"text":message}, status=status) if is_pwa else self.password_form(message, status)
        if request.method == "POST":
            self.require_origin(request)
            data = await (request.json() if is_pwa else request.post())
        with self.authority() as primary:
            digest, username = self.require_user(request, primary)
        if data is None:
            return self.password_form()
        current, new = data.get("current"), data.get("new")
        if not (isinstance(current, str) and len(current) <= 128 and isinstance(new, str) and 14 <= len(new) <= 128):
            return feedback("Введите текущий пароль и новый пароль длиной от 14 символов.", 400)
        with self.sessions() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            if not conn.execute("SELECT 1 FROM sessions WHERE digest=? AND expires>?", (digest, now)).fetchone():
                raise web.HTTPSeeOther(location=link("login"))
            conn.execute("DELETE FROM failures WHERE at<=?", (now - 300,))
            if conn.execute("SELECT count(*) FROM failures").fetchone()[0] >= 8:
                return feedback("Слишком много попыток. Повторите через пять минут.", 429)
            verifier = self.get_verifier(conn, username)
            if not matches(current, verifier):
                conn.execute("INSERT INTO failures VALUES (?)", (now,))
                return feedback("Текущий веб-пароль не подошёл.", 401)
            if self.can_write(username):
                conn.execute("UPDATE operator_credentials SET verifier=? WHERE username=?", (password_hash(new), username))
            else:
                conn.execute("UPDATE credential SET verifier=? WHERE id=1", (password_hash(new),))
            conn.execute("DELETE FROM sessions WHERE username=?", (username,))
            token = self.new_session(conn, now, username)
        return self.redirect_with_session(token, is_pwa)

    async def registry(self, request):
        phrase = request.query.get("q", "").strip()[:100]
        raw_page = request.query.get("page", "")
        number = int(raw_page) if raw_page.isascii() and raw_page.isdecimal() and len(raw_page) < 8 else 1
        number = max(number, 1)
        where = " WHERE " + " OR ".join(f"instr(fold({name}),?)>0" for name in SEARCH) if phrase else ""
        params = (phrase.casefold(),) * len(SEARCH) if phrase else ()
        with self.authority() as conn:
            self.require_user(request, conn)
            count = conn.execute("SELECT count(*) FROM clients" + where, params).fetchone()[0]
            pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
            number = min(number, pages)
            rows = conn.execute(
                "SELECT id," + ",".join(FIELDS) + " FROM clients" + where +
                " ORDER BY id LIMIT ? OFFSET ?", (*params, PAGE_SIZE, (number - 1) * PAGE_SIZE),
            ).fetchall()
        labels = "".join(f"<th scope=\"col\">{html(label)}</th>" for label in FIELDS.values())
        entries = []
        for row in rows:
            cells = "".join(f"<td>{html(row[key]) or '—'}</td>" for key in FIELDS if key != "contract_id")
            entries.append(f'<tr><td><a href="{PREFIX}contract/{row["id"]}">{html(row["contract_id"])}</a></td>{cells}</tr>')
        previous = f'<a href="{html(link(q=phrase, page=number-1))}">Назад</a>' if number > 1 else ""
        following = f'<a href="{html(link(q=phrase, page=number+1))}">Далее</a>' if number < pages else ""
        start = (number - 1) * PAGE_SIZE + 1 if count else 0
        end = min(number * PAGE_SIZE, count)
        table = f'<div class="table"><table><thead><tr>{labels}</tr></thead><tbody>{"".join(entries)}</tbody></table></div>'
        body = f'''
            <form class="search" method="get" action="{PREFIX}">
              <label>Найти договор<input name="q" value="{html(phrase)}" maxlength="100"
                     placeholder="Договор, клиент, кредитор или ответственный"></label>
              <button>Найти</button>
            </form>
            <div class="pager">{previous}<span>{start}–{end} из {count}</span>{following}</div>
            {table if entries else '<p>Договоры не найдены.</p>'}
        '''
        return page("Реестр", body, signed_in=True)

    async def contract(self, request):
        raw = request.match_info["id"]
        if len(raw) > 18:
            raise web.HTTPNotFound()
        with self.authority() as conn:
            _, username = self.require_user(request, conn)
            row = conn.execute("SELECT " + ",".join(DETAIL) + " FROM clients WHERE id=?", (int(raw),)).fetchone()
        if not row:
            raise web.HTTPNotFound(text="Договор не найден.")
        fields = "".join(f'<div><dt>{html(label)}</dt><dd>{html(row[key]) or "—"}</dd></div>' for key, label in DETAIL.items())
        edit = f'<p><a href="{link(f"contract/{raw}/edit")}">Редактировать</a></p>' if self.can_write(username) else ""
        return page(row["client"] or "Договор", f'<dl>{fields}</dl>{edit}', signed_in=True)

    async def edit(self, request):
        raw = request.match_info["id"]
        if not re.fullmatch(r"[0-9]{1,18}", raw):
            raise web.HTTPNotFound()
        row_id = int(raw)
        if request.method == "POST":
            self.require_origin(request)
            form = await request.post()
            with self.authority() as primary:
                _, username = self.require_user(request, primary)
                if not self.can_write(username):
                    raise web.HTTPForbidden(text="Доступна только работа на чтение.")
            with closing(sqlite3.connect(self.primary.as_uri() + "?mode=rw", uri=True, timeout=6)) as conn, conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                _, username = self.require_user(request, conn)
                if not self.can_write(username):
                    raise web.HTTPForbidden(text="Доступна только работа на чтение.")
                change(conn, self.catalog, row_id, form.get("revision"), form.get("operation"),
                       form.get("field"), form.get("value"), self.operator["uid"])
            raise web.HTTPSeeOther(location=link(f"contract/{raw}", saved=1))
        with self.authority() as primary:
            _, username = self.require_user(request, primary)
            if not self.can_write(username):
                raise web.HTTPForbidden(text="Доступна только работа на чтение.")
            row, revision = card(primary, self.catalog, row_id)
        options = "".join(f'<option value="{html(key)}">{html(title)}</option>' for key, title in self.catalog.labels.items())
        values = "".join(f'<tr><th>{html(title)}</th><td>{html(row[key]) or "—"}</td></tr>'
                         for key, title in self.catalog.labels.items())
        return page("Правка карточки", f'''
            <p><a href="{link(f"contract/{raw}")}">{html(row['contract_id'])}</a></p>
            <form class="panel" method="post" action="{link(f"contract/{raw}/edit")}">
              <input type="hidden" name="revision" value="{revision}">
              <input type="hidden" name="operation" value="{secrets.token_hex(16)}">
              <label>Поле<select name="field">{options}</select></label>
              <label>Новое значение<textarea name="value" maxlength="4096"></textarea></label>
              <p>Пустое значение очищает выбранное поле.</p>
              <button>Сохранить</button>
            </form>
            <div class="table"><table><tbody>{values}</tbody></table></div>
        ''', signed_in=True)

    async def summary(self, request):
        with self.authority() as conn:
            self.require_user(request, conn)
            rows = conn.execute("""
                SELECT coalesce(nullif(trim(partner),''),'—') AS partner, count(*) AS contracts,
                    sum(fold(trim(responsible_soft)) NOT IN ('','не закреплен','не закреплён','нет','не назначен')) AS soft,
                    sum(fold(trim(responsible)) NOT IN ('','не закреплен','не закреплён','нет','не назначен')) AS hard
                  FROM clients GROUP BY 1 ORDER BY 2 DESC,1
            """).fetchall()
        lines = "".join(f'<tr><td>{html(row["partner"])}</td><td>{row["contracts"]}</td>'
                        f'<td>{row["soft"]}</td><td>{row["hard"]}</td></tr>' for row in rows)
        body = ('<div class="table"><table><thead><tr><th>Кредитор</th><th>Договоры</th>'
                '<th>Назначено в Софт</th><th>Назначено в Хард</th></tr></thead>'
                f'<tbody>{lines}</tbody></table></div>')
        return page("Сводка", body, signed_in=True)

    async def health(self, request):
        with self.authority() as conn:
            allowed = self.authorized(conn, self.username)
            if self.operator:
                allowed = allowed and self.authorized(conn, self.operator["username"])
            conn.execute("SELECT " + ",".join(DETAIL) + " FROM clients LIMIT 1").fetchone()
        with self.sessions() as conn:
            ready = conn.execute("SELECT 1 FROM credential WHERE id=1").fetchone()
        return web.json_response({"ok": bool(ready and allowed)}, status=200 if ready and allowed else 503)


@web.middleware
async def web_headers(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = web.Response(status=exc.status, headers=exc.headers, body=exc.body)
    except RegistryConflict as exc:
        response = page("Правка не сохранена", f'<p role="alert">{html(exc)}</p>', signed_in=True, status=exc.status)
    except sqlite3.Error:
        response = page("Реестр недоступен", "<p>Повторите через несколько секунд.</p>", status=503)
    policy = "default-src 'none'; style-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    if request.get("analyst_pwa_asset"):
        policy = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                  "font-src 'self'; connect-src 'self'; worker-src 'self'; object-src 'none'; frame-ancestors 'none'; "
                  "base-uri 'self'; form-action 'self'")
    response.headers.update({
        "Cache-Control": "no-store",
        "Content-Security-Policy": policy,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "X-Robots-Tag": "noindex, nofollow",
    })
    return response


WEB_ASSETS = ("golos-cyrillic.woff2", "golos-latin.woff2", "golos-OFL.txt")


def create_app(site, stylesheet, asset_dir=None, pwa_assets=None):
    app = web.Application(client_max_size=20*1024*1024 if pwa_assets else 8192, middlewares=[web_headers])
    if pwa_assets:
        if __package__:
            from .analyst_pwa import mount_pwa
        else:
            from analyst_pwa import mount_pwa
        mount_pwa(app, site, pwa_assets, PREFIX, COOKIE)
    app.add_routes([
        web.get(link("classic") if pwa_assets else PREFIX, site.registry),
        web.get(link("login"), site.login), web.post(link("login"), site.login),
        web.post(link("logout"), site.logout), web.get(link("password"), site.change_password),
        web.post(link("password"), site.change_password), web.get(link("summary"), site.summary),
        web.get(PREFIX + r"contract/{id:\d+}", site.contract), web.get(link("health"), site.health),
        web.get(PREFIX + r"contract/{id:\d+}/edit", site.edit),
        web.post(PREFIX + r"contract/{id:\d+}/edit", site.edit),
    ])

    async def css(request):
        return web.Response(body=stylesheet, content_type="text/css")

    app.router.add_get(link("style.css"), css)
    if asset_dir:
        async def font(request):
            name = request.match_info["name"]
            if name not in WEB_ASSETS:
                raise web.HTTPNotFound()
            return web.FileResponse(asset_dir / name)

        app.router.add_get(link("assets/{name}"), font)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--operator", type=Path)
    parser.add_argument("--pwa-assets", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    operator = json.loads(args.operator.read_text(encoding="utf-8")) if args.operator else None
    site = AnalystSite(args.primary, args.state, args.origin, args.username, args.account_id, operator)
    site.initialize(args.seed.read_text(encoding="ascii").strip())
    css = Path(__file__).with_name("analyst_web.css").read_bytes()
    web.run_app(create_app(site, css, Path(__file__).parent, args.pwa_assets), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
