import re, shutil, os
from pathlib import Path
import pandas as pd, xlwings as xw
import datetime

# ================= НАСТРОЙКИ =================
MONTHS_RU = {
    1: "ЯНВАРЬ", 2: "ФЕВРАЛЬ", 3: "МАРТ", 4: "АПРЕЛЬ",
    5: "МАЙ", 6: "ИЮНЬ", 7: "ИЮЛЬ", 8: "АВГУСТ",
    9: "СЕНТЯБРЬ", 10: "ОКТЯБРЬ", 11: "НОЯБРЬ", 12: "ДЕКАБРЬ"
}
current_month_name = MONTHS_RU[datetime.datetime.now().month]
SOURCE_FILE = f'U:/rbr/Развитие СНГ/ОБЩИЙ РЕЕСТР {current_month_name}.xlsb'
SHEET_NAME = 'Портфели'
ALLOWED_SHEETS = {'Портфели', 'Статусы', 'Статистика'}
HEADER_ROW_NAMES = 2
HEADER_ROW_TYPES = 3
DATA_START_ROW = 4
TARGET_COLUMN_NAME = "ответственный Сотрудник"

COUNTRY_DIRS = {
    "Страна_1": Path(r'U:\rbr\Развитие СНГ\Узбекистан\Портфели сотрудников УЗБ'),
    "Страна_2": Path(r'U:\rbr\Развитие СНГ\Таджикистан\Портфели отдела взыскания РТ'),
}


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', '_', str(name).strip())


def get_column_letter_excel(col_idx):
    """Возвращает буквенное имя столбца Excel по его индексу (1-based)"""
    result = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


# ================= ОСНОВНАЯ ЛОГИКА =================

if not Path(SOURCE_FILE).exists():
    raise FileNotFoundError(f"[!] ОШИБКА: Исходный файл реестра не найден по пути: {SOURCE_FILE}")

print("1. Сканируем диски и строим карту путей...")
employee_folder_map = {}
for country_name, country_path in COUNTRY_DIRS.items():
    if not country_path.exists():
        print(f"   [!] Предупреждение: Директория {country_name} по пути {country_path} сейчас недоступна. Пропускаем.")
        continue
    for sub_dir in country_path.iterdir():
        if sub_dir.is_dir():
            for emp_dir in sub_dir.iterdir():
                if emp_dir.is_dir():
                    emp_key = emp_dir.name.strip().lower()
                    if emp_key not in employee_folder_map:
                        employee_folder_map[emp_key] = []
                    employee_folder_map[emp_key].append(emp_dir)

print(f"   Успешно проиндексировано папок сотрудников на дисках: {len(employee_folder_map)}")

print("\n2. Быстро считываем структуру данных через calamine...")
df_all = pd.read_excel(SOURCE_FILE, sheet_name=SHEET_NAME, header=None, engine="calamine")

headers = [str(val).strip() if pd.notna(val) else "" for val in df_all.iloc[HEADER_ROW_NAMES - 1]]
if TARGET_COLUMN_NAME not in headers:
    raise ValueError(f"Заголовок '{TARGET_COLUMN_NAME}' не найден в строке {HEADER_ROW_NAMES}")

target_col_idx = headers.index(TARGET_COLUMN_NAME)

# Считываем строку 3 для точного определения типов колонок
col_types = [str(val).strip().lower() if pd.notna(val) else "" for val in df_all.iloc[HEADER_ROW_TYPES - 1]]

# Группируем индексы столбцов (переводим в 1-based для Excel)
hard_cols = [i + 1 for i, t in enumerate(col_types) if t == "хард" or t == "сх"]
soft_cols = [i + 1 for i, t in enumerate(col_types) if "софт" in t or t == "сх"]
all_cols_1based = list(range(1, len(col_types) + 1))

unique_employees = sorted([
    emp for emp in df_all.iloc[DATA_START_ROW - 1:, target_col_idx].unique()
    if emp and str(emp).strip() and str(emp).strip() != "nan" and str(emp).strip() != "None"
])
print(f"Найдено сотрудников в таблице: {len(unique_employees)}")

# Создаем временные эталонные заготовки в исходном формате .xlsb
base_dir_temp = Path(SOURCE_FILE).parent
temp_hard_template = base_dir_temp / "~temp_hard.xlsb"
temp_soft_template = base_dir_temp / "~temp_soft.xlsb"

print("\n3. Создаем два пустых отформатированных мастер-шаблона силами Excel...")
with xw.App(visible=False) as app:
    app.screen_updating = False
    app.calculation = 'manual'
    app.display_alerts = False

    # --- МАСТЕР-ШАБЛОН ДЛЯ ХАРДА ---
    shutil.copy2(SOURCE_FILE, temp_hard_template)
    wb = app.books.open(str(temp_hard_template))

    # Сначала удаляем лишние листы
    for sheet in list(wb.sheets):
        if sheet.name not in ALLOWED_SHEETS:
            sheet.delete()

    ws = wb.sheets[SHEET_NAME]
    cols_to_del_hard = [c for c in all_cols_1based if c not in hard_cols]
    for col_idx in sorted(cols_to_del_hard, reverse=True):
        ws.range(f"{get_column_letter_excel(col_idx)}:{get_column_letter_excel(col_idx)}").api.Delete()
    wb.save()
    wb.close()

    # --- МАСТЕР-ШАБЛОН ДЛЯ СОФТА ---
    shutil.copy2(SOURCE_FILE, temp_soft_template)
    wb = app.books.open(str(temp_soft_template))

    # Сначала удаляем лишние листы
    for sheet in list(wb.sheets):
        if sheet.name not in ALLOWED_SHEETS:
            sheet.delete()

    ws = wb.sheets[SHEET_NAME]
    cols_to_del_soft = [c for c in all_cols_1based if c not in soft_cols]
    for col_idx in sorted(cols_to_del_soft, reverse=True):
        ws.range(f"{get_column_letter_excel(col_idx)}:{get_column_letter_excel(col_idx)}").api.Delete()
    wb.save()
    wb.close()

# Список задач для финальной пакетной очистки строк в Excel
jobs_to_clean = []

print("\n4. Копируем файлы-заготовки .xlsb силами ОС...")
for employee in unique_employees:
    emp_key = str(employee).strip().lower()
    safe_name = sanitize_filename(employee)

    if emp_key not in employee_folder_map:
        continue

    first_folder = employee_folder_map[emp_key]
    is_soft = first_folder[0].parent.name.lower() == "soft"

    selected_template = temp_soft_template if is_soft else temp_hard_template

    # Копируем .xlsb напрямую во все целевые папки сотрудников
    for target_folder in employee_folder_map[emp_key]:
        output_xlsb = target_folder / f'{safe_name}.xlsb'

        # ЗАПОМИНАЕМ ПУТЬ СТРАРОГО ФАЙЛА (если он существует), чтобы перенести из него статистику
        old_file_path = None
        if output_xlsb.exists():
            # Чтобы не затереть файл до открытия, переименуем его во временный
            old_file_path = target_folder / f'~old_{safe_name}.xlsb'
            if old_file_path.exists():
                os.remove(old_file_path)
            os.rename(output_xlsb, old_file_path)

        shutil.copy2(selected_template, output_xlsb)
        # Передаем в список задач путь к созданному файлу, имя сотрудника и путь к его старому файлу (если есть)
        jobs_to_clean.append((output_xlsb, str(employee).strip(), old_file_path))

# ================= 5. МАССИВНАЯ ОЧИСТКА ДАННЫХ В EXCEL И ПЕРЕНОС СТАТИСТИКИ =================
if jobs_to_clean:
    print(f"\n5. Запуск Excel в фоновом режиме для обработки {len(jobs_to_clean)} файлов...")

    import time  # Импортируем для микропаузы при удалении занятых файлов

    with xw.App(visible=False) as app:
        app.screen_updating = False
        app.calculation = 'manual'
        app.display_alerts = False

        for file_path, emp_name, old_file_path in jobs_to_clean:
            print(f"Обработка и очистка данных: {file_path.name}")

            # --- ПЕРЕНОС ЛИСТА СТАТИСТИКА ИЗ СТАРОЙ ВЕРСИИ ---
            if old_file_path and old_file_path.exists():
                try:
                    wb_old = app.books.open(str(old_file_path))
                    if 'Статистика' in [s.name for s in wb_old.sheets]:
                        wb_new = app.books.open(str(file_path))

                        ws_old_stat = wb_old.sheets['Статистика']
                        ws_new_stat = wb_new.sheets['Статистика']

                        # Определяем заполненные границы старого листа статистики
                        last_row_stat = ws_old_stat.api.UsedRange.Rows.Count
                        last_col_stat = ws_old_stat.api.UsedRange.Columns.Count

                        if last_row_stat > 0 and last_col_stat > 0:
                            max_col_letter_stat = get_column_letter_excel(last_col_stat)
                            stat_range_address = f"A1:{max_col_letter_stat}{last_row_stat}"

                            # ИСПРАВЛЕНО: Прямое обращение к .api.FormulaLocal для сбора двумерного массива формул
                            old_formulas = ws_old_stat.range(stat_range_address).api.FormulaLocal

                            # Очищаем новый лист «Статистика» и заливаем туда формулы один в один через API
                            ws_new_stat.clear_contents()
                            ws_new_stat.range(stat_range_address).api.FormulaLocal = old_formulas

                            # ЖЕСТКАЯ КОРРЕКЦИЯ: Вырезаем имя старого файла из тела формул
                            old_name_clean = old_file_path.name
                            ws_new_stat.api.Cells.Replace(
                                What=f"[{old_name_clean}]",
                                Replacement="",
                                LookAt=2,  # xlPart
                                SearchOrder=1,
                                MatchCase=False
                            )

                            ws_new_stat.api.Cells.Replace(
                                What=f"{old_name_clean}!",
                                Replacement="",
                                LookAt=2,
                                SearchOrder=1,
                                MatchCase=False
                            )

                        wb_new.save()
                        wb_new.close()

                    # Принудительно закрываем старую книгу ДО попытки удаления файла с диска
                    wb_old.close()

                except Exception as e:
                    print(f"   [!] Не удалось скопировать Статистику для {emp_name}: {e}")
                    # В случае падения внутри блока гарантируем закрытие книги, если она была открыта
                    try:
                        wb_old.close()
                    except:
                        pass
                finally:
                    # Даем операционной системе 100 миллисекунд полностью освободить дескриптор файла
                    time.sleep(0.1)
                    if old_file_path.exists():
                        try:
                            os.remove(old_file_path)
                        except Exception as rm_err:
                            print(
                                f"   [!] Предупреждение: Не удалось удалить временный файл {old_file_path.name}: {rm_err}")

            # --- ОБЫЧНАЯ ОЧИСТКА СТРОК ПОРТФЕЛЯ ---
            wb = app.books.open(str(file_path))
            ws = wb.sheets[SHEET_NAME]

            # Определяем положение колонки сотрудника на сдвинутом листе
            new_target_col_letter = None
            current_headers = ws.range(f"{HEADER_ROW_NAMES}:{HEADER_ROW_NAMES}").value
            if TARGET_COLUMN_NAME in current_headers:
                new_target_idx = current_headers.index(TARGET_COLUMN_NAME) + 1
                new_target_col_letter = get_column_letter_excel(new_target_idx)

            first_empty_row = DATA_START_ROW
            if new_target_col_letter:
                last_row = ws.api.UsedRange.Rows.Count
                max_col = ws.api.UsedRange.Columns.Count
                max_col_letter = get_column_letter_excel(max_col)

                if last_row >= DATA_START_ROW:
                    # Читаем абсолютно ВСЕ строки данных листа в память Python за 1 миг
                    all_file_values = ws.range(f"A{DATA_START_ROW}:{max_col_letter}{last_row}").value
                    if not isinstance(all_file_values, list):
                        all_file_values = [[all_file_values]]

                    # Фильтруем массив в память Python
                    filtered_rows = [
                        row for row in all_file_values
                        if len(row) >= new_target_idx and str(row[new_target_idx - 1]).strip() == emp_name
                    ]

                    # Полностью очищаем старые строки данных одной командой
                    ws.range(f"A{DATA_START_ROW}:{max_col_letter}{last_row}").clear_contents()

                    # В один клик выгружаем отфильтрованные строки обратно на лист
                    if filtered_rows:
                        ws.range(f"A{DATA_START_ROW}").value = filtered_rows

                        # --- ЖЕСТКОЕ И СВЕРХБЫСТРОЕ ОТСЕЧЕНИЕ ЛИШНЕГО ХВОСТА СТРОК ---
                        first_empty_row = DATA_START_ROW + len(filtered_rows)

                        if last_row >= first_empty_row:
                            ws.range(f"{first_empty_row}:{last_row}").api.Delete()
                    else:
                        ws.range(f"{DATA_START_ROW}:{last_row}").api.Delete()
                        first_empty_row = DATA_START_ROW

            # Обновляем кэш ползунка прокрутки Excel
            _ = ws.api.UsedRange
            ws.range(f"{DATA_START_ROW}:{first_empty_row}").api.WrapText = False
            ws.range(f"{DATA_START_ROW}:{first_empty_row}").row_height = 15
            wb.save()
            wb.close()

# Уничтожаем временные мастер-шаблоны с диска
for t in [temp_hard_template, temp_soft_template]:
    if t.exists():
        os.remove(t)

print("\n[УСПЕХ] Скрипт полностью завершен!")