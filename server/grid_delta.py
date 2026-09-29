"""Перевоз грида реестра «разницей» — опыт 30.09.2026 (ветка exp/fast-exchange).

Смысл импорта не меняется ни на букву: сервер собирает из «базы» (грида,
который уже присылали) и разницы ТОТ ЖЕ грид, сверяет его SHA-256 с
присланным отпечатком и дальше идёт прежним путём — pick_grid, proof,
import_grid. Разница — только транспорт: вместо 45–57 МБ JSON (3–6 МБ
в gzip) по туннелю едут изменённые строки.

Формат: {"base": sha256 базы, "digest": sha256 нового грида, "rows": число
строк, "ops": [["=", n] | ["-", n] | ["+", [строки…]], …]}. Операции идут по
базе сверху вниз: «=» — взять n строк базы как есть, «-» — пропустить n строк
базы, «+» — вставить присланные строки. Вся база должна быть пройдена.
"""
from __future__ import annotations

import difflib
import hashlib
import json

MAX_ROWS = 400_000
MAX_OPS = 200_000
_SCALARS = (str, int, float, bool, type(None))


class DeltaError(ValueError):
    """Разница не собирается в заявленный грид — перевозить целиком."""


def canonical(grid) -> bytes:
    return json.dumps(grid, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def grid_digest(grid) -> str:
    return hashlib.sha256(canonical(grid)).hexdigest()


def _row_key(row) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def make_delta(base: list[list], new: list[list]) -> dict:
    """Разница base → new. Строки сравниваются целиком (как JSON)."""
    a = [_row_key(r) for r in base]
    b = [_row_key(r) for r in new]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops: list[list] = []

    def push(op, arg):
        if ops and ops[-1][0] == op and op in ("=", "-"):
            ops[-1][1] += arg
        elif ops and ops[-1][0] == op == "+":
            ops[-1][1].extend(arg)
        else:
            ops.append([op, arg])

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            push("=", i2 - i1)
        elif tag == "delete":
            push("-", i2 - i1)
        elif tag == "insert":
            push("+", [list(r) for r in new[j1:j2]])
        else:                                   # replace
            push("-", i2 - i1)
            push("+", [list(r) for r in new[j1:j2]])
    return {"base": grid_digest(base), "digest": grid_digest(new),
            "rows": len(new), "ops": ops}


def apply_delta(base: list[list], delta: dict) -> list[list]:
    """Собрать новый грид; всё, что не сходится, — DeltaError."""
    if not isinstance(delta, dict):
        raise DeltaError("разница должна быть объектом")
    ops = delta.get("ops")
    digest = delta.get("digest")
    rows = delta.get("rows")
    if not isinstance(ops, list) or len(ops) > MAX_OPS:
        raise DeltaError("ops должен быть списком разумной длины")
    if not isinstance(digest, str) or len(digest) != 64:
        raise DeltaError("digest должен быть SHA-256")
    if type(rows) is not int or not 0 <= rows <= MAX_ROWS:
        raise DeltaError("rows вне допустимого")
    if delta.get("base") != grid_digest(base):
        raise DeltaError("база не та")
    out: list[list] = []
    i = 0
    for op in ops:
        if not (isinstance(op, list) and len(op) == 2 and op[0] in ("=", "-", "+")):
            raise DeltaError("неизвестная операция")
        kind, arg = op
        if kind in ("=", "-"):
            if type(arg) is not int or arg < 0 or i + arg > len(base):
                raise DeltaError("выход за базу")
            if kind == "=":
                out.extend(base[i:i + arg])
            i += arg
        else:
            if not isinstance(arg, list):
                raise DeltaError("вставка должна быть списком строк")
            for row in arg:
                if not isinstance(row, list) or not all(isinstance(c, _SCALARS) for c in row):
                    raise DeltaError("строка вставки должна быть списком значений")
                out.append(row)
        if len(out) > MAX_ROWS:
            raise DeltaError("слишком много строк")
    if i != len(base):
        raise DeltaError("база пройдена не до конца")
    if len(out) != rows or grid_digest(out) != digest:
        raise DeltaError("собранный грид не совпал с отпечатком")
    return out
