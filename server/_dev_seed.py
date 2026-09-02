"""
Засеять локальную дев-БД реальным реестром (ПД остаётся на хосте).

Запускать на ХОСТЕ (не в контейнере) — пишет в ./data_dev/hardbot.db, который
дев-compose монтирует как /data. Реальный xlsx в образ не попадает.

    python _dev_seed.py                         # дефолтный файл (Кейсы на обогащение)
    python _dev_seed.py "C:\\путь\\Общий.xlsx"  # любой другой реестр
"""
import os
import sys
from pathlib import Path

DEFAULT = r"C:\Users\yegor\Downloads\portfolio_tool\Худжанд\Кейсы на обогащение из Общего.xlsx"

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    data_dir = Path(__file__).parent / "data_dev"
    data_dir.mkdir(exist_ok=True)
    os.environ["APP_DATA"] = str(data_dir)

    import db
    db.init_db()
    rep = db.import_xlsx(
        src,
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    print(f"источник: {src}")
    print(f"БД: {data_dir / 'hardbot.db'}")
    print(f"импорт: строк {rep['total_rows']}, добавлено {rep['inserted']}, "
          f"обновлено {rep['updated']}, без КД {rep['skipped_no_id']}")
