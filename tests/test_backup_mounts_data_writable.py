"""Копия базы должна сниматься и при остановленном приложении.

Ловушка. База живёт в режиме WAL. Чтобы её открыть, SQLite создаёт рядом
служебный файл «hardbot.db-shm»; когда приложение работает, -shm уже есть, а
когда оно остановлено — его рядом нет. На томе, смонтированном «:ro», создать
-shm нельзя, и открытие падает с «unable to open database file». То есть с
«:ro» копия не снималась ровно в том случае, ради которого одноразовый
контейнер и написан: приложение лежит, копия нужнее всего.

Урок уже записан в migrate.sh («том монтируется НА ЗАПИСЬ, хотя мы только
читаем») — этот тест сторожит, чтобы «:ro» не вернулось ни туда, ни в
backup.sh: читаем мы всё равно только через mode=ro в самом соединении.
"""

import re
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "deploy" / "units" / "core"

# «-v …/data:/data:ro» в любом виде: с кавычками, с переменной, с $PWD.
RO_DATA_MOUNT = re.compile(r"-v\s+\"?[^\"\s]*[/:]data:/data:ro")


def _scripts():
    return sorted(CORE.glob("*.sh"))


def test_core_scripts_exist():
    assert _scripts(), "в deploy/units/core не найдено ни одного .sh — проверять нечего"


def test_data_volume_never_mounted_read_only():
    bad = []
    for path in _scripts():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]  # пояснения в комментариях разрешены
            if RO_DATA_MOUNT.search(code):
                bad.append(f"{path.name}:{number}: {line.strip()}")
    assert not bad, (
        "том с данными смонтирован «:ro» — при остановленном приложении SQLite\n"
        "не сможет создать -shm и копия не снимется:\n" + "\n".join(bad)
    )


def test_backup_takes_snapshot_without_running_app():
    text = (CORE / "backup.sh").read_text(encoding="utf-8")
    assert "--no-deps" in text and "compose run" in text, (
        "backup.sh больше не снимает копию одноразовым контейнером — при "
        "остановленном приложении копию будет нечем снять"
    )
    assert "mode=ro" in text, (
        "соединение с базой должно оставаться mode=ro: на запись монтируется том, "
        "а не открывается база"
    )


def test_backup_does_not_build_host_paths_by_hand():
    """Второй урок, дороже первого: путь в аргументах докера ломает Git Bash.

    MSYS переписывает пути в аргументах команды, и «-v путь:/data» приезжало в
    докер как «C:/Program Files/Git/data»: контейнер монтировал пустоту, а
    sqlite отвечал «unable to open database file» — то есть ровно так же, как
    при «:ro», и отличить одно от другого по сообщению нельзя. Том берём из
    compose, чтобы путей в командной строке не было вовсе.
    """
    text = (CORE / "backup.sh").read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        assert not re.search(r"docker\s+run[^|]*-v\s", code), (
            f"backup.sh:{number} снова собирает «-v путь:цель» руками: {line.strip()}"
        )


def test_failed_backup_leaves_no_empty_copy():
    """Оборвавшаяся копия не должна оставлять каталог с файлом на 0 байт."""
    text = (CORE / "backup.sh").read_text(encoding="utf-8")
    assert ".hardbot.db.part" in text, (
        "копия пишется прямо в hardbot.db: при обрыве в backups/<время>/ останется "
        "файл на 0 байт, и каталог будет выглядеть как снятая копия"
    )
    assert 'mv "$tmp_db" "$dest/hardbot.db"' in text, (
        "проверенный временный файл должен переименовываться в hardbot.db"
    )
