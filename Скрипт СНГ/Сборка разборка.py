import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
BACKUP_SCRIPT = SCRIPT_DIR / "Бэкап.py"
PRE_CHECK_SCRIPT = SCRIPT_DIR / "Предпроверка.py"
SBORKA_SCRIPT = SCRIPT_DIR / "Сборка.py"
RAZBORKA_SCRIPT = SCRIPT_DIR / "Разборка.py"


def run_script(script_path):
    """Запускает python-скрипт и возвращает True, если он завершился успешно."""
    print(f"\n>>> Запуск скрипта: {script_path.name}...")
    result = subprocess.run([sys.executable, str(script_path)])

    if result.returncode == 0:
        print(f">>>> {script_path.name} успешно завершен.")
        return True
    else:
        print(f"❌ ОШИБКА: Скрипт {script_path.name} завершился с кодом ошибки {result.returncode}.")
        return False


def main():
    print("=== СТАРТ ОБЩЕЙ АВТОМАТИЗАЦИИ ===")

    # Шаг 0: Резервное копирование исходных файлов
    if not run_script(BACKUP_SCRIPT):
        print("\n⛔ КРИТИЧЕСКИЙ СБОЙ: Не удалось сделать бэкап файлов. Процесс остановлен.")
        return

    # Шаг 1: Запуск предпроверки
    if not run_script(PRE_CHECK_SCRIPT):
        print("\n⛔ КРИТИЧЕСКИЙ СБОЙ: Сбор данных для предпроверки не удался.")
        return

    # Шаг 2: Запуск сборки
    if not run_script(SBORKA_SCRIPT):
        print("\n⛔ КРИТИЧЕСКИЙ СБОЙ: Сборка не удалась. Разборка отменена.")
        return

    # Шаг 3: Запуск разборки
    if not run_script(RAZBORKA_SCRIPT):
        print("\n⛔ ВНИМАНИЕ: Разборка завершилась с ошибкой.")
        return

    print("\n🎉 [УСПЕХ] Все этапы автоматизации последовательно выполнены!")


if __name__ == "__main__":
    main()
