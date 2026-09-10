"""Проверка кода ответа в скриптах установки: никакого «|| echo 000».

Ловушка повторялась дважды. При отказе curl сам печатает «000» — так устроен
-w '%{http_code}'. Дописанный сверху «|| echo 000» срабатывает, когда curl
ОТДАЛ код и при этом завершился ненулевым (обрыв тела, --max-time, ошибка
записи в /dev/null): подстановка склеивает две строки, получается «200000»,
сравнение с «200» не проходит — и доктор объявляет живое приложение мёртвым.

Правильная форма: «... )" || true» и подстановка «${code:-000}» отдельной
строкой. Тест сторожит все скрипты установки сразу, чтобы починка в одной
единице снова не забыла про другую.
"""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def _shell_scripts():
    return sorted(DEPLOY.rglob("*.sh"))


def test_deploy_scripts_exist():
    assert _shell_scripts(), "в deploy/ не найдено ни одного .sh — проверять нечего"


def test_no_echo_000_after_curl():
    bad = []
    for path in _shell_scripts():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]  # пояснения в комментариях разрешены
            if "echo 000" in code:
                bad.append(f"{path.relative_to(DEPLOY.parent)}:{number}: {line.strip()}")
    assert not bad, "код ответа склеится с дописанным «000»:\n" + "\n".join(bad)
