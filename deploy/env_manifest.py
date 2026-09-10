#!/usr/bin/env python3
"""Манифест переменных окружения: один источник, из которого всё порождается.

ЗАЧЕМ. Раньше было три разных правды: код читал переменные, `.env.example`
описывал часть из них, а README — ещё какую-то часть. Расхождение измерили:
код читал 42 переменные, `deploy/.env.example` описывал 18, а `OSRM_URL_RU`
жила ровно в одной строке `server/routing_client.py` и больше нигде. Инженер,
которому систему передают, узнать о ней не мог никак.

Теперь правда одна — файл `env_manifest.json` рядом. Из него:
  • порождаются `.env.example` каждой единицы (`--write`), их не пишут руками;
  • проверяется, что код не читает переменную, которой в манифесте нет
    (`--check`, тот же код гоняет tests/test_env_manifest.py).

Ищем чтение переменных в исходниках так (см. `scan_python`):
  1. `os.getenv("X")`, `os.environ["X"]`, `os.environ.get/pop("X")`;
  2. любой вызов, в имени которого есть `env` — `_int_env(env, "X", ...)`,
     `Settings.from_env`; берём первый строковый аргумент вида ИМЯ_ПЕРЕМЕННОЙ;
  3. `env.get("X")` / `environ.get("X")` — так читает служба речи, которой
     окружение передают словарём.
Правило 2 намеренно широкое: лучше поймать лишнее (и один раз вписать в
манифест), чем пропустить переменную, о которой инженер не узнает.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).resolve().parent / "env_manifest.json"

# Каталоги с кодом, который читает окружение. deploy/*.sh и compose-файлы
# разбираются отдельно (scan_compose): там переменные не читают, а подставляют.
# tests/ здесь не для полноты, а по делу: переключатели вида RUN_EXCEL_COM_TEST
# — такая же настройка, о которой человек должен узнать из одного места.
CODE_DIRS = ("server", "stt", "engine", "pult", "registry_cycle", "tools", "tests")

# Имя переменной окружения: заглавные, цифры, подчёркивания, не короче трёх.
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

# Единицы поставки, для которых порождается .env.example.
DEPLOY_UNITS = ("core", "stt", "gps")


# --- чтение манифеста ---------------------------------------------------------

def load(path: Path | str = MANIFEST) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def names(manifest: dict) -> set[str]:
    return {v["name"] for v in manifest["variables"]}


def keys(manifest: dict) -> list[tuple[str, str]]:
    """(единица, имя) — именно пара уникальна: COMPOSE_PROFILES есть и у ядра,
    и у маршрутов, и это разные настройки в разных .env."""
    return [(v.get("unit", ""), v["name"]) for v in manifest["variables"]]


def in_env_example(var: dict) -> bool:
    """По умолчанию переменная попадает в .env.example. false — тонкая настройка:
    она описана в манифесте, но файл оператора ею не засоряется."""
    return bool(var.get("in_env_example", True))


# --- поиск чтений в коде ------------------------------------------------------

def _str_const(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _reads_in_file(tree: ast.AST) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        found: list[str] = []

        if isinstance(node, ast.Subscript):
            # os.environ["X"]
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr == "environ":
                found = [_str_const(node.slice) or ""]

        elif isinstance(node, ast.Call):
            func = node.func
            callee = _callee_name(func)
            owner = getattr(getattr(func, "value", None), "id", "") or \
                getattr(getattr(func, "value", None), "attr", "")

            if (callee in ("getenv", "get", "pop", "setdefault")
                    and owner in ("os", "environ", "env")):
                # os.getenv("X"), os.environ.get("X"), env.get("X"),
                # os.environ.setdefault("X", …) — тоже настройка, просто со
                # значением, которое код подставляет себе сам.
                found = [_str_const(node.args[0]) or ""] if node.args else []
            elif "env" in callee.lower():
                # _int_env(env, "X", ...), _stt_env("STT_SOCKET", "PRAXIS_STT_SOCKET", …)
                # Берём ВСЕ похожие на имя аргументы: у пар «новое имя / старое имя»
                # второе — тоже рабочая переменная, и инженер должен о ней знать.
                found = [_str_const(a) or "" for a in node.args]

        for name in found:
            if name and ENV_NAME.match(name):
                out.append((name, node.lineno))
    return out


def scan_python(root: Path | str = ROOT, dirs: tuple[str, ...] = CODE_DIRS) -> dict[str, list[str]]:
    """{ИМЯ: ["server/web.py:80", ...]} — где код читает окружение."""
    root = Path(root)
    found: dict[str, list[str]] = {}
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            rel = path.relative_to(root).as_posix()
            for name, line in _reads_in_file(tree):
                found.setdefault(name, []).append(f"{rel}:{line}")
    return found


COMPOSE_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]{2,})[:\-}]")


def scan_compose(root: Path | str = ROOT) -> dict[str, list[str]]:
    """То же для compose-файлов единиц: `${APP_PORT:-8080}` — тоже переменная,
    которую инженер обязан найти в `.env.example`, хотя её не читает Python."""
    root = Path(root)
    found: dict[str, list[str]] = {}
    for unit in DEPLOY_UNITS:
        path = root / "deploy" / "units" / unit / "docker-compose.yml"
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in COMPOSE_VAR.findall(line):
                found.setdefault(name, []).append(f"{rel}:{n}")
    return found


def scan_all(root: Path | str = ROOT) -> dict[str, list[str]]:
    found = scan_python(root)
    for name, places in scan_compose(root).items():
        found.setdefault(name, []).extend(places)
    return found


def unknown_variables(manifest: dict, root: Path | str = ROOT) -> dict[str, list[str]]:
    """Переменные, которые код читает, а манифест не описывает."""
    known = names(manifest)
    return {n: places for n, places in scan_all(root).items() if n not in known}


# --- порождение .env.example --------------------------------------------------

def _wrap(text: str, width: int = 76) -> list[str]:
    """Комментарий в 76 колонок; пустая строка в описании остаётся пустой."""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("#")
            continue
        cur = ""
        for word in para.split():
            if cur and len(cur) + 1 + len(word) > width:
                lines.append(f"# {cur}")
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        if cur:
            lines.append(f"# {cur}")
    return lines


def render_env_example(manifest: dict, unit: str) -> str:
    """Текст `.env.example` единицы. Руками этот файл не пишут — правят манифест."""
    spec = manifest["units"][unit]
    out: list[str] = _wrap(spec["header"])
    variables = [v for v in manifest["variables"]
                 if v.get("unit") == unit and in_env_example(v)]

    known_sections = {s["id"] for s in spec["sections"]}
    orphans = [v["name"] for v in variables if v.get("section") not in known_sections]
    if orphans:
        raise ValueError(f"{unit}: раздел не описан у переменных {orphans}")

    for section in spec["sections"]:
        block = [v for v in variables if v.get("section") == section["id"]]
        if not block:
            continue
        out += ["", "", f"# ============ {section['title']} ============"]
        for var in block:
            out += [""] + _wrap(var["why"])
            out.append(f"{var['name']}={var.get('example', var.get('default', ''))}")

    return "\n".join(out) + "\n"


def env_example_names(manifest: dict, unit: str) -> list[str]:
    """Что манифест обязан породить в .env.example единицы — в порядке разделов."""
    return env_file_names(render_env_example(manifest, unit))


def env_file_names(text: str) -> list[str]:
    """Имена переменных из готового .env / .env.example (комментарии мимо)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            out.append(line.split("=", 1)[0].strip())
    return out


# --- командная строка ---------------------------------------------------------

def _cmd_check(args) -> int:
    manifest = load(args.manifest)
    unknown = unknown_variables(manifest, args.root)
    if unknown:
        print("Код читает переменные, которых нет в манифесте:")
        for name, places in sorted(unknown.items()):
            print(f"  {name}: {', '.join(places[:4])}")
        return 1
    print(f"Манифест описывает все {len(names(manifest))} переменных, "
          "расхождений с кодом нет.")
    return 0


def _cmd_write(args) -> int:
    manifest = load(args.manifest)
    for unit in DEPLOY_UNITS:
        target = Path(args.root) / "deploy" / "units" / unit / ".env.example"
        if not target.parent.is_dir():
            print(f"пропущено (нет каталога единицы): {target.parent}")
            continue
        target.write_text(render_env_example(manifest, unit), encoding="utf-8")
        print(f"порождён {target}")
    return 0


def _cmd_show(args) -> int:
    print(render_env_example(load(args.manifest), args.unit), end="")
    return 0


def _cmd_table(args) -> int:
    """Полный список — включая тонкие настройки, которых нет в .env.example."""
    manifest = load(args.manifest)
    for var in sorted(manifest["variables"], key=lambda v: (v.get("unit", ""), v["name"])):
        mark = "обязательна" if var.get("required") else f"по умолчанию: {var.get('default') or 'пусто'}"
        where = "" if in_env_example(var) else "  [нет в .env.example]"
        print(f"{var['name']:<38} {var.get('unit', ''):<8} {mark}{where}")
        print(f"    читает: {', '.join(var.get('readers', [])) or '—'}")
    print(f"\nвсего переменных: {len(manifest['variables'])}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", default=str(MANIFEST))
    p.add_argument("--root", default=str(ROOT))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="код не читает ничего сверх манифеста")
    sub.add_parser("write", help="переписать .env.example всех единиц")
    show = sub.add_parser("show", help="показать .env.example одной единицы")
    show.add_argument("unit", choices=DEPLOY_UNITS)
    sub.add_parser("table", help="весь список переменных с местами чтения")
    args = p.parse_args(argv)
    return {"check": _cmd_check, "write": _cmd_write,
            "show": _cmd_show, "table": _cmd_table}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
