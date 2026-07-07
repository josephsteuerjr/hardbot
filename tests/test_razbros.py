"""razbros.py — чистая логика без Excel/pandas: конфиг республик, разрешение корней
по маске, карта папок сотрудников (2 уровня, !Архив/_tech мимо), отчёт-превью.

Раскладка (apply) требует живого Excel/xlwings — здесь не тестируется.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import razbros  # noqa: E402


# --- конфиг республик -------------------------------------------------------
def test_config_new_format(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"republics": [
        {"republic": "Таджикистан", "subpath": "Портфели отдела взыскания РТ"},
        {"republic": "Узбекистан", "subpath": "Портфели сотрудников УЗБ"},
    ]}), encoding="utf-8")
    reps = razbros.load_config(str(p))
    assert [r["republic"] for r in reps] == ["Таджикистан", "Узбекистан"]
    assert reps[0]["subpath"] == "Портфели отдела взыскания РТ"


def test_config_legacy_with_comments(tmp_path):
    """Легаси-формат republicRoots + // комментарии + \\ в пути — не должно падать."""
    p = tmp_path / "cfg.json"
    p.write_text(
        '{\n "republicRoots": [\n'
        '   {"republic":"Таджикистан","path":"U:/rbr/Развитие СНГ/Таджикистан/Портфели отдела взыскания РТ"}, // коммент\n'
        '   {"republic":"Узбекистан","path":"U:/rbr/Развитие СНГ/Узбекистан/Портфели сотрудников УЗБ"},\n'
        ' ]\n}\n', encoding="utf-8")
    reps = razbros.load_config(str(p))
    assert [r["republic"] for r in reps] == ["Таджикистан", "Узбекистан"]
    # subpath = хвост пути после названия республики
    assert reps[0]["subpath"] == "Портфели отдела взыскания РТ"
    assert reps[1]["subpath"] == "Портфели сотрудников УЗБ"


def test_config_strip_jsonc():
    raw = '{\n // строка-коммент\n "republics": [ {"republic":"РТ","subpath":"П"}, ] /* хвост */ }'
    data = json.loads(razbros._strip_jsonc(raw))
    assert data["republics"][0]["republic"] == "РТ"


# --- дерево-фикстура по маске ----------------------------------------------
def _make_tree(base: Path):
    reps = [
        ("Таджикистан", "Портфели отдела взыскания РТ",
         {"Душанбе": ["Петров Пётр"], "Soft": ["Иванов Иван"],
          "!Архив": ["Уволенный Ух"], "_tech": ["служебка"]}),
        ("Узбекистан", "Портфели сотрудников УЗБ",
         {"Ташкент": ["Сидоров Сидор"], "!Архив": ["Другой Уволенный"]}),
    ]
    for republic, subpath, cities in reps:
        for city, emps in cities.items():
            for emp in emps:
                d = base / republic / subpath / city / emp
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{emp}.xlsb").write_text("x", encoding="utf-8")
    return base


def _reps():
    return [
        {"republic": "Таджикистан", "subpath": "Портфели отдела взыскания РТ"},
        {"republic": "Узбекистан", "subpath": "Портфели сотрудников УЗБ"},
    ]


def test_resolve_roots_from_base(tmp_path):
    _make_tree(tmp_path)
    roots = razbros.resolve_roots(str(tmp_path), _reps(), [])
    got = {name for name, _p in roots}
    assert got == {"Таджикистан", "Узбекистан"}
    assert all(p.exists() for _n, p in roots)


def test_resolve_roots_base_is_portfolio_root(tmp_path):
    """Если под базой нет республик, но она сама — корень портфелей, берём её напрямую."""
    portfolio = tmp_path / "Портфели отдела взыскания РТ" / "Душанбе" / "Петров Пётр"
    portfolio.mkdir(parents=True)
    base = tmp_path / "Портфели отдела взыскания РТ"
    roots = razbros.resolve_roots(str(base), _reps(), [])
    assert len(roots) == 1 and roots[0][1] == base


def test_build_folder_map_mask_and_skips(tmp_path):
    _make_tree(tmp_path)
    roots = razbros.resolve_roots(str(tmp_path), _reps(), [])
    fmap = razbros.build_folder_map(roots)
    assert set(fmap) == {"петров пётр", "иванов иван", "сидоров сидор"}
    # !Архив/_tech не просочились
    assert not any(k.startswith("!") or "увол" in k or "служеб" in k for k in fmap)
    # метаданные: республика/город/kind
    iv = fmap["иванов иван"][0]
    assert iv["kind"] == "soft" and iv["city"] == "Soft"
    assert fmap["сидоров сидор"][0]["republic"] == "Узбекистан"


def test_make_report_matched_unmatched_extra(tmp_path):
    _make_tree(tmp_path)
    roots = razbros.resolve_roots(str(tmp_path), _reps(), [])
    fmap = razbros.build_folder_map(roots)
    employees = ["Петров Пётр", "Иванов Иван", "Нет Такого"]
    rep = razbros.make_report(fmap, employees)
    assert rep["counts"]["matched"] == 2
    assert rep["unmatched"] == ["Нет Такого"]
    # Сидоров есть на диске, но его нет в источнике → лишняя папка
    assert "Сидоров Сидор" in rep["extra_folders"]
    names = {m["employee"] for m in rep["matched"]}
    assert names == {"Петров Пётр", "Иванов Иван"}
