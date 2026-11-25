"""Reusable, side-effect-free cycle safeguards for the legacy launcher."""
import subprocess
import sys
from pathlib import Path

STAGES = ('Бэкап.py', 'Сборка.py', 'Откреп-закреп.py', 'Разборка.py', 'Очитка сотрудников без закрепов.py')


def run(directory, *, runner=subprocess.run, verify=lambda: None):
    directory = Path(directory)
    for name in STAGES:
        verify()
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(path)
        result = runner([sys.executable, str(path)])
        if result.returncode:
            print(f'Цикл остановлен: {name}, код {result.returncode}. Следующие этапы не запускались.')
            return result.returncode
    return 0


def assignment_dates(grid, cols, pinned, today, *, hard, soft, date_hard, date_soft):
    """Assignment is not a call/visit: preserve their results and comments."""
    for row, owners in sorted(pinned.items()):
        for owner, title in ((hard, date_hard), (soft, date_soft)):
            if owner in owners:
                grid.set_date(row, cols[title], today)


def text_columns(ws, headers, first_row, count):
    if not count:
        return
    for i, title in enumerate(headers, 1):
        name = str(title).strip().casefold()
        if name in ('номер кд / id', 'инн', 'снилс') or any(x in name for x in ('комментар', 'телефон', 'адрес', 'паспорт', 'доп.номер')):
            ws.range((first_row, i), (first_row + count - 1, i)).number_format = '@'
