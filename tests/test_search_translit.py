"""Поиск находит клиента, записанного латиницей, при наборе по-русски.

ФИО должников в узбекских и таджикских портфелях лежат латиницей
(«TUKHTASINOV NUSRATULLO»), а ищут их так, как произносят, — «Тухтасинов».
Пока сравнивались сырые строки, поиск молчал при живом клиенте в портфеле, и
руководитель в поле насчитал трёх «пропавших» подряд за десять минут.

Проверяем НАСТОЯЩИЙ код оболочки: вырезаем fold/personMatches из index.html и
выполняем их в node. Переписывать те же правила на Python бессмысленно —
сошлась бы копия, а не то, что уедет на телефон.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "server" / "miniapp" / "index.html"

# (что набрали, как записано в реестре, должно ли найтись)
PAIRS = [
    ("Тухтасинов", "TUKHTASINOV NUSRATULLO", True),
    ("Тухтасинов", "TUXTASINOV NUSRATULLO", True),      # x вместо kh — та же буква
    ("Абдухамидов", "ABDUKHAMIDOV ALISHER", True),
    ("Жураев", "ZHURAEV AKHMADALI", True),              # ж = j или zh
    ("Мирзаев", "MIRZAYEV BOBUR", True),                # -аев = -ayev
    ("Қодиров", "KODIROV UMID", True),                  # қ = q или k
    ("Тожиддинов", "TOJIDDINOV SARDOR", True),          # удвоение не мешает
    ("Юсупов", "YUSUPOV JASUR", True),
    ("Ғаниев", "GANIEV OTABEK", True),
    ("TUKHTASINOV", "TUKHTASINOV NUSRATULLO", True),    # латиницей тоже
    ("Тухтасинов", "ISMOILOV HOJIAKBAR", False),        # чужой не всплывает
    ("Иванов", "ABDUKHAMIDOV ALISHER", False),
]

HARNESS = """
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("const FOLD_MAP=");
const end = src.indexOf("function filterList(");
if (start < 0 || end < 0) { console.log(JSON.stringify({error: "не нашёл fold/personMatches"})); process.exit(0); }
eval(src.slice(start, end));
const out = JSON.parse(process.argv[3]).map(([q, name, want]) => {
  const p = {client: name, ids: ["48260984"], tels: ["79688992824"]};
  return personMatches(p, q.toLowerCase(), q.replace(/\\D+/g, ""), fold(q)) === want;
});
const p2 = {client: "ABDUKHAMIDOV", ids: ["48260984"], tels: ["79688992824"]};
console.log(JSON.stringify({
  pairs: out,
  by_contract: personMatches(p2, "48260984", "48260984", fold("48260984")),
  by_phone: personMatches(p2, "8992824", "8992824", fold("8992824")),
  stranger: personMatches(p2, "zzz", "", fold("zzz")),
}));
"""


def _run_shell_search():
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка кода оболочки пропущена")
    harness = ROOT / "tests" / "_translit_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    try:
        done = subprocess.run(
            [node, str(harness), str(INDEX), json.dumps(PAIRS, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    finally:
        harness.unlink(missing_ok=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def search():
    return _run_shell_search()


def test_cyrillic_query_finds_latin_name(search):
    assert "error" not in search, search.get("error")
    missed = [PAIRS[i][:2] for i, ok in enumerate(search["pairs"]) if not ok]
    assert not missed, f"не сошлись пары: {missed}"


def test_contract_and_phone_search_survived_translit(search):
    """Скелет имени не должен отменять поиск по номеру и телефону."""
    assert search["by_contract"] is True
    assert search["by_phone"] is True


def test_unrelated_query_matches_nobody(search):
    assert search["stranger"] is False


def test_fold_is_computed_once_per_person():
    """760 имён на каждую набранную букву — заметная пауза в поле."""
    body = INDEX.read_text(encoding="utf-8")
    assert "p._fold||(p._fold=fold(p.client))" in body
