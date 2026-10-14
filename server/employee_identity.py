"""Совместимый реестр стабильных ID и явная проверка строк имён.

Клиентские очереди продолжают владеть uid. Импорт не переименовывает аккаунты,
не выбирает одного из однофамильцев и не переписывает исторических авторов.
"""
import uuid

import db


def init_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS employee_identities(id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS employee_aliases(name TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employee_identities(id), added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")


def ensure(conn, names):
    for name in sorted(set(names)):
        if not name or conn.execute("SELECT 1 FROM employee_aliases WHERE name=?", (name,)).fetchone():
            continue
        matches = {r["employee_id"] for r in conn.execute("SELECT name,employee_id FROM employee_aliases") if db._name_key(r["name"]) == db._name_key(name)}
        if len(matches) > 1:
            raise ValueError("Неоднозначная идентичность сотрудника")
        identifier = next(iter(matches)) if matches else uuid.uuid4().hex
        if not matches:
            conn.execute("INSERT INTO employee_identities(id,display_name) VALUES(?,?)", (identifier, name))
        conn.execute("INSERT INTO employee_aliases(name,employee_id) VALUES(?,?)", (name, identifier))


def conflicts(conn, names):
    known = {}
    for row in conn.execute("SELECT name,employee_id FROM employee_aliases"):
        known.setdefault(db._name_key(row["name"]), set()).add(row["employee_id"])
    result = []
    for name in sorted(set(names)):
        if len(known.get(db._name_key(name), ())) > 1:
            result.append(name)
    return result


def migration_report(conn):
    names = {r[0] for r in conn.execute("SELECT responsible FROM clients UNION SELECT responsible_soft FROM clients UNION SELECT employee FROM agent_bind") if r[0]}
    groups = {}
    for name in names:
        groups.setdefault(db._name_key(name), set()).add(name)
    return {"names": len(names), "collisions": [sorted(g) for g in groups.values() if len(g) > 1],
            "policy": "Не объединять автоматически. Разные люди с одинаковым ФИО требуют различных отображаемых имён до миграции всех клиентов на ID."}


def add_alias(identifier, name):
    name = str(name or "").strip()
    if not name:
        raise ValueError("name")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM employee_identities WHERE id=?", (identifier,)).fetchone():
            raise ValueError("employee_id")
        existing = conn.execute("SELECT employee_id FROM employee_aliases WHERE name=?", (name,)).fetchone()
        if existing and existing[0] != identifier:
            raise ValueError("Имя уже принадлежит другому сотруднику")
        conn.execute("INSERT OR IGNORE INTO employee_aliases(name,employee_id) VALUES(?,?)", (name, identifier))
        conn.execute("UPDATE employee_identities SET display_name=? WHERE id=?", (name, identifier))
        conn.commit()
