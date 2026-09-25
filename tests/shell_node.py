"""Запускатель настоящей оболочки под node для tests/test_shell_*.py.

Сценарий — JS-код (тело async-функции), который tests/shell_harness.js выполняет
рядом с оболочкой из server/miniapp/index.html: головной скрипт манифеста и
основной скрипт в одном vm-контексте, браузер — минимальные заглушки. Возвращает
то, что сценарий сложил в `out`. Переписывать логику оболочки на Python
бессмысленно — сошлась бы копия, а не то, что уедет на телефон (как в
test_search_translit). Нет node — тест пропускается.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "server" / "miniapp" / "index.html"
HARNESS = Path(__file__).resolve().parent / "shell_harness.js"


def run_shell(scenario: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("нет node — проверка кода оболочки пропущена")
    done = subprocess.run(
        [node, str(HARNESS), str(INDEX)], input=scenario,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert done.returncode == 0, done.stderr
    # Только по \n: splitlines() режет и по U+2028, а JSON оставляет его как есть.
    lines = [line for line in done.stdout.split("\n") if line.strip()]
    assert lines, done.stderr
    out = json.loads(lines[-1])
    assert "error" not in out, out["error"]
    # Необработанный отказ промиса в браузере — строка в консоли, а здесь — повод
    # посмотреть: так вылезают ошибки в самой оболочке.
    assert "__problems" not in out, out["__problems"]
    return out
