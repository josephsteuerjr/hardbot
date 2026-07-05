import warnings
from pathlib import Path
import datetime
import pandas as pd
import xlwings as xw
import re

# Полностью глушим предупреждения
warnings.filterwarnings("ignore")

# Словарь для автоматического определения имени файла по текущему месяцу
MONTHS_RU = {
    1: "ЯНВАРЬ", 2: "ФЕВРАЛЬ", 3: "МАРТ", 4: "АПРЕЛЬ",
    5: "МАЙ", 6: "ИЮНЬ", 7: "ИЮЛЬ", 8: "АВГУСТ",
    9: "СЕНТЯБРЬ", 10: "ОКТЯБРЬ", 11: "НОЯБРЬ", 12: "ДЕКАБРЬ"
}
current_month_name = MONTHS_RU[datetime.datetime.now().month]

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
MAIN_REGISTRY_PATH = Path(
    fr"U:/rbr/Развитие СНГ/ОБЩИЙ РЕЕСТР {current_month_name}.xlsb")
TARGET_SHEET_NAME = "Портфели"  # Имя целевого листа во ВСЕХ файлах

# Укажите прямые пути к папкам, где лежат файлы сотрудников для сборки
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

# НАЗВАНИЯ СТОЛБЦОВ, КОТОРЫЕ ОБНОВЛЯЮТСЯ
TARGET_COLUMNS = {
    "ответственный Сотрудник",  # Обязательно для проверки условий
    "Сумма последнего платежа",
    "Дата последнего платежа",
    "Сумма прогноза",
    "Прогноз",
    "дата обещания",
    "Общий результат",
    "Доп. Адрес/Доп.Номер",
    "Дата выезда",
    "Комментарий текущего месяца выезда",
    "Дата встречи",
    "С кем был контакт?",
    "Договорились на оплату?",
    "Кто был на адресе?",
    "Местоположение клиента",
    "Цель приезда в РФ",
    "Cтатус звонка",
    "комментарий звонка",
    "статус звонка софт",
    "Дата перезвона",
    "Комментарий текущего месяца Софт",
}

HEADER_ROW = 2
DATA_START_ROW = 4
KEY_COLUMN = "Номер КД / ID"
RESPONSIBLE_COLUMN = "ответственный Сотрудник"

# Список колонок, которые не переносятся, если ответственные сотрудники не совпадают
RESTRICTED_COLUMNS = {"Общий результат", "Cтатус звонка", "статус звонка софт"}


def clean_id(raw_id):
    """Глубокая очистка ID от любых скрытых символов и мусора."""
    if raw_id is None:
        return ""

    # 1. Переводим в строку и принудительно заменяем неразрывные пробелы (\xa0) на обычные
    s = str(raw_id).replace('\xa0', ' ')

    # 2. Удаляем вообще все управляющие символы и пробелы по краям регулярным выражением
    s = re.sub(r'^[\s\u200b\u200c\u200d\ufeff]+|[\s\u200b\u200c\u200d\ufeff]+$', '', s)

    # 3. Убираем хвосты Excel вида .0, .00 и т.д.
    s = re.sub(r'\.0+$', '', s)

    # 4. Исправляем экспоненциальный формат Excel (например, 1.23E+05)
    if 'e+' in s.lower():
        try:
            s = str(int(float(s)))
        except:
            pass

    return s


def get_files_from_folders(folders_list):
    collected_files = []
    for folder in folders_list:
        if not folder.exists(): continue
        for file_path in folder.rglob("*.xlsb"):
            if not file_path.name.startswith("~$"):
                collected_files.append(file_path)
    return collected_files


def main():
    if not MAIN_REGISTRY_PATH.exists():
        print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Автоматически сформированный файл не найден!")
        print(f"Ожидался путь: {MAIN_REGISTRY_PATH}")
        return

    print(f"[1/3] Использование реестра: {MAIN_REGISTRY_PATH.name}")
    try:
        df_struct = pd.read_excel(MAIN_REGISTRY_PATH, sheet_name=TARGET_SHEET_NAME, header=None, nrows=HEADER_ROW,
                                  engine="calamine")
        headers_line = df_struct.iloc[HEADER_ROW - 1].fillna("").tolist()
    except Exception as e:
        print(f"[КРИТИЧЕСКАЯ ОШИБКА] Не удалось прочитать лист '{TARGET_SHEET_NAME}' в главном файле: {e}")
        return

    main_registry_headers = {str(h).strip() for h in headers_line if str(h).strip()}
    missing_cols = TARGET_COLUMNS - main_registry_headers
    if missing_cols:
        print(
            f"[ВНИМАНИЕ] На листе '{TARGET_SHEET_NAME}' главного реестра не найдены некоторые целевые колонки: {missing_cols}")

    emp_files = get_files_from_folders(SOURCE_DIRECTORIES)
    print(f"-> Найдено файлов сотрудников для обработки: {len(emp_files)}")

    updates_db = {}

    print(f"[2/3] Сбор данных из файлов сотрудников...")
    app = xw.App(visible=False)
    app.screen_updating = False
    app.display_alerts = False
    app.calculation = "manual"

    try:
        total_collected_rows = 0
        for file_path in emp_files:
            try:
                wb_emp = app.books.open(file_path, read_only=True)
                if TARGET_SHEET_NAME not in [sh.name for sh in wb_emp.sheets]:
                    wb_emp.close()
                    continue

                ws_emp = wb_emp.sheets[TARGET_SHEET_NAME]
                emp_last_col = ws_emp.range(HEADER_ROW, 1).end("right").column
                emp_last_row = ws_emp.range(DATA_START_ROW, 1).end("down").row
                if emp_last_row > 1048576 or emp_last_row < DATA_START_ROW:
                    emp_last_row = ws_emp.api.UsedRange.Rows.Count

                emp_headers = ws_emp.range((HEADER_ROW, 1), (HEADER_ROW, emp_last_col)).value
                emp_matrix = ws_emp.range((DATA_START_ROW, 1), (emp_last_row, emp_last_col)).value
                wb_emp.close()

                if not emp_matrix or not emp_headers: continue
                if not isinstance(emp_matrix, list): emp_matrix = [emp_matrix]
                if not isinstance(emp_headers, list): emp_headers = [emp_headers]

                emp_headers = [str(h).strip() for h in emp_headers if h is not None]
                if KEY_COLUMN not in emp_headers: continue
                key_idx = emp_headers.index(KEY_COLUMN)

                for row in emp_matrix:
                    if len(row) <= key_idx: continue
                    client_id = clean_id(row[key_idx])
                    if not client_id: continue

                    if client_id not in updates_db:
                        updates_db[client_id] = {}

                    for c_idx, col_name in enumerate(emp_headers):
                        if col_name == KEY_COLUMN or c_idx >= len(row): continue

                        if col_name in TARGET_COLUMNS:
                            val = row[c_idx]
                            if val is not None and str(val).strip() != "" and str(val).lower() != "nan":
                                updates_db[client_id][col_name] = val
                                total_collected_rows += 1

            except Exception as e:
                print(f"Ошибка чтения файла {file_path.name}: {e}")

        print(f"-> Успешно собрано в память {total_collected_rows} изменений для {len(updates_db)} уникальных ID.")

        if not updates_db:
            print("\n[ВНИМАНИЕ] Словарь обновлений пуст! Запись отменена.")
            app.quit()
            return

        print(f"[3/3] Открытие главного реестра XLSB и запись данных...")
        wb_main = app.books.open(MAIN_REGISTRY_PATH)
        ws_main = wb_main.sheets[TARGET_SHEET_NAME]

        last_col_idx = ws_main.range(HEADER_ROW, 1).end("right").column
        last_row = ws_main.range(DATA_START_ROW, 1).end("down").row
        if last_row > 1048576 or last_row < DATA_START_ROW:
            last_row = ws_main.api.UsedRange.Rows.Count

        main_headers = ws_main.range((HEADER_ROW, 1), (HEADER_ROW, last_col_idx)).value
        main_headers = [str(h).strip() for h in main_headers if h is not None]

        if KEY_COLUMN not in main_headers:
            print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Колонка '{KEY_COLUMN}' не найдена во 2-й строке!")
            wb_main.close()
            app.quit()
            return

        main_key_idx = main_headers.index(KEY_COLUMN)
        main_resp_idx = main_headers.index(RESPONSIBLE_COLUMN) if RESPONSIBLE_COLUMN in main_headers else None

        main_matrix = ws_main.range((DATA_START_ROW, 1), (last_row, last_col_idx)).value
        if not isinstance(main_matrix, list): main_matrix = [main_matrix]

        updated_cells_count = 0
        updated_rows_count = 0
        match_debug_count = 0
        restricted_skips_count = 0

        def to_clean_str(v):
            if v is None: return ""
            if isinstance(v, (datetime.date, datetime.datetime)): return v.strftime('%d.%m.%Y')
            if isinstance(v, datetime.time): return v.strftime('%H:%M:%S')
            s = str(v).strip()
            s = re.sub(r'\.0+$', '', s)
            return s

        print("-> Начинается точечная запись измененных строк в Excel...")

        for r_idx, row in enumerate(main_matrix):
            if len(row) <= main_key_idx: continue
            client_id = clean_id(row[main_key_idx])
            if not client_id: continue

            if client_id in updates_db:
                match_debug_count += 1
                row_updates = updates_db[client_id]

                # ПРОВЕРКА УСЛОВИЯ: Сравнение ответственных сотрудников
                is_same_responsible = True
                if main_resp_idx is not None and RESPONSIBLE_COLUMN in row_updates:
                    emp_responsible = clean_id(row_updates[RESPONSIBLE_COLUMN]).lower()
                    main_responsible = clean_id(row[main_resp_idx]).lower()

                    if emp_responsible != main_responsible:
                        is_same_responsible = False

                if match_debug_count <= 3:
                    print(f"   [ОТЛАДКА] ID {client_id}. Совпадение ответственных: {is_same_responsible}")

                row_changed = False

                for c_idx, col_name in enumerate(main_headers):
                    if col_name in row_updates:

                        # ИСКЛЮЧЕНИЕ: Колонку ответственного сотрудника только проверяем, но не перезаписываем
                        if col_name == RESPONSIBLE_COLUMN:
                            continue

                        # Если сотрудники разные и колонка в списке запрещенных — пропускаем ее обновление
                        if not is_same_responsible and col_name in RESTRICTED_COLUMNS:
                            restricted_skips_count += 1
                            continue

                        new_val = row_updates[col_name]
                        old_val = row[c_idx]

                        if to_clean_str(old_val) != to_clean_str(new_val):
                            if isinstance(new_val, (datetime.date, datetime.datetime)):
                                row[c_idx] = new_val.strftime('%d.%m.%Y')
                            elif isinstance(new_val, datetime.time):
                                row[c_idx] = new_val.strftime('%H:%M:%S')
                            else:
                                row[c_idx] = new_val

                            updated_cells_count += 1
                            row_changed = True

                if row_changed:
                    current_excel_row = DATA_START_ROW + r_idx
                    try:
                        ws_main.range((current_excel_row, 1), (current_excel_row, last_col_idx)).value = row
                        updated_rows_count += 1
                    except Exception as cell_err:
                        print(f"[ВНИМАНИЕ] Ошибка записи строки {current_excel_row} для ID {client_id}: {cell_err}")
                        raise cell_err

        print(f"-> Анализ завершен. Всего совпадений по ID: {match_debug_count}")
        print(f"-> Пропущено обновлений колонок из-за разных сотрудников: {restricted_skips_count}")

        print("-> Установка высоты строк данных на 15 пунктов...")
        ws_main.range(f"{DATA_START_ROW}:{last_row}").row_height = 15

        app.calculation = "automatic"
        wb_main.save()
        wb_main.close()

        print(f"\n✅ [УСПЕХ] Файл {MAIN_REGISTRY_PATH.name} успешно обновлен!")
        print(f"-> Физически изменено строк: {updated_rows_count}")
        print(f"-> Физически заменено ячеек: {updated_cells_count}")

    except Exception as e:
        print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] Ошибка автоматизации: {e}")
    finally:
        app.quit()

if __name__ == "__main__":
    main()
