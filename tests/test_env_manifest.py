"""Манифест переменных окружения — источник, из которого всё порождается.

Замер, с которого это началось: код читал 42 переменные, deploy/.env.example
описывал 18, а OSRM_URL_RU жила ровно в одной строке server/routing_client.py
и больше нигде. Инженеру, которому систему передают, узнать о ней было неоткуда:
маршруты по РФ молча считались по прямой.

Здесь проверяется ровно то, из-за чего такое расхождение возникает снова:
  1. код не начал читать переменную, которой в манифесте нет;
  2. сам манифест не разъехался (обязательная с готовым значением — не
     обязательная, раздел без описания — не раздел);
  3. .env.example единиц не разошёлся с манифестом — их не пишут руками,
     их порождают (deploy/env_manifest.py write);
  4. сам сканировщик находит все привычные способы прочитать окружение —
     иначе пункт 1 был бы проверкой, которая всегда зелёная.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_SPEC = importlib.util.spec_from_file_location(
    "hard_env_manifest", ROOT / "deploy" / "env_manifest.py")
assert _SPEC is not None and _SPEC.loader is not None
em = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(em)

MANIFEST = em.load()

# Единицы поставки + то, что ИТ-службе не ставят, но что живёт в репозитории.
KNOWN_UNITS = {"core", "stt", "gps", "engine", "pult", "система", "тесты"}


# --- 1. Код не читает ничего сверх манифеста ----------------------------------

def test_code_reads_nothing_beyond_the_manifest():
    unknown = em.unknown_variables(MANIFEST, ROOT)
    assert not unknown, (
        "Код читает переменные, которых нет в deploy/env_manifest.json:\n"
        + "\n".join(f"  {n}: {', '.join(places[:3])}"
                    for n, places in sorted(unknown.items()))
        + "\n\nДобавьте их в манифест (имя, единица, зачем, что по умолчанию) —"
          " иначе о настройке не узнает никто, кроме автора строки.")


# --- 2. Манифест не разъехался сам с собой ------------------------------------

def test_every_variable_is_described_in_full():
    for var in MANIFEST["variables"]:
        name = var["name"]
        assert var.get("unit") in KNOWN_UNITS, f"{name}: неизвестная единица"
        assert var.get("why", "").strip(), f"{name}: не сказано, зачем она"
        assert var.get("readers"), f"{name}: не сказано, кто её читает"


def test_a_required_variable_has_nothing_to_default_to():
    """«Обязательная» со значением по умолчанию — обман: установка пройдёт,
    а работать будет не то. Обязательная обязана быть пустой в .env.example."""
    for var in MANIFEST["variables"]:
        if var.get("required"):
            assert not var.get("default"), f"{var['name']}: обязательная, но с умолчанием"
            assert not var.get("example"), f"{var['name']}: обязательная, но уже заполнена"


def test_names_are_unique_within_a_unit():
    """Пара (единица, имя) уникальна, а не одно имя: COMPOSE_PROFILES есть и у
    ядра, и у маршрутов — это разные настройки в разных .env, и это нормально."""
    seen = em.keys(MANIFEST)
    assert len(seen) == len(set(seen)), "одна и та же переменная описана дважды"


@pytest.mark.parametrize("unit", em.DEPLOY_UNITS)
def test_every_variable_of_a_unit_knows_its_section(unit):
    """render_env_example падает на переменной без раздела — здесь это ловится
    тестом, а не при генерации у инженера."""
    text = em.render_env_example(MANIFEST, unit)
    assert text.startswith("#") and text.endswith("\n")
    assert em.env_file_names(text), f"{unit}: .env.example вышел пустым"


def test_a_variable_without_a_section_breaks_generation_loudly():
    broken = {
        "units": {"core": {"header": "тест", "sections": [{"id": "a", "title": "A"}]}},
        "variables": [{"name": "X_Y_Z", "unit": "core", "section": "нет такого",
                       "why": "тест", "readers": ["-"]}],
    }
    with pytest.raises(ValueError, match="X_Y_Z"):
        em.render_env_example(broken, "core")


# --- 3. .env.example порождается, а не пишется руками -------------------------

@pytest.mark.parametrize("unit", em.DEPLOY_UNITS)
def test_env_example_matches_the_manifest(unit):
    """Набор переменных в файле единицы обязан совпадать с манифестом.

    Сравниваются ИМЕНА, а не текст: комментарии в файле — дело генератора,
    а вот появившаяся руками строка или потерянная переменная — это ровно то
    расхождение, ради которого всё затевалось.
    """
    path = ROOT / "deploy" / "units" / unit / ".env.example"
    if not path.is_file():
        pytest.skip(f"единица {unit} ещё не разложена по каталогам: нет {path}")
    in_file = set(em.env_file_names(path.read_text(encoding="utf-8")))
    expected = set(em.env_example_names(MANIFEST, unit))
    assert in_file == expected, (
        f"{path} разошёлся с манифестом.\n"
        f"  только в файле:    {sorted(in_file - expected)}\n"
        f"  только в манифесте: {sorted(expected - in_file)}\n"
        "Файл порождают: python deploy/env_manifest.py write")


def test_generated_file_survives_reading_back():
    """Порождённый текст должен читаться как .env: имена ровно те, что задуманы."""
    for unit in em.DEPLOY_UNITS:
        text = em.render_env_example(MANIFEST, unit)
        assert em.env_file_names(text) == em.env_example_names(MANIFEST, unit)


# --- 4. Сканировщик действительно ищет -----------------------------------------

_ALL_THE_WAYS = '''
import os

A_PLAIN = os.getenv("VAR_GETENV")
B_ITEM = os.environ["VAR_ITEM"]
C_GET = os.environ.get("VAR_ENVIRON_GET")
os.environ.setdefault("VAR_SETDEFAULT", "1")


def _int_env(env, name, default):
    return env.get(name) or default


D = _int_env(os.environ, "VAR_VIA_HELPER", 0)


def _pair_env(new, old, default, environ=None):
    env = os.environ if environ is None else environ
    return env.get(new) or env.get(old) or default


E = _pair_env("VAR_NEW_NAME", "VAR_OLD_NAME", "")


def from_env(env):
    return env.get("VAR_MAPPING_GET")
'''


def test_scanner_finds_every_way_the_code_reads_env(tmp_path):
    """Гарантия для проверки №1: сканировщик знает все способы, которыми в этом
    коде читают окружение. Особенно пары «новое имя / старое имя» — забытое
    старое имя и есть та переменная, о которой потом никто не знает."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "sample.py").write_text(_ALL_THE_WAYS, encoding="utf-8")
    found = em.scan_python(tmp_path, dirs=("server",))
    assert set(found) == {
        "VAR_GETENV", "VAR_ITEM", "VAR_ENVIRON_GET", "VAR_SETDEFAULT",
        "VAR_VIA_HELPER", "VAR_NEW_NAME", "VAR_OLD_NAME", "VAR_MAPPING_GET",
    }
    assert found["VAR_GETENV"][0].startswith("server/sample.py:")


def test_scanner_reads_compose_interpolation(tmp_path):
    """В compose-файлах переменные не читают, а подставляют — но для инженера
    это такая же настройка, и её тоже надо найти в .env.example."""
    unit = tmp_path / "deploy" / "units" / "gps"
    unit.mkdir(parents=True)
    (unit / "docker-compose.yml").write_text(
        'services:\n  vroom:\n    ports: ["${VROOM_PORT:-3000}:3000"]\n'
        '    command: ["--graph", "${OSRM_GRAPH_UZ}"]\n', encoding="utf-8")
    found = em.scan_compose(tmp_path)
    assert set(found) == {"VROOM_PORT", "OSRM_GRAPH_UZ"}


def test_a_new_variable_makes_the_guard_fail(tmp_path):
    """Собственно то, ради чего всё: появилась строка os.getenv — проверка
    краснеет, пока переменную не описали."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "new.py").write_text(
        'import os\nX = os.getenv("SOME_BRAND_NEW_KNOB")\n', encoding="utf-8")
    unknown = em.unknown_variables(MANIFEST, tmp_path)
    assert "SOME_BRAND_NEW_KNOB" in unknown
