"""Аккаунты «логин-пароль» для сотрудников: вход без персональных ссылок.

Модель владельца (14.10.2026, согласовано с ИБ/ИТ):
- аккаунты заводит владелец на сервере (account_access.py, HTTP-ручек записи
  нет — как у admin_access.py); логин-пароль выдаётся человеку в офисе;
- сотрудник вводит логин/пароль НА ТЕЛЕФОНЕ (публичный вход, GPS обязателен)
  и НА КОМПЬЮТЕРЕ (внутренний вход): «ввели и там, и там» = человек в офисе;
- руководитель видит список «кто ввёл» и пересылает владельцу; доверять
  руководителю подтверждение нельзя — подтверждает ТОЛЬКО владелец;
- до подтверждения вход «работает, но без данных»: приложение показывает
  экран ожидания (тот же pending, что у заявок привязки). Доступ появляется
  командой владельца; повторный вход с нового устройства — тот же круг.
Токены выпускаются существующих классов (agent_devices/manager_devices),
помеченные account_id: телефон и ПК одним классом, различаются отпечатком.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

import db

ROLES = ("agent", "manager", "security", "full")
LOCK_AFTER_FAILS = 5
LOCK_MINUTES = 15
GPS_MAX_AGE_HOURS = 24      # запас на часовые пояса; сама точность не критична

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DK = 16384, 8, 1, 32


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE,
          employee TEXT NOT NULL,
          office TEXT,
          role TEXT NOT NULL DEFAULT 'agent',
          password_hash TEXT NOT NULL,
          must_change_password INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'awaiting',
          totp_secret TEXT,
          created_by TEXT, created_at TEXT, updated_at TEXT,
          activated_by TEXT, activated_at TEXT,
          failed_attempts INTEGER NOT NULL DEFAULT 0,
          locked_until TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_devices (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL,
          kind TEXT NOT NULL,             -- phone | desktop
          fingerprint TEXT NOT NULL,
          label TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',   -- pending | active | revoked
          entrance TEXT NOT NULL,         -- public | internal
          gps TEXT,                       -- телефон: {"lat","lon","accuracy"}
          first_seen TEXT NOT NULL, last_seen TEXT,
          approved_by TEXT, approved_at TEXT, revoked_at TEXT,
          agent_dev_id INTEGER            -- строка agent_devices с токеном
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_devices_acc "
                 "ON account_devices(account_id, kind, status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_logins (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER,
          username_attempted TEXT NOT NULL,
          kind TEXT NOT NULL,             -- ok|ok_pending|bad_password|locked|
                                          -- disabled|no_gps|unknown|passwd|approved
          entrance TEXT, device_kind TEXT, fingerprint TEXT, label TEXT,
          ip TEXT, gps TEXT,
          at TEXT NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_logins_acc "
                 "ON account_logins(account_id, at)")
    # Связка выпущенных токенов с аккаунтом (старые строки остаются NULL):
    for table in ("agent_devices", "manager_devices"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "account_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER")
        if table == "agent_devices" and "fingerprint" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN fingerprint TEXT")


# --- пароли ------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=_SCRYPT_DK, maxmem=64 * 1024 * 1024)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p),
                                dklen=len(bytes.fromhex(digest_hex)), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def _valid_password(password: str) -> bool:
    return 8 <= len(password) <= 128 and any(c.isdigit() for c in password) \
        and any(c.isalpha() for c in password)


def create_account(employee: str, username: str, password: str, *, office: str = "",
                   role: str = "agent", by: str = "cli") -> dict:
    """Завести аккаунт. Роль по умолчанию agent (сотрудник Хард/Софт);
    manager/security/full — когда в managers есть привязка по telegram_id
    (токенам этих ролей нужен uid). Пароль печатается вызывающим ОДИН раз."""
    employee = str(employee or "").strip()
    username = normalize_username(username)
    office = str(office or "").strip()
    if role not in ROLES:
        return {"ok": False, "error": f"role (одна из {', '.join(ROLES)})"}
    if not employee or len(employee) > 120:
        return {"ok": False, "error": "ФИО сотрудника обязательно."}
    if not USERNAME_RE.match(username):
        return {"ok": False, "error":
                "Логин: 3–32 символа, латиница/цифры/._- , начинается с буквы или цифры."}
    if not _valid_password(password or ""):
        return {"ok": False, "error": "Пароль от 8 символов, с буквой и цифрой."}
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE username=?", (username,)).fetchone():
            return {"ok": False, "error": "Такой логин уже есть."}
        conn.execute("INSERT INTO accounts(username,employee,office,role,password_hash,"
                     "status,created_by,created_at) VALUES(?,?,?,?,?,'awaiting',?,?)",
                     (username, employee, office, role, hash_password(password), by, _now()))
        conn.commit()
    db.audit_access(0, "account_create", employee, f"{username} role={role} by {by}")
    return {"ok": True, "username": username}


def set_password(username: str, new: str, *, by: str = "cli") -> dict:
    username = normalize_username(username)
    if not _valid_password(new or ""):
        return {"ok": False, "error": "Пароль от 8 символов, с буквой и цифрой."}
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            return {"ok": False, "error": "Нет такого аккаунта."}
        conn.execute("UPDATE accounts SET password_hash=?, must_change_password=1, "
                     "updated_at=? WHERE id=?", (hash_password(new), _now(), row["id"]))
        conn.commit()
    db.audit_access(0, "account_passwd", username, f"by {by}")
    return {"ok": True}


# --- журнал ------------------------------------------------------------------

def _journal(conn: sqlite3.Connection, account_id: int | None, username: str, kind: str,
             *, entrance: str = "", device_kind: str = "", fingerprint: str = "",
             label: str = "", ip: str = "", gps: str = "") -> None:
    conn.execute("INSERT INTO account_logins(account_id,username_attempted,kind,entrance,"
                 "device_kind,fingerprint,label,ip,gps,at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (account_id, username[:64], kind, entrance[:16], device_kind[:16],
                  fingerprint[:128], label[:120], (ip or "")[:64], gps[:120], _now()))


# --- вход --------------------------------------------------------------------

def _mint_agent_token(conn: sqlite3.Connection, account: sqlite3.Row,
                      device_row: sqlite3.Row) -> str:
    """Выпустить/ротировать токен агентского класса для устройства аккаунта."""
    raw = secrets.token_urlsafe(32)
    now = _now()
    if device_row["agent_dev_id"]:
        conn.execute("UPDATE agent_devices SET revoked_at=?, revoked_by=? WHERE id=? "
                     "AND revoked_at IS NULL",
                     (now, "account:rotate", device_row["agent_dev_id"]))
    dev_id = conn.execute(
        "INSERT INTO agent_devices(employee,telegram_id,token_hash,device,created_at,"
        "last_seen_at,account_id,fingerprint) VALUES(?,?,?,?,?,?,?,?)",
        (account["employee"], None, db.app_token_key(raw), device_row["label"][:80],
         now, now, account["id"], device_row["fingerprint"])).lastrowid
    conn.execute("UPDATE account_devices SET agent_dev_id=? WHERE id=?",
                 (dev_id, device_row["id"]))
    return raw


def login(username: str, password: str, *, device: dict, entrance: str,
          ip: str = "", gps: dict | None = None) -> dict:
    """Вход по логину-паролю. Возвращает {action, ...}; токен — только вместе
    с action ok/ok_pending (клиент хранит и показывает статус)."""
    username = normalize_username(username)
    device_kind = "phone" if str(device.get("kind")) == "phone" else "desktop"
    entrance = "internal" if entrance == "internal" else "public"
    fingerprint = str(device.get("fp") or "").strip()
    label = str(device.get("label") or "").strip() or (str(device.get("name") or "") or "устройство")
    gps_text = ""
    if isinstance(gps, dict):
        try:
            gps_text = json.dumps({"lat": round(float(gps["lat"]), 6),
                                   "lon": round(float(gps["lon"]), 6),
                                   "accuracy": round(float(gps.get("accuracy") or 0), 1)},
                                  ensure_ascii=False)
        except (KeyError, TypeError, ValueError):
            gps_text = ""

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username=?",
                           (username,)).fetchone()
        if row is None:
            _journal(conn, None, username, "unknown", entrance=entrance,
                     device_kind=device_kind, fingerprint=fingerprint, label=label, ip=ip)
            conn.commit()
            return {"action": "bad", "error": "Неверный логин или пароль."}
        # блокировка перебора по аккаунту
        locked_until = row["locked_until"] or ""
        if locked_until > _now():
            _journal(conn, row["id"], username, "locked", entrance=entrance,
                     device_kind=device_kind, fingerprint=fingerprint, label=label, ip=ip)
            conn.commit()
            return {"action": "locked",
                    "error": "Слишком много неудачных попыток. Повторите через 15 минут."}
        if not verify_password(password or "", row["password_hash"]):
            fails = int(row["failed_attempts"] or 0) + 1
            locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)
                      ).isoformat(timespec="seconds") if fails >= LOCK_AFTER_FAILS else None
            conn.execute("UPDATE accounts SET failed_attempts=?, locked_until=? WHERE id=?",
                         (fails, locked, row["id"]))
            _journal(conn, row["id"], username, "bad_password", entrance=entrance,
                     device_kind=device_kind, fingerprint=fingerprint, label=label, ip=ip)
            conn.commit()
            return {"action": "bad", "error": "Неверный логин или пароль."}
        if row["status"] == "disabled":
            _journal(conn, row["id"], username, "disabled", entrance=entrance,
                     device_kind=device_kind, fingerprint=fingerprint, label=label, ip=ip)
            conn.commit()
            return {"action": "blocked",
                    "error": "Доступ этому аккаунту закрыт. Обратитесь к руководителю."}
        if device_kind == "phone" and not gps_text:
            # Требование владельца: GPS на телефоне обязателен именно при входе.
            _journal(conn, row["id"], username, "no_gps", entrance=entrance,
                     device_kind=device_kind, fingerprint=fingerprint, label=label, ip=ip)
            conn.commit()
            return {"action": "no_gps", "error":
                    "Включите доступ к геолокации и повторите вход. Если разрешение "
                    "было отклонено — удалите и заново установите приложение."}
        if not fingerprint or len(fingerprint) > 128:
            return {"action": "bad", "error": "Приложение не сообщило отпечаток устройства."}

        conn.execute("UPDATE accounts SET failed_attempts=0, locked_until=NULL WHERE id=?",
                     (row["id"],))
        dev = conn.execute(
            "SELECT * FROM account_devices WHERE account_id=? AND fingerprint=? "
            "AND status<>'revoked' ORDER BY id DESC LIMIT 1",
            (row["id"], fingerprint)).fetchone()
        if dev is None:
            cur = conn.execute(
                "INSERT INTO account_devices(account_id,kind,fingerprint,label,status,"
                "entrance,gps,first_seen) VALUES(?,?,?,?,?,?,?,?)",
                (row["id"], device_kind, fingerprint, label[:120], "pending",
                 entrance, gps_text, _now()))
            dev = conn.execute("SELECT * FROM account_devices WHERE id=?",
                               (cur.lastrowid,)).fetchone()
        else:
            conn.execute("UPDATE account_devices SET last_seen=?, label=?, gps=CASE WHEN ?<>'' "
                         "THEN ? ELSE gps END WHERE id=?",
                         (_now(), label[:120], gps_text, gps_text, dev["id"]))

        token = _mint_agent_token(conn, row, dev)
        account_active = row["status"] == "active"
        device_active = dev["status"] == "active"
        action = "ok" if (account_active and device_active) else "ok_pending"
        _journal(conn, row["id"], username, action, entrance=entrance,
                 device_kind=device_kind, fingerprint=fingerprint, label=label,
                 ip=ip, gps=gps_text)
        conn.commit()
        return {"action": action, "token": token, "employee": row["employee"],
                "username": row["username"], "role": row["role"],
                "office": row["office"] or "",
                "must_change_password": bool(row["must_change_password"]),
                "error": "" if action == "ok" else
                "Вход записан. Доступ появится после подтверждения владельца."}


def device_gate(account_id: int, agent_dev_id: int) -> tuple[str, str]:
    """('ok'|'pending'|'blocked', ФИО) для middleware: решает, пропускать ли
    токен, выпущенный аккаунту. Вызывается на КАЖДЫЙ запрос — дёшево (индекс)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT a.status AS astatus, a.employee AS employee, d.status AS dstatus "
            "FROM accounts a JOIN account_devices d ON d.account_id=a.id "
            "WHERE a.id=? AND d.agent_dev_id=?", (account_id, agent_dev_id)).fetchone()
        if row is None:
            return "blocked", ""
        if row["astatus"] == "disabled":
            return "blocked", row["employee"]
        if row["astatus"] == "active" and row["dstatus"] == "active":
            return "ok", row["employee"]
        return "pending", row["employee"]


# --- смена пароля ------------------------------------------------------------

def change_password(username: str, current: str, new: str) -> dict:
    username = normalize_username(username)
    if not _valid_password(new or ""):
        return {"ok": False, "error":
                "Пароль от 8 символов, с буквой и цифрой."}
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None or not verify_password(current or "", row["password_hash"]):
            return {"ok": False, "error": "Текущий пароль неверен."}
        conn.execute("UPDATE accounts SET password_hash=?, must_change_password=0, "
                     "updated_at=? WHERE id=?",
                     (hash_password(new), _now(), row["id"]))
        _journal(conn, row["id"], username, "passwd")
        conn.commit()
    return {"ok": True}


# --- действия владельца --------------------------------------------------------

def approve_account(username: str, by: str) -> dict:
    """Команда владельца: открыть доступ аккаунту (устройства подтверждаются
    отдельно — обычно одной командой approve_device по каждому вводу)."""
    username = normalize_username(username)
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            return {"ok": False, "error": "Нет такого аккаунта."}
        conn.execute("UPDATE accounts SET status='active', activated_by=?, activated_at=?, "
                     "updated_at=? WHERE id=?", (by, _now(), _now(), row["id"]))
        _journal(conn, row["id"], username, "approved", label=f"by {by}")
        conn.commit()
        db.audit_access(0, "account_approve", row["employee"], f"{username} by {by}")
    return {"ok": True}


def approve_device(device_id: int, by: str) -> dict:
    """Подтвердить устройство (обычно — оба ввода из офиса). Новый ТЕЛЕФОН
    при подтверждении гасит прежний телефон аккаунта: сменить — значит сменить."""
    with db.connect() as conn:
        dev = conn.execute("SELECT * FROM account_devices WHERE id=?", (device_id,)).fetchone()
        if dev is None:
            return {"ok": False, "error": "Нет такого устройства."}
        conn.execute("UPDATE account_devices SET status='active', approved_by=?, "
                     "approved_at=? WHERE id=?", (by, _now(), device_id))
        if dev["kind"] == "phone":
            old = conn.execute(
                "SELECT id, agent_dev_id FROM account_devices WHERE account_id=? AND kind='phone' "
                "AND status='active' AND id<>?", (dev["account_id"], device_id)).fetchall()
            for row in old:
                conn.execute("UPDATE account_devices SET status='revoked', revoked_at=? "
                             "WHERE id=?", (_now(), row["id"]))
                if row["agent_dev_id"]:
                    conn.execute("UPDATE agent_devices SET revoked_at=?, revoked_by=? "
                                 "WHERE id=? AND revoked_at IS NULL",
                                 (_now(), f"account:new-phone:{by}", row["agent_dev_id"]))
        conn.commit()
        acc = conn.execute("SELECT username FROM accounts WHERE id=?",
                           (dev["account_id"],)).fetchone()
        if acc:
            _journal(conn, dev["account_id"], acc["username"], "approved",
                     device_kind=dev["kind"], label=dev["label"], gps=dev["gps"] or "",
                     ip=f"by {by}")
            conn.commit()
        db.audit_access(0, "account_device_approve", str(dev["label"]),
                        f"#{device_id} {dev['kind']} by {by}")
    return {"ok": True}


def set_status(username: str, status: str, by: str) -> dict:
    username = normalize_username(username)
    if status not in ("active", "awaiting", "disabled"):
        return {"ok": False, "error": "status"}
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            return {"ok": False, "error": "Нет такого аккаунта."}
        conn.execute("UPDATE accounts SET status=?, updated_at=? WHERE id=?",
                     (status, _now(), row["id"]))
        if status == "disabled":   # сразу погасить все токены аккаунта
            for dev in conn.execute("SELECT agent_dev_id FROM account_devices "
                                    "WHERE account_id=? AND status='active'",
                                    (row["id"],)).fetchall():
                if dev["agent_dev_id"]:
                    conn.execute("UPDATE agent_devices SET revoked_at=?, revoked_by=? "
                                 "WHERE id=? AND revoked_at IS NULL",
                                 (_now(), f"account:disable:{by}", dev["agent_dev_id"]))
            conn.execute("UPDATE account_devices SET status='revoked', revoked_at=? "
                         "WHERE account_id=? AND status='active'", (_now(), row["id"]))
        conn.commit()
    return {"ok": True}


# --- списки -------------------------------------------------------------------

def entered_list(office: str = "") -> list[dict]:
    """«Кто ввёл»: строка на аккаунт с телефонным и компьютерным вводом.
    Руководитель пересылает список владельцу; ready = введено и там, и там."""
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY office, employee").fetchall()
        out: list[dict] = []
        for acc in rows:
            if office and (acc["office"] or "") != office:
                continue
            devices = conn.execute(
                "SELECT * FROM account_devices WHERE account_id=? AND status<>'revoked' "
                "ORDER BY id DESC", (acc["id"],)).fetchall()
            phone = next((d for d in devices if d["kind"] == "phone"), None)
            desktop = next((d for d in devices if d["kind"] == "desktop"), None)
            if phone is None and desktop is None:
                continue
            out.append({
                "employee": acc["employee"], "username": acc["username"],
                "office": acc["office"] or "", "status": acc["status"],
                "phone": {"label": phone["label"], "at": phone["first_seen"],
                          "gps": phone["gps"] or "", "confirmed": phone["status"] == "active"}
                if phone else None,
                "desktop": {"label": desktop["label"], "at": desktop["first_seen"],
                            "confirmed": desktop["status"] == "active"} if desktop else None,
                "ready": phone is not None and desktop is not None,
            })
        return out


def admin_accounts() -> list[dict]:
    """Полный список для владельца/аналитика: аккаунты, устройства, журнал."""
    with db.connect() as conn:
        out = []
        for acc in conn.execute("SELECT * FROM accounts ORDER BY office, employee").fetchall():
            devices = conn.execute(
                "SELECT id,kind,label,status,entrance,gps,first_seen,approved_by,approved_at "
                "FROM account_devices WHERE account_id=? ORDER BY id DESC",
                (acc["id"],)).fetchall()
            logins = conn.execute(
                "SELECT kind,at,entrance,device_kind,label,ip FROM account_logins "
                "WHERE account_id=? ORDER BY id DESC LIMIT 10",
                (acc["id"],)).fetchall()
            out.append({"id": acc["id"], "username": acc["username"],
                        "employee": acc["employee"], "office": acc["office"] or "",
                        "role": acc["role"], "status": acc["status"],
                        "must_change_password": bool(acc["must_change_password"]),
                        "created_at": acc["created_at"], "activated_at": acc["activated_at"],
                        "devices": [dict(d) for d in devices],
                        "logins": [dict(l) for l in logins]})
        return out


def sessions_for(agent_dev_id: int, account_id: int, limit: int = 30) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT kind,at,entrance,device_kind,label,ip,gps FROM account_logins "
            "WHERE account_id=? ORDER BY id DESC LIMIT ?", (account_id, limit)).fetchall()
    return [dict(r) for r in rows]


def account_of_token(agent_dev_id: int) -> tuple[int, str] | None:
    """(account_id, username) по строке agent_devices — для своих ручек
    (сессии/выход) прямо токеном."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT a.id AS aid, a.username AS username FROM accounts a "
            "JOIN account_devices d ON d.account_id=a.id WHERE d.agent_dev_id=?",
            (agent_dev_id,)).fetchone()
    return (int(row["aid"]), row["username"]) if row else None
