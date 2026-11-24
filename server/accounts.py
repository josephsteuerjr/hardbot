"""Личные профили: создание сотрудником на ПК и подтверждение своим руководителем.
Одновременно один логический слот ПК и телефона. Ключ установки связывает
заявку с ПК; физическое присутствие проверяет руководитель, а не IP или GPS.
Телефон входит с тем же паролем после отдельного одобрения своего
руководителя. Геопозиция для входа и доступа к данным не требуется. Привилегии подтверждаются независимо; первичный профиль владельца
может быть создан из реальной root-сессии по отдельному поручению владельца.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
from datetime import timezone
from contextlib import nullcontext
import unicodedata
from datetime import datetime, timedelta

import db
import client_versions

ROLES = ("agent", "manager", "security", "full", "analyst")


class UIDConflict(ValueError):
    """A person's old UID cannot be inferred or revoked without an owner decision."""
# Привилегированные роли: активация двумя администраторами, токен класса
# manager_devices (у аналитика/руководителя свои продукты и ручки).
PRIVILEGED = ("manager", "security", "full", "analyst")
LOCK_AFTER_FAILS = 5
LOCK_MINUTES = 15

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DK = 16384, 8, 1, 32

# Фиктивный хэш для неизвестных логинов: scrypt и при unknown, и при неверном
# пароле — иначе логины перечислялись таймингом (ревью 15.10: 1 мс против 42).
_DUMMY_HASH = ""


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(12))
    return _DUMMY_HASH


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fresh_gps(device):
    return bool(device['gps'] and device['gps_seen_at'] and device['gps_seen_at']>
                (datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat())


def _approved_staff_pc(conn, account_id):
    return bool(conn.execute("SELECT 1 FROM account_devices d WHERE d.account_id=? AND d.kind='desktop' "
        "AND d.status='active' AND COALESCE(d.public_key,'')<>'' AND EXISTS(SELECT 1 FROM account_approval_events e "
        "WHERE e.device_id=d.id AND e.action IN ('pc_observation','device_policy') AND e.decision='approved') LIMIT 1",(account_id,)).fetchone())


def _approved_mobile_identity(conn, device_id):
    return bool(conn.execute("SELECT 1 FROM account_approval_events WHERE device_id=? "
        "AND action IN ('phone_identity','device_policy') AND decision='approved' LIMIT 1", (device_id,)).fetchone())


def _approvals_needed(role: str) -> int:
    # Owner's policy of 18.10.2026: one real administrator is sufficient.
    return 1


def init_schema(conn: sqlite3.Connection) -> None:
    admin_cols = {r[1] for r in conn.execute("PRAGMA table_info(admin_devices)")}
    if "account_approver" not in admin_cols:
        conn.execute("ALTER TABLE admin_devices ADD COLUMN account_approver TEXT")
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
          telegram_id INTEGER,
          approvals TEXT NOT NULL DEFAULT '[]',
          created_by TEXT, created_at TEXT, updated_at TEXT,
          activated_by TEXT, activated_at TEXT,
          failed_attempts INTEGER NOT NULL DEFAULT 0,
          locked_until TEXT
        )""")
    acc_cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
    for col, ddl in (("telegram_id", "INTEGER"), ("approvals", "TEXT NOT NULL DEFAULT '[]'"),
                     ("registration_gps", "TEXT"), ("registration_ip", "TEXT"),
                     ("password_review_pending", "INTEGER NOT NULL DEFAULT 0"),
                     ("password_reset_operation", "TEXT"), ("password_reset_actor", "INTEGER")):
        if col not in acc_cols:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {ddl}")
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
          approvals TEXT NOT NULL DEFAULT '[]',
          agent_dev_id INTEGER,           -- строка в token_table
          token_table TEXT NOT NULL DEFAULT 'agent_devices'
        )""")
    dev_cols = {r[1] for r in conn.execute("PRAGMA table_info(account_devices)")}
    for col, ddl in (("approvals", "TEXT NOT NULL DEFAULT '[]'"),
                     ("token_table", "TEXT NOT NULL DEFAULT 'agent_devices'"),
                     ("gps_exception_until", "TEXT"), ("gps_seen_at", "TEXT"),
                     ("public_key", "TEXT")):
        if col not in dev_cols:
            conn.execute(f"ALTER TABLE account_devices ADD COLUMN {col} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_devices_acc "
                 "ON account_devices(account_id, kind, status)")
    conn.execute("CREATE TABLE IF NOT EXISTS account_token_sessions ("
                 "token_table TEXT NOT NULL, token_id INTEGER NOT NULL, device_id INTEGER NOT NULL, "
                 "PRIMARY KEY(token_table,token_id))")
    conn.execute("INSERT OR IGNORE INTO account_token_sessions(token_table,token_id,device_id) "
                 "SELECT token_table,agent_dev_id,id FROM account_devices WHERE agent_dev_id IS NOT NULL")
    client_versions.init_schema(conn)
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
    conn.execute("""CREATE TABLE IF NOT EXISTS account_approval_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, device_id INTEGER,
        actor_uid INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        decision TEXT NOT NULL, ip TEXT, detail TEXT, at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_approval_events_acc "
                 "ON account_approval_events(account_id, id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS account_reset_device_grants (
        account_id INTEGER NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
        issued_at TEXT NOT NULL, consumed_at TEXT, device_id INTEGER,
        PRIMARY KEY(account_id, kind))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS account_phone_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
        device_id INTEGER NOT NULL, code_hash TEXT NOT NULL UNIQUE,
        manager_uid INTEGER NOT NULL, manager_token_id INTEGER NOT NULL,
        issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT,
        operator_principal TEXT, attestation TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_phone_requests_device "
                 "ON account_phone_requests(device_id, consumed_at)")
    # Historical UIDs must never regain legacy bearer/Telegram access after deletion.
    conn.execute("CREATE TABLE IF NOT EXISTS account_deletions ("
                 "account_id INTEGER PRIMARY KEY, telegram_id INTEGER, deleted_at TEXT NOT NULL, "
                 "deleted_by TEXT NOT NULL)")
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


def _synthetic_uid(conn: sqlite3.Connection) -> int:
    """Служебный внутренний id из диапазона 90 000 000 000+ (реальные
    telegram- id в эти числа не попадают). Проверяем уникальность против
    managers, agent_bind и уже заведённых аккаунтов."""
    while True:
        candidate = 90_000_000_000 + secrets.randbelow(1_000_000)
        busy = (conn.execute("SELECT 1 FROM managers WHERE telegram_id=?",
                             (candidate,)).fetchone()
                or conn.execute("SELECT 1 FROM agent_bind WHERE telegram_id=?",
                                (candidate,)).fetchone()
                or conn.execute("SELECT 1 FROM accounts WHERE telegram_id=?",
                                (candidate,)).fetchone()
                or conn.execute("SELECT 1 FROM account_deletions WHERE telegram_id=?",
                                (candidate,)).fetchone())
        if not busy:
            return candidate


def _resolve_agent_uid(conn: sqlite3.Connection, employee: str) -> int:
    """Внутренний id для СОТРУДНИКА (роль agent): наследуем id телефонной
    привязки того же человека (единая история визитов/звонков на переходный
    период ссылка+аккаунт), при отсутствии — служебный синтетический.
    Ревью 15.10: без uid аккаунт-токен не проходил _uid (401 на все ручки)."""
    matches = {int(m["telegram_id"]) for m in conn.execute(
        "SELECT b.employee,b.telegram_id FROM agent_bind b WHERE b.approved=1 "
        "AND NOT EXISTS (SELECT 1 FROM account_deletions x WHERE x.telegram_id=b.telegram_id)")
        if db._name_key(m["employee"]) == db._name_key(employee)}
    if len(matches) > 1:
        raise UIDConflict("ФИО соответствует нескольким внутренним ID; нужна ручная сверка.")
    return next(iter(matches)) if matches else _synthetic_uid(conn)


def create_account(employee: str, username: str, password: str, *, office: str = "",
                   role: str = "agent", by: str = "cli",
                   telegram_id: int | None = None, self_service: bool = False,
                   registration_gps: dict | None = None, registration_ip: str = "",
                   registration_pc: dict | None = None,
                   registration_device: dict | None = None, _connection=None) -> dict:
    """Завести аккаунт. Роль по умолчанию agent (сотрудник Хард/Софт).
    Руководителям/аналитику нужен внутренний номер пользователя (легаси-поле
    зовётся telegram_id, но Телеграм не используется): берётся из managers
    по ФИО, при отсутствии — создаётся запись со служебным id 90 000 000 000+.
    У СОТРУДНИКА id наследуется от его телефонной привязки (или служебный) —
    токен аккаунта должен проходить серверный _uid как обычный вход.
    Пароль печатается вызывающим ОДИН раз."""
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
    password_hash = hash_password(password)
    with (db.connect() if _connection is None else nullcontext(_connection)) as conn:
        if _connection is None: conn.execute("BEGIN IMMEDIATE")
        pc = None
        phone = None
        if self_service:
            import pc_registration
            import staff_roster
            if registration_pc is not None:
                pc = pc_registration.consume(conn, registration_pc, employee, username, password)
                if not pc:
                    return {"ok": False, "error": "Заявка компьютера недействительна или истекла. Повторите регистрацию в программе."}
            else:
                device = registration_device if isinstance(registration_device, dict) else {}
                fp = str(device.get('fp') or '').strip()
                if device.get('kind') != 'phone' or not 8 <= len(fp) <= 256:
                    return {"ok": False, "error": "Для регистрации с телефона нужна заявка телефона. На ПК используйте программу."}
                phone = {'fingerprint': fp, 'label': str(device.get('label') or 'Телефон')[:120]}
            roster = staff_roster.candidates(conn)
            names = {r['employee'] for r in roster}
            matches = [name for name in names if db._name_key(name) == db._name_key(employee)]
            if len(matches) != 1:
                return {"ok": False, "error": "ФИО не найдено однозначно в рабочем реестре. Уточните написание у руководителя."}
            employee = matches[0]
            if db._name_key(employee) in _privileged_names(conn):
                return {"ok": False, "error": "Для этой роли доступ выдаёт администратор."}
            role, telegram_id, by = "agent", None, "self-registration"
            office = next((r['office'] for r in roster if r['employee'] == employee), '')
            if not office:
                office = next((r[1] for r in conn.execute("SELECT employee,city FROM employee_city")
                               if db._name_key(r[0]) == db._name_key(employee)), "")
        if conn.execute("SELECT 1 FROM accounts WHERE username=?", (username,)).fetchone():
            return {"ok": False, "error": "Такой логин уже есть."}
        if any(db._name_key(r["employee"]) == db._name_key(employee)
               for r in conn.execute("SELECT employee FROM accounts")):
            return {"ok": False, "error": "Аккаунт этого сотрудника уже существует. Восстановите его доступ."}
        resolved_uid = int(telegram_id) if telegram_id is not None else None
        if role in PRIVILEGED and resolved_uid is None:
            matches = [m for m in conn.execute("SELECT name,telegram_id,role FROM managers")
                       if db._name_key(m["name"]) == db._name_key(employee)]
            if len({int(m["telegram_id"]) for m in matches}) > 1:
                return {"ok": False, "error": "ФИО соответствует нескольким руководителям; нужна ручная сверка."}
            if matches:
                resolved_uid = int(matches[0]["telegram_id"])
            else:
                resolved_uid = _synthetic_uid(conn)
                conn.execute("INSERT INTO managers(telegram_id,name,cities,employees,"
                             "direction,role,created_at) VALUES(?,?,?,?,?,?,?)",
                             (resolved_uid, employee, "", "", "", role, _now()))
        if resolved_uid is None:
            try:
                resolved_uid = _resolve_agent_uid(conn, employee)
            except UIDConflict as exc:
                return {"ok": False, "error": str(exc)}
        if role == 'agent' and conn.execute("SELECT 1 FROM account_deletions WHERE telegram_id=?", (resolved_uid,)).fetchone():
            return {"ok": False, "error": "Внутренний ID удалённого аккаунта повторно использовать нельзя. Создайте новый аккаунт без прежнего ID."}
        from config import ADMIN_ID
        if ADMIN_ID and resolved_uid == ADMIN_ID and role != "full":
            return {"ok": False, "error": "UID владельца требует полную роль и ручную сверку."}
        existing_manager = conn.execute("SELECT name,role FROM managers WHERE telegram_id=?",
                                        (resolved_uid,)).fetchone()
        if existing_manager and (str(existing_manager["role"] or "manager") != role
                                 or db._name_key(existing_manager["name"]) != db._name_key(employee)):
            return {"ok": False, "error": "Внутренний ID имеет другую роль или имя; нужна ручная сверка."}
        if conn.execute("SELECT 1 FROM accounts WHERE telegram_id=?", (resolved_uid,)).fetchone():
            return {"ok": False, "error": "Внутренний ID уже связан с аккаунтом; нужна ручная сверка."}
        if self_service and existing_manager:
            return {"ok": False, "error": "Для этой роли доступ выдаёт администратор."}
        gps_text = _gps_text(registration_gps)
        created = conn.execute("INSERT INTO accounts(username,employee,office,role,password_hash,"
                     "status,telegram_id,created_by,created_at,must_change_password,"
                     "registration_gps,registration_ip) "
                     "VALUES(?,?,?,?,?,'awaiting',?,?,?,?,?,?)",
                     (username, employee, office, role, password_hash,
                      resolved_uid, by, _now(), 0 if self_service else 1,
                      gps_text, registration_ip[:64]))
        pc_token = None
        if pc:
            account_id = created.lastrowid
            dev_id = conn.execute("INSERT INTO account_devices(account_id,kind,fingerprint,label,status,entrance,first_seen,public_key) "
                                  "VALUES(?,'desktop',?,?,'pending','internal',?,?)",
                                  (account_id, pc['fingerprint'], pc['label'], _now(), pc['public_key'])).lastrowid
            pc_token = _mint_token(conn, conn.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone(),
                                   conn.execute('SELECT * FROM account_devices WHERE id=?',(dev_id,)).fetchone())
        elif phone:
            account_id = created.lastrowid
            dev_id = conn.execute("INSERT INTO account_devices(account_id,kind,fingerprint,label,status,entrance,first_seen) "
                "VALUES(?,'phone',?,?,'pending','public',?)",
                (account_id, phone['fingerprint'], phone['label'], _now())).lastrowid
            pc_token = _mint_token(conn, conn.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone(),
                conn.execute('SELECT * FROM account_devices WHERE id=?',(dev_id,)).fetchone())
        if _connection is None: conn.commit()
    if _connection is None: db.audit_access(0, "account_create", employee, f"{username} role={role} by {by}")
    result = {"ok": True, "username": username, "employee": employee, "role": role,
              "uid": resolved_uid, "gps_recorded": bool(gps_text), "gps_exception_pending": False}
    if pc_token:
        result.update(token=pc_token, action='ok_pending')
    return result


def create_manager_account(uid: int, username: str, password: str, *, display_name: str = "", by: str) -> dict:
    """Bind an existing, verified manager without changing any of their scope.

    An explicit canonical display name prevents a second employee account under
    the same person's roster name. Rename and account creation succeed together.
    """
    name = str(display_name or "").strip()
    if len(name) > 120:
        return {"ok": False, "error": "ФИО: не более 120 символов."}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        manager = conn.execute("SELECT * FROM managers WHERE telegram_id=?", (uid,)).fetchone()
        if not manager:
            return {"ok": False, "error": "Руководитель не найден."}
        if str(manager['role'] or 'manager') not in ('manager', 'full'):
            return {"ok": False, "error": "Выберите профиль руководителя."}
        if conn.execute("SELECT 1 FROM accounts WHERE telegram_id=?", (uid,)).fetchone():
            return {"ok": False, "error": "Профиль этого руководителя уже существует."}
        if name:
            conn.execute("UPDATE managers SET name=? WHERE telegram_id=?", (name, uid))
        result = create_account(name or manager['name'], username, password,
            role=str(manager['role'] or 'manager'), telegram_id=uid, by=by, _connection=conn)
        if not result.get('ok'):
            conn.rollback()
            return result
        conn.commit()
    db.reset_role_caches()
    db.audit_access(0, "account_create", result['employee'], f"{result['username']} manager uid={uid} by {by}")
    return result


def _gps_text(gps: dict | None) -> str:
    if not isinstance(gps, dict):
        return ""
    try:
        lat, lon = float(gps["lat"]), float(gps["lon"])
        accuracy = float(gps.get("accuracy") or 0)
        if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(accuracy)
                and -90 <= lat <= 90 and -180 <= lon <= 180 and accuracy >= 0):
            return ""
    except (TypeError, ValueError, KeyError):
        return ""
    return json.dumps({"lat": round(lat, 6), "lon": round(lon, 6),
                       "accuracy": round(accuracy, 1)}, ensure_ascii=False)


def set_password(username: str, new: str, *, by: str = "cli") -> dict:
    username = normalize_username(username)
    if not _valid_password(new or ""):
        return {"ok": False, "error": "Пароль от 8 символов, с буквой и цифрой."}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            return {"ok": False, "error": "Нет такого аккаунта."}
        try:
            _assert_uid_owned(conn, row)
        except UIDConflict as exc:
            return {"ok": False, "error": str(exc)}
        conn.execute("UPDATE accounts SET password_hash=?, must_change_password=1, "
                     "failed_attempts=0, locked_until=NULL, "
                     "updated_at=? WHERE id=?", (hash_password(new), _now(), row["id"]))
        _revoke_account_tokens(conn, row["id"], f"account:password-reset:{by}")
        conn.execute("DELETE FROM account_reset_device_grants WHERE account_id=?", (row["id"],))
        # A verified owner reset is itself the access decision for a leader.
        # Each device kind may enter once; later replacements need approval.
        if (row['role'] in ('manager', 'security', 'full') and row['status'] != 'disabled'
                and (by == 'cli' or _owner_approver(conn, by))):
            conn.execute("UPDATE accounts SET status='active', approvals=?, activated_by=?, "
                         "activated_at=?, password_review_pending=0 WHERE id=?",
                         (json.dumps([by]), by, _now(), row['id']))
            for kind in ('desktop', 'phone'):
                conn.execute("INSERT INTO account_reset_device_grants VALUES(?,?,?,?,NULL,NULL) "
                             "ON CONFLICT(account_id,kind) DO UPDATE SET actor=excluded.actor, "
                             "issued_at=excluded.issued_at,consumed_at=NULL,device_id=NULL",
                             (row['id'], kind, by, _now()))
            _approval_event(conn, row['id'], None, 0, by, 'owner_password_reset', 'approved',
                            detail='next desktop and phone allowed; personal password required')
        conn.commit()
    db.audit_access(0, "account_passwd", username, f"by {by}")
    return {"ok": True}


def manager_reset_password(uid: int, username: str, new: str, operation_id: str, *, is_owner=False, ip="") -> dict:
    """A manager resets and restores only their current staff's access.

    The caller supplies the temporary password so a lost response never loses
    the credential. An operation ID makes an explicit retry non-destructive.
    """
    username = normalize_username(username)
    if not _valid_password(new or ""):
        return {"ok": False, "error": "Пароль от 8 символов, с буквой и цифрой."}
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,100}", operation_id or ""):
        return {"ok": False, "error": "operation_id"}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        manager, team = _manager_review_context(uid, is_owner)
        acc = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not _reviewable(conn, acc, uid, manager, team):
            return {"ok": False, "error": "account"}
        if acc['password_reset_operation'] == operation_id:
            if acc['password_reset_actor'] != uid:
                return {"ok": False, "error": "operation_id"}
            return {"ok": True, "replayed": True, "password_review_pending": bool(acc['password_review_pending'])}
        if acc['password_review_pending']:
            return {"ok": False, "error": "Смена уже ожидает подтверждения руководителя. Обновите список."}
        try:
            _assert_uid_owned(conn, acc)
        except UIDConflict as exc:
            return {"ok": False, "error": str(exc)}
        actor = f"manager:{uid}:{manager.get('name') or ''}"
        conn.execute("UPDATE accounts SET password_hash=?, must_change_password=1, status='awaiting',"
                     "approvals='[]',activated_by=NULL,activated_at=NULL,failed_attempts=0,locked_until=NULL,"
                     "password_review_pending=1,password_reset_operation=?,password_reset_actor=?,updated_at=? WHERE id=?",
                     (hash_password(new),operation_id,uid,_now(),acc['id']))
        _revoke_account_tokens(conn, acc['id'], f"account:password-reset:{actor}")
        _approval_event(conn, acc['id'], None, uid, actor, 'password_reset', 'pending', ip,
                        f"operation={operation_id}; current team leader approval required")
        conn.commit()
    return {"ok": True, "password_review_pending": True}


def _owner_approver(conn, by: str) -> bool:
    return bool(by.startswith('principal:') and conn.execute(
        "SELECT 1 FROM account_admin_grants g JOIN accounts a ON a.id=g.account_id "
        "WHERE g.granted_by='ssh-root' AND g.principal=? AND a.status='active'",
        (by[len('principal:'):],)).fetchone())


# --- журнал ------------------------------------------------------------------

def _journal(conn: sqlite3.Connection, account_id: int | None, username: str, kind: str,
             *, entrance: str = "", device_kind: str = "", fingerprint: str = "",
             label: str = "", ip: str = "", gps: str = "") -> None:
    conn.execute("INSERT INTO account_logins(account_id,username_attempted,kind,entrance,"
                 "device_kind,fingerprint,label,ip,gps,at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (account_id, username[:64], kind, entrance[:16], device_kind[:16],
                  fingerprint[:128], label[:120], (ip or "")[:64], gps[:120], _now()))


# --- вход --------------------------------------------------------------------

def _mint_token(conn: sqlite3.Connection, account: sqlite3.Row,
                device_row: sqlite3.Row) -> str:
    """Выпустить/ротировать токен нужного классА по роли: сотрудник —
    agent_devices (телефон и ПК одним классом), руководитель/аналитик —
    manager_devices (там обязателен telegram_id из managers)."""
    raw = secrets.token_urlsafe(32)
    now = _now()
    token_table = "manager_devices" if account["role"] in PRIVILEGED else "agent_devices"
    key = db.app_token_key(raw)
    if token_table == "manager_devices":
        dev_id = conn.execute(
            "INSERT INTO manager_devices(telegram_id,token_hash,device,created_at,"
            "last_seen_at,account_id) VALUES(?,?,?,?,?,?)",
            (account["telegram_id"], key, device_row["label"][:80], now, now,
             account["id"])).lastrowid
    else:
        dev_id = conn.execute(
            "INSERT INTO agent_devices(employee,telegram_id,token_hash,device,created_at,"
            "last_seen_at,account_id,fingerprint) VALUES(?,?,?,?,?,?,?,?)",
            (account["employee"], account["telegram_id"], key, device_row["label"][:80], now, now,
             account["id"], device_row["fingerprint"])).lastrowid
    conn.execute("UPDATE account_devices SET agent_dev_id=?, token_table=? WHERE id=?",
                 (dev_id, token_table, device_row["id"]))
    conn.execute('INSERT INTO account_token_sessions VALUES(?,?,?)',(token_table,dev_id,device_row['id']))
    return raw


def _revoke_slot_tokens(conn, device_id, by):
    for table in ('agent_devices','manager_devices'):
        conn.execute(f'UPDATE {table} SET revoked_at=?,revoked_by=? WHERE revoked_at IS NULL '
                     'AND id IN (SELECT token_id FROM account_token_sessions WHERE device_id=? AND token_table=?)',
                     (_now(),by,device_id,table))


def refresh_gps(token_table, token, gps):
    """Fresh coordinates belong to this authenticated phone session, never its PC."""
    if token_table not in ('agent_devices','manager_devices') or not token: return
    point=_gps_text(gps)
    if not point: return
    with db.connect() as c:
        c.execute('UPDATE account_devices SET gps=?,gps_seen_at=? WHERE kind=\'phone\' AND status<>\'revoked\' '
                  'AND id IN (SELECT s.device_id FROM account_token_sessions s JOIN '+token_table+
                  ' t ON t.id=s.token_id WHERE s.token_table=? AND t.token_hash=? AND t.revoked_at IS NULL)',
                  (point,datetime.now(timezone.utc).isoformat(),token_table,db.app_token_key(token)))
        c.commit()


def login(username: str, password: str, *, device: dict, entrance: str,
          ip: str = "", gps: dict | None = None, expected_uid: int | None = None,
          audience: str = "", pc_proof: dict | None = None) -> dict:
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
            lat, lon = float(gps["lat"]), float(gps["lon"])
            accuracy = float(gps.get("accuracy") or 0)
            if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(accuracy)
                    and -90 <= lat <= 90 and -180 <= lon <= 180 and accuracy >= 0):
                raise ValueError("gps")
            gps_text = json.dumps({"lat": round(lat, 6), "lon": round(lon, 6),
                                   "accuracy": round(accuracy, 1)},
                                  ensure_ascii=False)
        except (KeyError, TypeError, ValueError):
            gps_text = ""

    with db.connect() as conn:
        # Serialize failure counters, device creation and token rotation together.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM accounts WHERE username=?",
                           (username,)).fetchone()
        if row is None:
            verify_password(password or "", _dummy_hash())   # выровнять тайминг
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
        import account_policy
        if ((audience == "employee" and row["role"] != "agent")
                or (audience == "manager" and row["role"] not in ("manager", "security", "full"))
                or (audience == 'analyst' and (device_kind!='desktop' or not conn.execute('SELECT 1 FROM account_admin_grants WHERE account_id=?',(row['id'],)).fetchone()))
                or (audience == 'mobile' and (device_kind != 'phone' or row['role'] not in ('agent','manager','security','full')))
                or (audience == 'crm-mobile' and (os.getenv('HARD_CRM_MOBILE')!='1' or device_kind!='phone' or row['role'] not in ('agent','manager','security','full','analyst') or row['role']=='analyst' and not conn.execute('SELECT 1 FROM account_admin_grants WHERE account_id=?',(row['id'],)).fetchone()))
                or (audience == 'crm-desktop' and (os.getenv('HARD_CRM_DESKTOP')!='1' or device_kind!='desktop' or row['role'] not in ('agent','manager','security','full','analyst') or row['role']=='analyst' and not conn.execute('SELECT 1 FROM account_admin_grants WHERE account_id=?',(row['id'],)).fetchone()))
                or (account_policy.strict(connection=conn) and audience not in ("employee", "manager", "mobile", "analyst", "crm-mobile", "crm-desktop"))):
            return {"action": "blocked", "error": "Неверный класс входа для этой роли."}
        if not fingerprint or len(fingerprint) > 128:
            _journal(conn, row["id"], username, "no_fingerprint", entrance=entrance,
                     device_kind=device_kind, label=label, ip=ip)
            conn.commit()
            return {"action": "bad", "error": "Приложение не сообщило отпечаток устройства."}
        pc = None
        if device_kind=='desktop' and (account_policy.strict(connection=conn) or row['created_by']=='self-registration' or row['role']=='analyst'):
            import pc_registration
            pc=pc_registration.consume(conn,pc_proof,'',username,password)
            if not pc or pc['fingerprint']!=fingerprint:
                return {'action':'blocked','error':'Войдите через новую программу на компьютере. Проверка установки не пройдена.'}

        # У аккаунтов, заведённых до 15.10, у сотрудника мог не resolving'ся
        # внутренний id — без него токен не проходит серверный _uid (P1 ревью).
        if row["role"] not in PRIVILEGED and row["telegram_id"] is None:
            try:
                resolved_uid = _resolve_agent_uid(conn, row["employee"])
            except UIDConflict:
                return {"action": "blocked", "error": "Неоднозначный внутренний ID; нужна ручная сверка."}
            if conn.execute("SELECT 1 FROM accounts WHERE telegram_id=? AND id<>?",
                            (resolved_uid, row["id"])).fetchone():
                return {"action": "blocked", "error": "Внутренний ID уже связан с другим аккаунтом."}
            conn.execute("UPDATE accounts SET telegram_id=? WHERE id=?",
                         (resolved_uid, row["id"]))
            row = conn.execute("SELECT * FROM accounts WHERE username=?",
                               (username,)).fetchone()
        role_row = conn.execute("SELECT name, role FROM managers WHERE telegram_id=?",
                                (row["telegram_id"],)).fetchone()
        from config import ADMIN_ID
        if ADMIN_ID and row["telegram_id"] == ADMIN_ID and row["role"] != "full":
            return {"action": "blocked", "error": "UID владельца требует полную роль и ручную сверку."}
        if ((row["role"] in PRIVILEGED and (not role_row
                 or str(role_row["role"] or "manager") != row["role"]
                 or db._name_key(role_row["name"]) != db._name_key(row["employee"])))
                or (row["role"] == "agent" and role_row)):
            return {"action": "blocked", "error": "Роль аккаунта и внутреннего ID расходятся; нужна ручная сверка."}
        if expected_uid is not None and row["telegram_id"] != expected_uid:
            return {"action": "conflict", "error":
                    "На компьютере остались записи другого сотрудника. Восстановите его вход "
                    "и отправьте очередь перед сменой аккаунта."}
        conn.execute("UPDATE accounts SET failed_attempts=0, locked_until=NULL WHERE id=?",
                     (row["id"],))
        dev = conn.execute(
            "SELECT * FROM account_devices WHERE account_id=? AND fingerprint=? AND kind=? "
            "AND status<>'revoked' ORDER BY id DESC LIMIT 1",
            (row["id"], fingerprint, device_kind)).fetchone()
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
        if pc:
            conn.execute('UPDATE account_devices SET public_key=? WHERE id=?',(pc['public_key'],dev['id']))
            dev=conn.execute('SELECT * FROM account_devices WHERE id=?',(dev['id'],)).fetchone()

        if device_kind == 'phone':
            conn.execute("UPDATE account_devices SET gps=?,gps_seen_at=? WHERE id=?",
                         (gps_text, datetime.now(timezone.utc).isoformat() if gps_text else None, dev['id']))
        verified_entry = bool(pc) if device_kind == 'desktop' else audience in ('mobile', 'crm-mobile')
        paused = verified_entry and account_policy.device_confirmation_paused(conn)
        if paused and row['role']=='agent' and row['status']=='awaiting' and row['password_review_pending'] and row['password_reset_operation'] and row['password_reset_actor']:
            actor = 'policy:device-confirmation-paused'
            conn.execute("UPDATE accounts SET status='active',password_review_pending=0,activated_by=?,activated_at=?,updated_at=? WHERE id=?",
                         (actor,_now(),_now(),row['id']))
            _approval_event(conn,row['id'],None,0,actor,'password_reset','approved',ip,
                            'Existing staff credential verified while manual confirmation is paused')
            row=conn.execute('SELECT * FROM accounts WHERE id=?',(row['id'],)).fetchone()
        account_active = row['status'] == 'active'
        if (dev['status'] == 'pending' and account_active and not row['password_review_pending']
                and paused
                and not conn.execute("SELECT 1 FROM account_devices WHERE account_id=? AND kind=? "
                                     "AND fingerprint=? AND status='revoked' LIMIT 1",
                                     (row['id'], device_kind, fingerprint)).fetchone()):
            actor = 'policy:device-confirmation-paused'
            approved = approve_device(dev['id'], actor, _connection=conn, ip=ip, replace_same_kind=False)
            if approved.get('active'):
                _approval_event(conn,row['id'],dev['id'],0,actor,'device_policy','approved',ip,
                                'Valid password and verified application entry while manual confirmation is paused')
                dev=conn.execute('SELECT * FROM account_devices WHERE id=?',(dev['id'],)).fetchone()
        # Outside the temporary window, a new staff phone is approved on its
        # own. An approved PC and GPS do not activate it.
        ever = conn.execute("SELECT 1 FROM account_devices WHERE account_id=? AND kind=? AND approved_at IS NOT NULL LIMIT 1",
                            (row['id'],device_kind)).fetchone()
        approved_pc = _approved_staff_pc(conn,row['id'])
        root_grant=conn.execute('SELECT next_pc_allowed,granted_by FROM account_admin_grants WHERE account_id=?',(row['id'],)).fetchone()
        root_next=bool(root_grant and root_grant['next_pc_allowed'] and device_kind=='desktop'
                       and (row['role']=='analyst' or root_grant['granted_by']=='ssh-root' and row['role'] in {'manager','full'}))
        reset_grant=conn.execute('SELECT actor FROM account_reset_device_grants '
            'WHERE account_id=? AND kind=? AND consumed_at IS NULL',(row['id'],device_kind)).fetchone()
        reset_next=bool(reset_grant and row['role'] in ('manager','security','full'))
        first_privileged = row['role'] in PRIVILEGED and row['status']=='active' and (not ever or root_next or reset_next)
        if audience=='crm-mobile' and row['role']=='analyst' and device_kind=='phone':
            first_privileged=False  # New analyst phones require explicit device approval.
        if dev['status']=='pending' and first_privileged:
            if root_next:
                conn.execute('UPDATE account_admin_grants SET next_pc_allowed=0 WHERE account_id=?',(row['id'],))
            approver = 'account:owner-password-reset' if reset_next else 'account:first-login'
            if reset_next:
                conn.execute('UPDATE account_reset_device_grants SET consumed_at=?,device_id=? '
                    'WHERE account_id=? AND kind=?',(_now(),dev['id'],row['id'],device_kind))
            conn.execute("UPDATE account_devices SET status='active',approved_by=?,approved_at=? WHERE id=?",(approver,_now(),dev['id']))
            _approval_event(conn,row['id'],dev['id'],int(row['telegram_id'] or 0),approver,'device','approved',ip,
                f"owner reset by {reset_grant['actor']}" if reset_next else 'first device after account approval')
            dev=conn.execute('SELECT * FROM account_devices WHERE id=?',(dev['id'],)).fetchone()
        token = _mint_token(conn, row, dev)
        device_active = dev["status"] == "active"
        action = "ok" if (account_active and device_active) else "ok_pending"
        _journal(conn, row["id"], username, action, entrance=entrance,
                 device_kind=device_kind, fingerprint=fingerprint, label=label,
                 ip=ip, gps=gps_text)
        conn.commit()
        return {"action": action, "token": token, "employee": row["employee"],
                "username": row["username"], "role": row["role"],
                "uid": row["telegram_id"],
                "office": row["office"] or "",
                "must_change_password": bool(row["must_change_password"]),
                "gps_exception_pending": False,
                "error": "" if action == "ok" else
                "Вход записан. Доступ подтвердит ваш руководитель или администратор."}


def device_gate_by_token(token_table: str, row_id: int) -> tuple[str, str]:
    """('ok'|'pending'|'blocked', ФИО) для middleware: решает, пропускать ли
    токен, выпущенный аккаунту. Вызывается на КАЖДЫЙ запрос — дёшево (индекс)."""
    if token_table not in ("agent_devices", "manager_devices"):
        return "blocked", ""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT a.status AS astatus, a.employee AS employee, d.status AS dstatus, "
            "a.id AS account_id, a.created_by AS created_by, a.role AS role,a.telegram_id AS telegram_id, "
            "a.must_change_password AS change_password, a.password_review_pending, d.id AS device_id, d.kind AS device_kind, d.gps AS gps, "
            "d.gps_exception_until AS gps_exception_until, d.gps_seen_at AS gps_seen_at "
            "FROM accounts a JOIN account_devices d ON d.account_id=a.id "
            "JOIN account_token_sessions s ON s.device_id=d.id "
            "WHERE s.token_table=? AND s.token_id=?", (token_table, row_id)).fetchone()
        if row is None:
            return "blocked", ""
        if row["astatus"] == "disabled" or row["dstatus"] == "revoked":
            return "blocked", row["employee"]
        live_role=conn.execute('SELECT name,role FROM managers WHERE telegram_id=?',(row['telegram_id'],)).fetchone()
        if ((row['role'] in PRIVILEGED and (not live_role or str(live_role['role'] or 'manager')!=row['role']
                    or db._name_key(live_role['name'])!=db._name_key(row['employee'])))
                or (row['role']=='agent' and live_role)):
            return 'blocked',row['employee']
        import staff_roster
        if row['role']=='agent' and any(not r['active'] for r in staff_roster.rows(conn,active_only=False)
                if db._name_key(r['employee'])==db._name_key(row['employee'])):
            return 'blocked',row['employee']
        import account_policy
        if row['password_review_pending']:
            return ('password' if row['change_password'] else 'pending'), row['employee']
        strict=account_policy.strict(connection=conn)
        if (strict and row['role']=='agent' and not _approved_staff_pc(conn,row['account_id'])
                and not (row['device_kind']=='phone' and _approved_mobile_identity(conn,row['device_id']))):
            return 'pending',row['employee']
        if row["astatus"] == "active" and row["dstatus"] == "active":
            if row["change_password"]:
                return "password", row["employee"]
            return "ok", row["employee"]
        return "pending", row["employee"]


# --- смена пароля ------------------------------------------------------------

def change_password(username: str, current: str, new: str) -> dict:
    username = normalize_username(username)
    if not _valid_password(new or ""):
        return {"ok": False, "error":
                "Пароль от 8 символов, с буквой и цифрой."}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if row and (row["status"] == "disabled" or (row["locked_until"] or "") > _now()):
            return {"ok": False, "error": "Аккаунт отключён или временно заблокирован."}
        # Тайминг смены пароля не должен выдавать, существует ли логин.
        ok = verify_password(current or "", row["password_hash"]) if row else \
            verify_password(current or "", _dummy_hash())
        if row is None or not ok:
            if row:
                fails = int(row["failed_attempts"] or 0) + 1
                locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(
                    timespec="seconds") if fails >= LOCK_AFTER_FAILS else None
                conn.execute("UPDATE accounts SET failed_attempts=?,locked_until=? WHERE id=?",
                             (fails, locked, row["id"]))
            _journal(conn, row["id"] if row else None, username, "bad_password")
            conn.commit()
            return {"ok": False, "error": "Текущий пароль неверен."}
        if hmac.compare_digest(current.encode(), new.encode()):
            return {"ok": False, "error": "Новый пароль совпадает с текущим."}
        conn.execute("UPDATE accounts SET password_hash=?, must_change_password=0, "
                     "failed_attempts=0, locked_until=NULL, "
                     "updated_at=? WHERE id=?",
                     (hash_password(new), _now(), row["id"]))
        _journal(conn, row["id"], username, "passwd")
        conn.commit()
    return {"ok": True}


# --- действия администратора ----------------------------------------------------

def register_approver(device_id: int, principal: str) -> dict:
    """SSH-only: associate an enrolled operator with a person, never from HTTP."""
    principal = unicodedata.normalize("NFKC", str(principal or "")).strip().casefold()
    if not principal or len(principal) > 64:
        return {"ok": False, "error": "Укажите постоянный идентификатор администратора (до 64 символов)."}
    with db.connect() as conn:
        changed = conn.execute("UPDATE admin_devices SET account_approver=? "
                               "WHERE id=? AND revoked_at IS NULL AND role='operator'",
                               (principal, device_id)).rowcount
        conn.commit()
    if not changed:
        return {"ok": False, "error": "Нет действующего устройства оператора с таким id."}
    db.audit_access(0, "account_approver_register", str(device_id), principal)
    return {"ok": True, "principal": principal}


def _add_approval(approvals_json: str, by: str) -> list[str]:
    try:
        raw = json.loads(approvals_json or "[]")
        listed = raw if isinstance(raw, list) else []
    except (ValueError, TypeError):
        listed = []
    by = unicodedata.normalize("NFKC", str(by or "")).strip().casefold()
    listed = list(dict.fromkeys(unicodedata.normalize("NFKC", str(x)).strip().casefold()
                               for x in listed if str(x).strip()))
    if by and by not in listed:
        listed.append(by)
    return listed


def _approval_event(conn, account_id, device_id, actor_uid, actor, action, decision, ip="", detail=""):
    conn.execute("INSERT INTO account_approval_events "
                 "(account_id,device_id,actor_uid,actor,action,decision,ip,detail,at) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", (account_id, device_id, actor_uid, actor,
                 action, decision, ip[:64], detail[:2000], _now()))


def approve_account(username: str, by: str, *, _connection=None, actor_uid=0, ip="") -> dict:
    """Подтверждение администратора. Сотрудник активируется с первого;
    руководитель/аналитик — с одного реального администратора по правилу
    владельца от 18.10.2026. Principal сохраняет одну личность оператора.
    Чтение-изменение-запись под BEGIN IMMEDIATE: два одновременных
    подтверждения разных админов иначе теряли одно (воспроизведено ревью
    15.10: approvals оставался ["admin-egor"], аккаунт — awaiting)."""
    username = normalize_username(username)
    with (db.connect() if _connection is None else nullcontext(_connection)) as conn:
        if _connection is None:
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            return {"ok": False, "error": "Нет такого аккаунта."}
        if row["status"] == "disabled":
            return {"ok": False, "error": "Аккаунт закрыт; сначала enable."}
        password_review = bool(row['password_review_pending'])
        if password_review:
            from config import ADMIN_ID
            manager, team = _manager_review_context(actor_uid, actor_uid == ADMIN_ID) if _connection is not None and actor_uid else (None, set())
            scoped_leader = bool(manager and by == f"manager:{actor_uid}:{manager.get('name') or ''}"
                                 and _reviewable(conn, row, actor_uid, manager, team))
            if not (_owner_approver(conn, by) or scoped_leader):
                return {"ok": False, "error": "После смены пароля доступ подтверждает свой руководитель."}
            if row['must_change_password']:
                return {"ok": False, "error": "Сотрудник ещё не сменил временный пароль на личный."}
        import account_policy
        if not password_review and (row["created_by"] == "self-registration" or (row['role']=='agent' and account_policy.strict(connection=conn))) and _connection is None:
            return {"ok": False, "error": "Регистрацию на компьютере подтверждает свой руководитель."}
        approvals = _add_approval(row["approvals"], by)
        need = _approvals_needed(row["role"])
        active = len(approvals) >= need and row["status"] != "active"
        already = row["status"] == "active"
        if active or already:
            conn.execute("UPDATE accounts SET status='active', approvals=?, activated_by=?, "
                         "activated_at=?, updated_at=? WHERE id=?",
                         (json.dumps(approvals), by, _now(), _now(), row["id"]))
        else:
            conn.execute("UPDATE accounts SET approvals=?, updated_at=? WHERE id=?",
                         (json.dumps(approvals), _now(), row["id"]))
        _journal(conn, row["id"], username, "approved", label=f"by {by}")
        _approval_event(conn, row["id"], None, actor_uid, by, "account", "approved", ip,
                        f"{row['status']} -> {'active' if active or already else 'awaiting'}")
        if password_review:
            conn.execute("UPDATE accounts SET password_review_pending=0 WHERE id=?", (row['id'],))
            _approval_event(conn, row['id'], None, actor_uid, by, 'password_reset', 'approved', ip,
                            f"operation={row['password_reset_operation']}")
        if _connection is None:
            conn.commit()
            db.audit_access(actor_uid, "account_approve", row["employee"], f"{username} by {by}")
        if already or len(approvals) >= need:
            return {"ok": True, "active": True, "approvals": approvals}
        return {"ok": True, "active": False, "need": need,
                "approvals": approvals,
                "error": f"Подтверждено ({len(approvals)} из {need}). "
                         f"Нужно подтверждение ещё одного администратора."}


def approve_device(device_id: int, by: str, *, _connection=None, actor_uid=0, ip="", replace_same_kind=True) -> dict:
    """Подтвердить устройство (сотруднику — с первого подтверждения,
    руководителю/аналитику — тоже с одного администратора). Активация нового
    ТЕЛЕФОНА гасит прежний телефон аккаунта: сменить — значит сменить.
    Под BEGIN IMMEDIATE — как approve_account: одновременные подтверждения
    двух администраторов иначе теряют одно (lost update на approvals)."""
    with (db.connect() if _connection is None else nullcontext(_connection)) as conn:
        if _connection is None:
            conn.execute("BEGIN IMMEDIATE")
        dev = conn.execute("SELECT * FROM account_devices WHERE id=?", (device_id,)).fetchone()
        if dev is None:
            return {"ok": False, "error": "Нет такого устройства."}
        acc = conn.execute("SELECT * FROM accounts WHERE id=?",
                           (dev["account_id"],)).fetchone()
        if not acc or acc["status"] == "disabled" or dev["status"] == "revoked":
            return {"ok": False, "error": "Аккаунт или устройство отозвано. Выполните новый вход."}
        if acc['password_review_pending']:
            return {"ok": False, "error": "После смены пароля сначала требуется подтверждение руководителя."}
        import account_policy
        if (acc["created_by"] == "self-registration" or (acc['role']=='agent' and account_policy.strict(connection=conn))) and _connection is None:
            return {"ok": False, "error": "Устройство сотрудника подтверждает свой руководитель."}
        need = _approvals_needed(acc["role"]) if acc else 1
        approvals = _add_approval(dev["approvals"], by)
        table = dev["token_table"] or "agent_devices"
        activated = dev["status"] == "active" or len(approvals) >= need
        if activated and dev["status"] != "active":
            conn.execute("UPDATE account_devices SET status='active', approved_by=?, "
                         "approved_at=?, approvals=? WHERE id=?",
                         (by, _now(), json.dumps(approvals), device_id))
            if replace_same_kind and dev["kind"] in ("phone", "desktop"):
                old = conn.execute(
                    "SELECT id, agent_dev_id, token_table FROM account_devices "
                    "WHERE account_id=? AND kind=? AND status='active' AND id<>?",
                    (dev["account_id"], dev['kind'], device_id)).fetchall()
                for row in old:
                    conn.execute("UPDATE account_devices SET status='revoked', revoked_at=? "
                                 "WHERE id=?", (_now(), row["id"]))
                    _revoke_slot_tokens(conn,row['id'],f"account:new-{dev['kind']}:{by}")
        else:
            conn.execute("UPDATE account_devices SET approvals=? WHERE id=?",
                         (json.dumps(approvals), device_id))
        if acc:
            _journal(conn, dev["account_id"], acc["username"], "approved",
                     device_kind=dev["kind"], label=dev["label"], gps=dev["gps"] or "",
                     ip=f"by {by}")
        _approval_event(conn, dev["account_id"], device_id, actor_uid, by, "device", "approved", ip,
                        f"{dev['kind']} {dev['label']} {dev['status']} -> {'active' if activated else 'pending'}")
        if _connection is None:
            conn.commit()
            db.audit_access(actor_uid, "account_device_approve", str(dev["label"]),
                            f"#{device_id} {dev['kind']} by {by}")
        if activated:
            return {"ok": True, "active": True, "approvals": approvals}
        return {"ok": True, "active": False, "need": need, "approvals": approvals,
                "error": f"Подтверждено ({len(approvals)} из {need}). "
                         f"Нужно подтверждение ещё одного администратора."}


def _manager_review_context(uid: int, is_owner: bool):
    import digest_data
    manager = digest_data.manager_scope(uid, is_owner=is_owner)
    if not manager or not db.manager_caps(manager.get("role"), is_owner=is_owner)["binds_edit"]:
        return None, set()
    team = {db._name_key(e) for e in digest_data.sorted_employees(manager)}
    return manager, team


def _privileged_names(conn):
    names = [r[0] for r in conn.execute("SELECT name FROM managers")]
    names += [r[0] for r in conn.execute("SELECT b.employee FROM agent_bind b "
                                       "JOIN managers m ON m.telegram_id=b.telegram_id")]
    names += [r[0] for r in conn.execute("SELECT employee FROM accounts WHERE role<>'agent'")]
    return {db._name_key(name) for name in names if name}


def _reviewable(conn, acc, uid, manager, team, *, declared=None, privileged=None):
    keys = {db._name_key(acc['employee'])} if acc else set()
    if acc:
        from team_entry import families
        declared=families(conn) if declared is None else declared
        listed=[r for r in declared if db._name_key(acc['employee']) in r['keys']]
        if len(listed)>1 or any(not r['active'] or (r['manager_uid'] is not None and r['manager_uid']!=uid) for r in listed):
            return False
        if listed:
            keys=listed[0]['keys']
            if any(keys & r['keys'] for r in declared if r['employee_id']!=listed[0]['employee_id']):return False
    return bool(acc and manager and acc["role"] == "agent" and acc["status"] != "disabled"
                and keys & team
                and acc["telegram_id"] != uid
                and db._name_key(acc["employee"]) != db._name_key(manager.get("name"))
                and not keys & (_privileged_names(conn) if privileged is None else privileged)
                and not conn.execute("SELECT 1 FROM managers WHERE telegram_id=?", (acc["telegram_id"],)).fetchone())


def request_phone_registration(*args, **kwargs):
    return {'ok':False,'error':'Телефон входит по личному паролю. Компьютер и телефон сотрудника подтверждает свой руководитель во «Входе команды».'}


def approve_phone_registration(*args, **kwargs):
    return {'ok':False,'error':'Операторские коды телефона закрыты. Используйте одобрение своего руководителя в программе.'}


def manager_reviewable(uid: int, *, is_owner=False) -> set[str]:
    manager, team = _manager_review_context(uid, is_owner)
    with db.connect() as conn:
        import account_policy
        return {a["username"] for a in conn.execute("SELECT * FROM accounts")
                if _reviewable(conn, a, uid, manager, team)}


def request_gps_exception(token_table, token_id, account_id, ip=''):
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        dev=conn.execute('SELECT d.*,a.status account_status,a.role,a.telegram_id FROM account_devices d '
            'JOIN account_token_sessions s ON s.device_id=d.id JOIN accounts a ON a.id=d.account_id '
            'WHERE s.token_table=? AND s.token_id=? AND a.id=?',(token_table,token_id,account_id)).fetchone()
        if not dev or dev['kind']!='phone' or dev['status']=='revoked' or dev['account_status']!='active':
            return {'ok':False,'error':'Завершите регистрацию и получите подтверждение своего руководителя во «Входе команды».'}
    # Compatibility for older shells: never demote an approved phone.
    return {'ok':True,'pending':dev['status']!='active','gps_required':False}


def manager_approve(uid: int, *, username="", device_id=None, is_owner=False, ip="",
                    observed_pc=False, gps_exception_reason='', gps_exception_minutes=60) -> dict:
    """Recheck the live team and role under the same write lock as the approval."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        manager, team = _manager_review_context(uid, is_owner)
        actor = f"manager:{uid}:{(manager or {}).get('name') or 'Владелец'}"
        device = (conn.execute("SELECT * FROM account_devices WHERE id=?", (device_id,)).fetchone()
                  if device_id is not None else None)
        acc = (conn.execute("SELECT * FROM accounts WHERE id=?", (device["account_id"],)).fetchone()
               if device else conn.execute("SELECT * FROM accounts WHERE username=?", (normalize_username(username),)).fetchone())
        import account_policy
        if (not _reviewable(conn, acc, uid, manager, team)
                or (device_id is not None and (not device or device["status"] == "revoked"))):
            _approval_event(conn, acc["id"] if acc else None, device_id, uid, actor,
                            "device" if device_id is not None else "account", "denied", ip, "scope, role or state")
            conn.commit()
            return {"ok": False, "error": "account"}
        if acc['password_review_pending'] and acc['must_change_password']:
            return {'ok':False,'error':'Сотрудник ещё не сменил временный пароль на личный.'}
        if acc['created_by']=='self-registration' or account_policy.strict(connection=conn):
            if device is None:
                return {'ok':False,'error':'Выберите заявку компьютера или телефона сотрудника.'}
            if device['kind']=='desktop' and (not observed_pc or not device['public_key']):
                return {'ok':False,'error':'Сверьте сотрудника и его компьютер лично; подтвердите эту проверку.'}
            if device['kind']=='desktop':
                _approval_event(conn,acc['id'],device['id'],uid,actor,'pc_observation','approved',ip,'Employee and PC observed in person')
            elif device['kind']=='phone':
                _approval_event(conn,acc['id'],device['id'],uid,actor,'phone_identity','approved',ip,'Manager confirmed employee identity and phone; no prior PC required')
        if acc['status']!='active' or acc['password_review_pending']:
            first=approve_account(acc['username'],actor,_connection=conn,actor_uid=uid,ip=ip)
            if not first.get('ok'): return first
        if device_id is None:
            result = approve_account(acc["username"], actor, _connection=conn, actor_uid=uid, ip=ip)
        else:
            result = approve_device(device_id, actor, _connection=conn, actor_uid=uid, ip=ip)
        conn.commit()
    db.audit_access(uid, "manager_account_approve", acc["employee"],
                    f"account={acc['id']} device={device_id} by={actor} ip={ip}")
    return result


def _assert_uid_owned(conn: sqlite3.Connection, account: sqlite3.Row) -> int | None:
    """Refuse collateral revocation on an unresolved shared/foreign legacy UID."""
    uid = account["telegram_id"]
    repair_uid = False
    if uid is None:
        # Old account rows could predate the UID migration. Do not leave a
        # known same-person legacy bearer alive merely because the new account
        # has not yet logged in; equally, never choose the first fuzzy match.
        matches = {int(r["telegram_id"]) for r in conn.execute(
            "SELECT telegram_id,employee FROM agent_bind WHERE approved=1")
            if db._name_key(r["employee"]) == db._name_key(account["employee"])}
        if len(matches) > 1:
            raise UIDConflict("Старый аккаунт имеет несколько возможных UID; нужна ручная сверка.")
        if matches:
            uid = next(iter(matches))
            repair_uid = True
        else:
            return
    if conn.execute("SELECT 1 FROM accounts WHERE telegram_id=? AND id<>?",
                    (uid, account["id"])).fetchone():
        raise UIDConflict("Внутренний ID принадлежит нескольким аккаунтам; нужна ручная сверка.")
    for table, name in (("agent_bind", "employee"), ("managers", "name")):
        for owner in conn.execute(f"SELECT {name} FROM {table} WHERE telegram_id=?", (uid,)):
            if db._name_key(owner[0]) != db._name_key(account["employee"]):
                raise UIDConflict("Внутренний ID также принадлежит другому человеку; нужна ручная сверка.")
    if repair_uid:
        conn.execute("UPDATE accounts SET telegram_id=? WHERE id=?", (uid, account["id"]))
    return uid


def _revoke_account_tokens(conn: sqlite3.Connection, account_id: int, by: str) -> None:
    """Account reset/disable ends every old credential for its unambiguous UID.

    Logout deliberately does NOT use this: it retires just the current session.
    """
    account = conn.execute("SELECT id,telegram_id,employee FROM accounts WHERE id=?",
                           (account_id,)).fetchone()
    uid = _assert_uid_owned(conn, account)
    now = _now()
    for table in ("agent_devices", "manager_devices"):
        conn.execute(f"UPDATE {table} SET revoked_at=?,revoked_by=? "
                     "WHERE (account_id=? OR (? IS NOT NULL AND telegram_id=?)) "
                     "AND revoked_at IS NULL", (now, by, account_id, uid, uid))
    if uid is not None:
        conn.execute("DELETE FROM app_tokens WHERE telegram_id=?", (uid,))
        conn.execute("DELETE FROM app_links WHERE telegram_id=?", (uid,))


def legacy_uid_deleted(uid: int) -> bool:
    with db.connect() as conn:
        return bool(conn.execute("SELECT 1 FROM account_deletions WHERE telegram_id=?", (uid,)).fetchone())


def delete_account(username: str, by: str, *, expected_id: int) -> dict:
    """Remove credentials and device enrollment atomically; retain business facts/audit.

    The numeric account generation prevents a stale dialog deleting a newly-created
    account under a reused username. Historical tokens remain revoked permanently.
    """
    username = normalize_username(username)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = delete_account_in_transaction(conn, username, by, expected_id=expected_id)
        if not result.get('ok'):
            return result
        conn.commit()
    db.audit_access(0, "account_delete", username, f"account={expected_id} by {by}")
    return result


def delete_account_in_transaction(conn, username: str, by: str, *, expected_id: int) -> dict:
    """Same deletion inside the caller's transaction (e.g. remove from staff)."""
    row = conn.execute("SELECT id,telegram_id,employee FROM accounts WHERE username=?", (username,)).fetchone()
    if row is None or type(expected_id) is not int or row['id'] != expected_id:
        return {"ok": False, "error": "Аккаунт уже удалён или изменился. Обновите список."}
    if conn.execute("SELECT 1 FROM account_admin_grants WHERE account_id=? AND granted_by='ssh-root'", (row['id'],)).fetchone():
        return {"ok": False, "error": "Первичный профиль владельца защищён от удаления."}
    try:
        uid = _assert_uid_owned(conn, row)
    except UIDConflict as exc:
        return {"ok": False, "error": str(exc)}
    _revoke_account_tokens(conn, row['id'], f"account:delete:{by}")
    conn.execute("INSERT INTO account_deletions VALUES(?,?,?,?)", (row['id'], uid, _now(), by))
    conn.execute("DELETE FROM account_phone_requests WHERE account_id=?", (row['id'],))
    conn.execute("DELETE FROM account_client_versions WHERE account_id=?", (row['id'],))
    conn.execute("DELETE FROM account_token_sessions WHERE device_id IN "
                 "(SELECT id FROM account_devices WHERE account_id=?)", (row['id'],))
    conn.execute("DELETE FROM account_devices WHERE account_id=?", (row['id'],))
    conn.execute("DELETE FROM account_admin_grants WHERE account_id=?", (row['id'],))
    _approval_event(conn, row['id'], None, 0, by, 'account_delete', 'deleted')
    conn.execute("DELETE FROM accounts WHERE id=?", (row['id'],))
    return {"ok": True, "deleted": True, "account_id": expected_id}


def set_status(username: str, status: str, by: str) -> dict:
    username = normalize_username(username)
    if status not in ("active", "awaiting", "disabled"):
        return {"ok": False, "error": "status"}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id,telegram_id,employee FROM accounts WHERE username=?", (username,)).fetchone()
        if row is None:
            return {"ok": False, "error": "Нет такого аккаунта."}
        if status == "disabled":
            try:
                _assert_uid_owned(conn, row)
            except UIDConflict as exc:
                return {"ok": False, "error": str(exc)}
        # Re-enabling an account must go through its normal approval policy.
        if status == "active":
            status = "awaiting"
        conn.execute("DELETE FROM account_reset_device_grants WHERE account_id=?", (row['id'],))
        conn.execute("UPDATE accounts SET status=?, approvals='[]', activated_by=NULL, "
                     "activated_at=NULL, updated_at=? WHERE id=?",
                     (status, _now(), row["id"]))
        if status == "disabled":   # сразу погасить все токены аккаунта
            _revoke_account_tokens(conn, row["id"], f"account:disable:{by}")
            conn.execute("UPDATE account_devices SET status='revoked', revoked_at=? "
                         "WHERE account_id=? AND status<>'revoked'", (_now(), row["id"]))
        conn.commit()
    db.audit_access(0, "account_status", username, f"{status} by {by}")
    return {"ok": True}


# --- списки -------------------------------------------------------------------

def entered_list(office: str = "", *, include_empty=False) -> list[dict]:
    """«Кто ввёл»: строка на аккаунт с телефонным и компьютерным вводом.
    Руководитель пересылает список администраторам; ready = введено и там, и там."""
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
            if phone is None and desktop is None and not include_empty:
                continue
            out.append({
                "account_id": acc["id"], "actor_uid": acc["telegram_id"],
                "employee": acc["employee"], "username": acc["username"],
                "office": acc["office"] or "", "status": acc["status"], "role": acc["role"],
                "password_review_pending": bool(acc['password_review_pending']),
                "must_change_password": bool(acc['must_change_password']),
                "self_registration": acc["created_by"] == "self-registration",
                "devices": [{"id": d["id"], "kind": d["kind"], "label": d["label"],
                             "status": d["status"], "at": d["first_seen"],
                             "last_seen": d['last_seen'],
                             **client_versions.for_device(conn, d['id']),
                             "gps_present": bool(d['gps'] and d['gps_seen_at'] and d['gps_seen_at']>(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()),
                             "gps_exception_until": d['gps_exception_until'],
                             "approved_by": d["approved_by"], "approved_at": d["approved_at"]}
                            for d in devices],
                "phone": {"label": phone["label"], "at": phone["first_seen"],
                          "gps": phone["gps"] or "", "confirmed": phone["status"] == "active"}
                if phone else None,
                "desktop": {"label": desktop["label"], "at": desktop["first_seen"],
                            "confirmed": desktop["status"] == "active"} if desktop else None,
                "ready": acc['status']=='active' and any(d['status']=='active' for d in devices),
                "desktop_ready": bool(desktop and desktop['status']=='active'),
            })
        return out


def is_server_operator(account_id, uid):
    """Authority is attached to the exact root-provisioned account."""
    with db.connect() as conn:
        return conn.execute(
            "SELECT 1 FROM account_admin_grants g JOIN accounts a ON a.id=g.account_id "
            "WHERE a.id=? AND a.telegram_id=? AND a.status='active' AND a.role IN ('manager','full') "
            "AND g.granted_by='ssh-root'",
            (account_id, uid),
        ).fetchone() is not None


def admin_accounts() -> list[dict]:
    """Полный список для администратора/аналитика: аккаунты, устройства, журнал."""
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
            approvals = conn.execute("SELECT device_id,actor_uid,actor,action,decision,ip,detail,at "
                                     "FROM account_approval_events WHERE account_id=? "
                                     "ORDER BY id DESC LIMIT 50", (acc["id"],)).fetchall()
            # The operator needs EVERY still-redeemable phone request, even
            # when one account has >5 pending replacement devices. Bound only
            # the historical rows, not the current decisions it can make.
            recent = conn.execute(
                "SELECT id,device_id,manager_uid,issued_at,expires_at,consumed_at,operator_principal "
                "FROM account_phone_requests WHERE account_id=? ORDER BY id DESC LIMIT 5",
                (acc["id"],)).fetchall()
            live = conn.execute(
                "SELECT id,device_id,manager_uid,issued_at,expires_at,consumed_at,operator_principal "
                "FROM account_phone_requests WHERE account_id=? AND consumed_at IS NULL "
                "AND expires_at>? ORDER BY id DESC",
                (acc["id"], datetime.now(timezone.utc).isoformat(timespec="seconds"))).fetchall()
            requests = [{k: v for k, v in dict(r).items() if k != "id"}
                        for r in sorted({r["id"]: r for r in [*recent, *live]}.values(),
                                        key=lambda r: r["id"], reverse=True)]
            out.append({"id": acc["id"], "username": acc["username"],
                        "owner_root": bool(conn.execute("SELECT 1 FROM account_admin_grants WHERE account_id=? AND granted_by='ssh-root'", (acc['id'],)).fetchone()),
                        "employee": acc["employee"], "office": acc["office"] or "",
                        "created_by": acc["created_by"] or "",
                        "registration_gps": acc["registration_gps"] or "",
                        "gps_exception_pending": bool(acc["created_by"] == "self-registration" and not acc["registration_gps"]),
                        "registration_ip": acc["registration_ip"] or "",
                        "phone_requests": requests,
                        "role": acc["role"], "status": acc["status"],
                        "must_change_password": bool(acc["must_change_password"]),
                        "password_review_pending": bool(acc['password_review_pending']),
                        "created_at": acc["created_at"], "activated_at": acc["activated_at"],
                        "devices": [{**dict(d), **client_versions.for_device(conn, d['id'])}
                                    for d in devices],
                        "approval_events": [dict(e) for e in approvals],
                        "logins": [dict(l) for l in logins]})
        return out


def sessions_for(agent_dev_id: int, account_id: int, limit: int = 30) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT kind,at,entrance,device_kind,label,ip,gps FROM account_logins "
            "WHERE account_id=? ORDER BY id DESC LIMIT ?", (account_id, limit)).fetchall()
    return [dict(r) for r in rows]


def manager_revoke_device(uid, device_id, *, is_owner=False, ip=''):
    if type(device_id) is not int or device_id<=0:return {'ok':False,'error':'device_id'}
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        manager,team=_manager_review_context(uid,is_owner)
        device=conn.execute('SELECT * FROM account_devices WHERE id=?',(device_id,)).fetchone()
        acc=conn.execute('SELECT * FROM accounts WHERE id=?',(device['account_id'],)).fetchone() if device else None
        if not _reviewable(conn,acc,uid,manager,team):return {'ok':False,'error':'Устройство недоступно руководителю.'}
        actor=f'manager:{uid}'
        _revoke_slot_tokens(conn,device_id,actor)
        if device['status']!='revoked':
            conn.execute("UPDATE account_devices SET status='revoked',revoked_at=? WHERE id=?",(_now(),device_id))
            _approval_event(conn,acc['id'],device_id,uid,actor,'device','revoked',ip,'manager device revocation')
        conn.commit()
    return {'ok':True,'revoked':device_id}


def manager_team(uid, *, is_owner=False):
    import team_work
    manager,team=_manager_review_context(uid,is_owner)
    if not manager:return {'ok':False,'error':'Нет права управления командой.'}
    board=team_work.board(team=team,manager=manager,writable=True)
    reviewable=manager_reviewable(uid,is_owner=is_owner)
    entered={r['account_id']:r for r in entered_list(include_empty=True)}
    for row in board['employees']:
        account=entered.get(row['account_id'])
        row['account']=account
        row['can_reset']=bool(account and account['username'] in reviewable and not account['password_review_pending'])
        row['can_approve']=bool(account and account['username'] in reviewable and not (account['password_review_pending'] and account['must_change_password']))
        row['can_revoke']=bool(account and account['username'] in reviewable)
        if account and account.get('phone'):account['phone'].pop('gps',None)
    return board


def revoke_account_device(device_id: int, *, by: str = "operator") -> bool:
    """Retire a logical device even after its credential has been deleted.

    Password reset revokes tokens; their next request removes the token rows.
    The approval still exists and must be revocable independently of them.
    """
    if type(device_id) is not int or device_id <= 0:
        return False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        device = conn.execute("SELECT id,account_id,status FROM account_devices WHERE id=?",
                              (device_id,)).fetchone()
        if device is None:
            return False
        _revoke_slot_tokens(conn, device_id, by)
        if device["status"] != "revoked":
            conn.execute("UPDATE account_devices SET status='revoked',revoked_at=? WHERE id=?",
                         (_now(), device_id))
            _approval_event(conn, device["account_id"], device_id, 0, by,
                            "device", "revoked", detail=f"operator:account_device#{device_id}")
        conn.commit()
    return True


def revoke_approved_device(token_table: str, row_id: int, *, by: str = "operator") -> bool:
    """Operator device revocation differs from self logout: approval is retired.

    Next password login with the same fingerprint creates a NEW pending device,
    never silently reuses the formerly approved one. Old token rows may already
    have rotated; retire the approval only when this row is its current token.
    """
    if token_table not in ("agent_devices", "manager_devices"):
        return False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        token = conn.execute(f"SELECT account_id FROM {token_table} WHERE id=?", (row_id,)).fetchone()
        if not token:
            return False
        now = _now()
        changed = conn.execute(f"UPDATE {token_table} SET revoked_at=?,revoked_by=? "
                               "WHERE id=? AND revoked_at IS NULL", (now, by, row_id)).rowcount
        approval_revoked = False
        if token["account_id"]:
            approved = conn.execute("SELECT d.id,d.account_id FROM account_devices d JOIN account_token_sessions s ON s.device_id=d.id WHERE s.token_table=? "
                                    "AND s.token_id=? AND d.status<>'revoked'",
                                    (token_table, row_id)).fetchone()
            if approved:
                conn.execute("UPDATE account_devices SET status='revoked',revoked_at=? WHERE id=?",
                             (now, approved["id"]))
                _revoke_slot_tokens(conn,approved['id'],by)
                approval_revoked = True
                _approval_event(conn, approved["account_id"], approved["id"], 0, by,
                                "device", "revoked", detail=f"operator:{token_table}#{row_id}")
        conn.commit()
    return bool(changed or approval_revoked)


def revoke_token(token_table: str, row_id: int, by: str = "self:account") -> bool:
    """Погасить строку токена В ЕГО таблице (agent_devices/manager_devices).
    account_devices строку не трогаем: устройство остаётся подтверждённым,
    следующий вход выпустит свежий токен без нового одобрения — как у
    ссылочного выхода (_agent_device_logout)."""
    if token_table not in ("agent_devices", "manager_devices"):
        return False
    now = _now()
    with db.connect() as conn:
        cur = conn.execute(f"UPDATE {token_table} SET revoked_at=?, revoked_by=? "
                           "WHERE id=? AND revoked_at IS NULL", (now, by, row_id))
        conn.commit()
        return cur.rowcount > 0


def account_of_token(token_table: str, row_id: int) -> tuple[int, str] | None:
    """(account_id, username) по строке токена — для своих ручек
    (сессии/выход) прямо токеном."""
    if token_table not in ("agent_devices", "manager_devices"):
        return None
    with db.connect() as conn:
        row = conn.execute(
            "SELECT a.id AS aid, a.username AS username FROM accounts a "
            "JOIN account_devices d ON d.account_id=a.id JOIN account_token_sessions s ON s.device_id=d.id "
            "WHERE s.token_table=? AND s.token_id=?", (token_table, row_id)).fetchone()
    return (int(row["aid"]), row["username"]) if row else None
