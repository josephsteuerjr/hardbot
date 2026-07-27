import datetime
import shutil
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")


now = datetime.datetime.now()

FOLDER_NAME = f"{now.strftime('%d.%m')}"

BACKUP_BASE_DIR = Path(r"C:\Users\roman.komarov\Desktop\Скрипт сборки и разборки\Архив реестров")
TARGET_BACKUP_DIR = BACKUP_BASE_DIR / FOLDER_NAME

SOURCE_DIRECTORIES = [
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Soft"),
    Path(r"U:\rbr\Развитие СНГ\Таджикистан\Портфели отдела взыскания РТ\Soft"),
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Андижан"),
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Самарканд"),
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Ташкент"),
    Path(r"U:\rbr\Развитие СНГ\Таджикистан\Портфели отдела взыскания РТ\Душанбе"),
    Path(r"U:\rbr\Развитие СНГ\Таджикистан\Портфели отдела взыскания РТ\Худжанд")
]


def main():
    print(f"=== СТАРТ РЕЗЕРВНОГО КОПИРОВАНИЯ ФАЙЛОВ ===")
    print(f"-> Целевая папка: {TARGET_BACKUP_DIR}")

    copied_count = 0

    for src_dir in SOURCE_DIRECTORIES:
        if not src_dir.exists():
            print(f"⚠ Папка не найдена, пропускаем: {src_dir.name}")
            continue

        print(f"-> Сканирование папки: {src_dir.name}...")

        # Определяем страну на основе пути к исходной папке
        if "Узбекистан" in src_dir.parts:
            country_folder = "Узбекистан"
        elif "Таджикистан" in src_dir.parts:
            country_folder = "Таджикистан"
        else:
            country_folder = "Другие"

        # Формируем путь к подпапке страны и создаем её, если её еще нет
        country_backup_dir = TARGET_BACKUP_DIR / country_folder
        country_backup_dir.mkdir(parents=True, exist_ok=True)

        for file_path in src_dir.rglob("*.xlsb"):
            if file_path.name.startswith("~$"):
                continue

            try:
                # Копируем файл в подпапку соответствующей страны
                dest_file_path = country_backup_dir / file_path.name

                shutil.copy2(file_path, dest_file_path)
                copied_count += 1

            except Exception as e:
                print(f"   [ОШИБКА] Не удалось скопировать {file_path.name}: {e}")

    print(f"✅ Бэкап завершен успешно. Файлы разложены по странам. Всего: {copied_count}\n")
    return True


if __name__ == "__main__":
    main()
