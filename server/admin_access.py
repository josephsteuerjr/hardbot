"""Управление доступом пульта: коды привязки и устройства.

Команда намеренно НЕ имеет HTTP-входа. Выдать код может только тот, кто может
выполнить команду на самом сервере, — сегодня это тот, у кого SSH, завтра то же
самое сделает панель ИТ-инженера. В этом и смысл: право впустить новое
устройство привязано к доступу к серверу, а не к знанию строки из ``.env``.

    python admin_access.py issue --name "Ноут дома"
    python admin_access.py codes            # невыданные коды
    python admin_access.py devices [--all]  # устройства
    python admin_access.py revoke --id 3

Код печатается ОДИН раз: на сервере остаётся только его хэш, поэтому
«посмотреть код позже» нельзя — можно лишь выдать новый.

Внутри рабочего контура:

    cd /opt/hard/deploy/units/core
    docker compose exec -T app python /app/admin_access.py issue --name "Ноут дома"
"""

from __future__ import annotations

import argparse
import sys

import db


def _out(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text)


def cmd_issue(args: argparse.Namespace) -> int:
    made = db.issue_enroll_code(args.name or "")
    _out("")
    _out("Код привязки (показывается один раз):")
    _out("")
    _out("    " + made["code"])
    _out("")
    _out(f"Выдан {made['created_at']}" + (f" для «{args.name}»" if args.name else ""))
    _out("Передайте его тому, кто ставит пульт. В пульте: вкладка «Сервер» →")
    _out("«Привязать по коду». Код погаснет при первом же обмене.")
    return 0


def cmd_codes(args: argparse.Namespace) -> int:
    rows = db.list_enroll_codes(include_used=args.all)
    if not rows:
        _out("Невыданных кодов нет." if not args.all else "Кодов нет.")
        return 0
    for r in rows:
        state = f"обменян {r['used_at']} (устройство {r['used_by_device']})" if r["used_at"] else "ждёт обмена"
        _out(f"  #{r['id']:<4} {r['name'] or '(без имени)':<28} выдан {r['created_at']}  ·  {state}")
    return 0


def cmd_devices(args: argparse.Namespace) -> int:
    rows = db.list_admin_devices(include_revoked=args.all)
    if not rows:
        _out("Привязанных устройств нет. Выдайте код: admin_access.py issue --name ...")
        return 0
    for r in rows:
        state = f"ОТОЗВАНО {r['revoked_at']}" if r["revoked_at"] else f"на связи: {r['last_seen'] or 'ни разу'}"
        _out(f"  #{r['id']:<4} {r['name'] or '(без имени)':<28} привязано {r['created_at']}  ·  {state}")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    if db.revoke_admin_device(args.id):
        _out(f"Устройство #{args.id} отозвано: его токен больше не работает.")
        return 0
    _out(f"Устройство #{args.id} не найдено или уже отозвано.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Коды привязки и устройства пульта")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="выдать одноразовый код привязки")
    p_issue.add_argument("--name", default="", help="для кого: «Ноут дома»")
    p_issue.set_defaults(func=cmd_issue)

    p_codes = sub.add_parser("codes", help="показать коды")
    p_codes.add_argument("--all", action="store_true", help="включая обменянные")
    p_codes.set_defaults(func=cmd_codes)

    p_dev = sub.add_parser("devices", help="показать устройства")
    p_dev.add_argument("--all", action="store_true", help="включая отозванные")
    p_dev.set_defaults(func=cmd_devices)

    p_rev = sub.add_parser("revoke", help="отозвать устройство")
    p_rev.add_argument("--id", type=int, required=True)
    p_rev.set_defaults(func=cmd_revoke)

    args = parser.parse_args(argv)
    db.init_db()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
