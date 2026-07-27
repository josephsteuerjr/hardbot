import warnings
from pathlib import Path
import xlwings as xw

# Глушим предупреждения pandas
warnings.filterwarnings("ignore")

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
TARGET_SHEET_NAME = "Портфели"
HEADER_ROW = 2
DATA_START_ROW = 4

SOURCE_DIRECTORIES = [
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Soft"),
    Path(r"U:\rbr\Развитие СНГ\Таджикистан\Портфели отдела взыскания РТ\Soft"),
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Андижан"),
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Самарканд"),
    Path(r"U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ\Ташкент"),
    Path(
        r"U:\rbr\Развитие СНГ\Таджикистан\Портфели отдела взыскания РТ\Душанбе"),
    Path(
        r"U:\rbr\Развитие СНГ\Таджикистан\Портфели отдела взыскания РТ\Худжанд")
]


REPORT_SOFT_PATH = Path(r"C:\Users\roman.komarov\Desktop\Скрипт сборки и разборки\ПРОВЕРКА_SOFT.xlsx")
REPORT_HARD_PATH = Path(r"C:\Users\roman.komarov\Desktop\Скрипт сборки и разборки\ПРОВЕРКА_HARD.xlsx")


def get_files_from_folders(folders_list):
    collected_files = []
    for folder in folders_list:
        if not folder.exists():
            continue
        for file_path in folder.rglob("*.xlsb"):
            if not file_path.name.startswith("~$"):
                collected_files.append(file_path)
    return collected_files


def save_report_file(app, path, headers, data_rows):
    """Вспомогательная функция для чистой записи файла отчета"""
    if not data_rows:
        return

    wb_report = app.books.add()
    ws_report = wb_report.sheets[0]
    ws_report.name = "Свод реестров"

    if headers:
        ws_report.range("A1").value = headers

    ws_report.range("A2").value = data_rows

    last_row = len(data_rows) + 1
    ws_report.range(f"1:{last_row}").api.WrapText = False
    ws_report.range(f"1:{last_row}").row_height = 15

    if path.exists():
        try:
            path.unlink()
        except:
            pass

    wb_report.save(str(path))
    wb_report.close()


def main():
    print("=== СТАРТ РАЗДЕЛЬНОГО СБОРА ДАННЫХ (SOFT И HARD) ===")

    emp_files = get_files_from_folders(SOURCE_DIRECTORIES)
    print(f"-> Найдено файлов сотрудников: {len(emp_files)}")

    if not emp_files:
        print("[ВНИМАНИЕ] Файлы для сбора не найдены.")
        return True

    soft_data = []
    hard_data = []

    soft_headers = None
    hard_headers = None

    # Запуск Excel в фоновом режиме
    app = xw.App(visible=False)
    app.screen_updating = False
    app.display_alerts = False
    app.calculation = "manual"

    print("-> Чтение файлов через Excel...")
    try:
        for file_path in emp_files:
            try:
                # Определяем тип файла на основе папки
                if "soft" in str(file_path).lower():
                    expected_cols = 45
                    is_soft = True
                else:
                    expected_cols = 51
                    is_soft = False

                wb_emp = app.books.open(file_path, read_only=True)

                if TARGET_SHEET_NAME not in [sh.name for sh in wb_emp.sheets]:
                    wb_emp.close()
                    continue

                ws_emp = wb_emp.sheets[TARGET_SHEET_NAME]

                # Ищем границы таблицы
                emp_last_col = ws_emp.range(HEADER_ROW, 1).end("right").column
                emp_last_row = ws_emp.range(DATA_START_ROW, 1).end("down").row

                if emp_last_row > 1048576 or emp_last_row < DATA_START_ROW:
                    emp_last_row = ws_emp.api.UsedRange.Rows.Count

                # Фиксируем шапку для соответствующего типа
                if is_soft and soft_headers is None:
                    soft_headers = ws_emp.range((HEADER_ROW, 1), (HEADER_ROW, min(emp_last_col, 44))).value
                elif not is_soft and hard_headers is None:
                    hard_headers = ws_emp.range((HEADER_ROW, 1), (HEADER_ROW, min(emp_last_col, 50))).value

                # Проверка количества колонок
                if emp_last_col != expected_cols:
                    f_type = "Soft" if is_soft else "Hard"
                    print(f"   [ВНИМАНИЕ] Ошибка структуры {f_type} в файле {file_path.name}! "
                          f"(Найдено столбцов: {emp_last_col}, Ожидалось: {expected_cols})")

                # Считываем матрицу данных
                emp_matrix = ws_emp.range((DATA_START_ROW, 1), (emp_last_row, emp_last_col)).value
                wb_emp.close()

                if not emp_matrix:
                    continue
                if not isinstance(emp_matrix, list):
                    emp_matrix = [emp_matrix]

                # Нормализуем строки строго под лимиты своего типа
                for row in emp_matrix:
                    if not isinstance(row, list):
                        row = [row]

                    cur_len = len(row)
                    if cur_len < expected_cols:
                        row = row + [None for _ in range(expected_cols - cur_len)]
                    elif cur_len > expected_cols:
                        row = row[:expected_cols]

                    if is_soft:
                        soft_data.append(row)
                    else:
                        hard_data.append(row)

                print(f"   [ОК] Прочитан файл: {file_path.name}")

            except Exception as file_err:
                print(f"   [ОШИБКА] Не удалось прочитать файл {file_path.name}: {file_err}")

        # Шаг 2: Запись файлов результатов
        print("\n-> Сохранение раздельных файлов проверки...")

        if soft_data:
            save_report_file(app, REPORT_SOFT_PATH, soft_headers, soft_data)
            print(f"✅ Создан файл SOFT: {REPORT_SOFT_PATH.name} (Строк: {len(soft_data)})")
        else:
            print("⚠ Данные для реестра SOFT не найдены.")

        if hard_data:
            save_report_file(app, REPORT_HARD_PATH, hard_headers, hard_data)
            print(f"✅ Создан файл HARD: {REPORT_HARD_PATH.name} (Строк: {len(hard_data)})")
        else:
            print("⚠ Данные для реестра HARD не найдены.")

    except Exception as main_err:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА автоматизации Excel: {main_err}")
        return False
    finally:
        app.quit()

    print("=== ПРЕДПРОВЕРКА ЗАВЕРШЕНА УСПЕШНО ===\n")
    return True


if __name__ == "__main__":
    main()