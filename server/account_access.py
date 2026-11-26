"""Управление аккаунтами «логин-пароль»: только на самом сервере.

Как и у admin_access.py, HTTP-ручек записи здесь НЕТ намеренно: право завести
аккаунт или сменить пароль равно доступу к серверу (SSH/панель ИТ). Входить
сотрудники будут сами — логином и паролем на телефоне (GPS обязателен) и на
компьютере; доступ открывается подтверждением администратора (approve), после того как
руководитель переслал список «кто ввёл» (см. /api/manager/entered).

    python account_access.py create --employee "ФИО" --username kurbanov --office Душанбе
    python account_access.py bulk --csv staff.csv          # ФИО;офис;логин;пароль
    python account_access.py passwd --username kurbanov
    python account_access.py entered [--office Душанбе]    # кто ввёл (там и там)
    python account_access.py approve --username kurbanov   # команда владельца
    python account_access.py approve-device --id 12
    python account_access.py disable --username kurbanov
    python account_access.py list [--by-office]

Пароль печатается ОДИН раз; в базе только scrypt-хэш. Внутри контура:

    cd /opt/hard/deploy/units/core
    docker compose exec -T app python /app/account_access.py list --by-office
"""

from __future__ import annotations

import argparse
import csv
import secrets
import sys

import accounts
import db


def _out(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text)


def _gen_password() -> str:
    # Читаемо для диктовки в офисе: без 0/O/1/l/I; буква+цифра гарантированы.
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(10))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def cmd_create(args: argparse.Namespace) -> int:
    password = args.password or _gen_password()
    result = accounts.create_account(args.employee, args.username, password,
                                     office=args.office or "", role=args.role, by="cli",
                                     telegram_id=args.telegram_id)
    if not result.get("ok"):
        _out("ОТКАЗ: " + result.get("error", ""))
        return 1
    _out(f"Аккаунт создан: {result['username']} ({args.employee}"
         + (f", офис {args.office}" if args.office else "") + ")")
    _out(f"Пароль (сообщить один раз, на экране не повторится): {password}")
    if args.role != "agent":
        _out("Для активации требуется подтверждение администратора.")
    return 0


def cmd_bulk(args: argparse.Namespace) -> int:
    rows: list[list[str]] = []
    if args.csv:
        with open(args.csv, encoding="utf-8-sig") as f:
            text = f.read()
        first = next((line for line in text.splitlines() if line.strip()), "")
        delimiter = ";" if ";" in first else "\t" if "\t" in first else ","
        rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    if not rows:
        _out("CSV пуст или не передан (--csv файл.csv, колонки: ФИО;офис;логин;пароль).")
        return 1
    made, failed = 0, []
    for row in rows:
        if not row or row[0].strip().startswith(("#", "ФИО")):
            continue
        employee = row[0].strip()
        office = row[1].strip() if len(row) > 1 else ""
        username = row[2].strip() if len(row) > 2 and row[2].strip() else ""
        password = row[3].strip() if len(row) > 3 and row[3].strip() else _gen_password()
        if not username:
            username = _username_for(employee)
        result = accounts.create_account(employee, username, password,
                                         office=office, by="cli-bulk")
        if result.get("ok"):
            made += 1
            _out(f"OK  {username:20} {employee} [{office}] пароль: {password}")
        else:
            failed.append((employee, result.get("error", "")))
            _out(f"FAIL {employee}: {result.get('error', '')}")
    _out(f"\nИтого: создано {made}, отказов {len(failed)}")
    return 0 if not failed else 1


def _username_for(employee: str) -> str:
    """A readable transliteration, with a suffix when an existing login is taken."""
    import re
    alphabet = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяқғҳҷӯӣ",
                        ("a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch "
                         "_ y _ e yu ya q gh h j u i").split()))
    raw = "".join(alphabet.get(ch, ch) for ch in employee.lower())
    base = re.sub(r"[^a-z0-9]+", ".", raw).strip(".")[:28] or "employee"
    with db.connect() as conn:
        used = {r[0] for r in conn.execute("SELECT username FROM accounts")}
    candidate, suffix = base, 1
    while candidate in used:
        suffix += 1
        candidate = base[:32 - len(str(suffix)) - 1] + "." + str(suffix)
    return candidate


def cmd_passwd(args: argparse.Namespace) -> int:
    password = args.password or _gen_password()
    result = accounts.set_password(args.username, password, by="cli")
    if not result.get("ok"):
        _out("ОТКАЗ: " + result.get("error", ""))
        return 1
    _out(f"Новый пароль {args.username} (сообщить один раз): {password}")
    return 0


def cmd_entered(args: argparse.Namespace) -> int:
    rows = accounts.entered_list(args.office or "")
    if not rows:
        _out("Никто ещё не вводил логин-пароль." if not args.office
             else f"В офисе «{args.office}» вводов нет.")
        return 0
    _out(f"{'ФИО':32} {'офис':10} тел/ПК         готов  статус")
    for r in rows:
        ph = "тел✓" if r["phone"] else "тел–"
        dk = "ПК✓" if r["desktop"] else "ПК–"
        _out(f"{r['employee']:32} {r['office'] or '—':10} {ph}/{dk}  "
             f"{'ДА' if r['ready'] else 'нет':5} {r['status']}")
        if r["phone"]:
            _out(f"    телефон: {r['phone']['label']} в {r['phone']['at']} "
                 f"({r['phone']['gps'] or 'без GPS'})")
    _out("\nГотовые (ввели и там, и там) переслать администратору; он: approve / approve-device.")
    _out("Руководителям/аналитику нужно ДВА подтверждения разных администраторов.")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    import crm_registration
    by=args.by or 'admin-cli'
    result = crm_registration.approve(args.username,by) or accounts.approve_account(args.username,by=by)
    if not result.get("ok"):
        _out("ОТКАЗ: " + result.get("error", ""))
        return 1
    if result.get("active"):
        _out(f"Аккаунт открыт. Подтверждения: {', '.join(result.get('approvals', []))}")
    else:
        _out(result.get("error", "Нужно ещё подтверждение администратора."))
    return 0


def cmd_registration_code(args: argparse.Namespace) -> int:
    import crm_registration
    try:
        _out(crm_registration.issue_employee_code(args.employee,hours=args.hours))
    except ValueError as exc:
        _out(str(exc))
        return 1
    return 0


def cmd_approve_device(args: argparse.Namespace) -> int:
    result = accounts.approve_device(args.id, by=args.by or "admin-cli")
    if not result.get("ok"):
        _out("ОТКАЗ: " + result.get("error", ""))
        return 1
    if result.get("active"):
        _out(f"Устройство подтверждено. Подтверждения: {', '.join(result.get('approvals', []))}")
    else:
        _out(result.get("error", "Нужно ещё подтверждение администратора."))
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    status = "disabled" if not args.enable else "awaiting"
    result = accounts.set_status(args.username, status, by="cli")
    _out(f"Статус: {status}" if result.get("ok") else "ОТКАЗ: " + result.get("error", ""))
    return 0 if result.get("ok") else 1


def cmd_approver(args: argparse.Namespace) -> int:
    result = accounts.register_approver(args.device_id, args.name)
    _out(f"Администратор назначен: {result['principal']}" if result.get("ok")
         else "ОТКАЗ: " + result.get("error", ""))
    return 0 if result.get("ok") else 1


def cmd_list(args: argparse.Namespace) -> int:
    rows = accounts.admin_accounts()
    current_office = None
    for r in rows:
        if args.by_office and r["office"] != current_office:
            current_office = r["office"]
            _out(f"\n== {(current_office or 'Без офиса')}")
        _out(f"{r['username']:20} {r['employee']:32} {r['status']:9} "
             f"{'смена пароля' if r['must_change_password'] else ''}")
        for d in r["devices"]:
            _out(f"    {d['kind']:8} {d['status']:8} {d['label'][:48]} "
                 f"{d['first_seen'] or ''}")
    if not rows:
        _out("Аккаунтов нет.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create")
    p.add_argument("--employee", required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--office", default="")
    p.add_argument("--role", default="agent",
                   help="agent|manager|security|full|analyst (руководителям/аналитику "
                        "достаточно одного подтверждения администратора)")
    p.add_argument("--telegram-id", type=int, default=None,
                   help="внутренний номер пользователя, если нужно задать явно "
                        "(иначе берётся из managers или создаётся служебный)")
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("bulk")
    p.add_argument("--csv", default="")
    p.set_defaults(func=cmd_bulk)

    p = sub.add_parser("passwd")
    p.add_argument("--username", required=True)
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_passwd)

    p = sub.add_parser("entered")
    p.add_argument("--office", default="")
    p.set_defaults(func=cmd_entered)

    p = sub.add_parser("approve")
    p.add_argument("--username", required=True)
    p.add_argument("--by", default="admin-cli",
                   help="имя администратора для двухподтверждений (по умолчанию admin-cli)")
    p.set_defaults(func=cmd_approve)

    p=sub.add_parser('registration-code',help='личный одноразовый код регистрации сотрудника')
    p.add_argument('--employee',required=True)
    p.add_argument('--hours',type=int,default=24)
    p.set_defaults(func=cmd_registration_code)

    p = sub.add_parser("approve-device")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--by", default="admin-cli")
    p.set_defaults(func=cmd_approve_device)

    p = sub.add_parser("disable")
    p.add_argument("--username", required=True)
    p.add_argument("--enable", action="store_true")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("list")
    p.add_argument("--by-office", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("approver", help="назначить администратора устройства (только SSH)")
    p.add_argument("--device-id", type=int, required=True)
    p.add_argument("--name", required=True, help="постоянный id человека; одинаковый на всех его устройствах")
    p.set_defaults(func=cmd_approver)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
