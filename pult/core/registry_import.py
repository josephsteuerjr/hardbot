"""Импорт реестра: чтение книги, выбор сотрудников, подготовка proof, отчёт.

Всё здесь — чистые функции над словарями. Экран (`ui/registry.py`) и поток
предпросмотр → применение (`core/import_flow.py`) только вызывают их.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

import enrich

from .constants import IMPORT_SOURCE_PUBLISHED_MASTER, IMPORT_SOURCE_TEST_SUBSET
from .text import fmt_n


# --- сотрудники ------------------------------------------------------------
def emp_key(name) -> str:
    """Ответственный для группировки: схлопнутые пробелы, регистр не важен."""
    return " ".join(
        str(name or "").replace("ё", "е").replace("Ё", "Е").split()
    ).casefold()


def merge_import_employees(file_employees: list[dict],
                           db_employees: list[dict]) -> list[dict]:
    """Объединить сотрудников файла и БД без заранее заданных фамилий."""
    merged: dict[str, dict] = {}
    for item in file_employees or []:
        name = " ".join(str(item.get("name") or "").split())
        key = str(item.get("key") or emp_key(name))
        if not key:
            continue
        file_count = int(item.get("file_count", item.get("count", 0)) or 0)
        row = merged.setdefault(key, {
            "key": key, "name": name, "file_count": 0, "db_count": 0,
            "variants": 0,
        })
        row["file_count"] += file_count
        row["count"] = row["file_count"]
        row["variants"] = max(
            int(row.get("variants") or 0),
            int(item.get("variants") or 1),
        )
        if name:
            row["name"] = name
    for item in db_employees or []:
        name = " ".join(str(item.get("employee") or "").split())
        key = emp_key(name)
        if not key:
            continue
        row = merged.setdefault(key, {
            "key": key, "name": name, "file_count": 0, "count": 0,
            "db_count": 0, "variants": 0,
        })
        row["db_count"] += int(item.get("contracts") or 0)
        if not row.get("name"):
            row["name"] = name
    return sorted(
        merged.values(),
        key=lambda item: (
            not bool(item.get("file_count")),
            -int(item.get("file_count") or 0),
            emp_key(item.get("name")),
        ),
    )


def existing_import_selection(employees: list[dict]) -> set[str]:
    """Сотрудники, для которых есть и строки файла, и портфель в БД."""
    return {
        str(item.get("key") or "")
        for item in employees or []
        if item.get("key")
        and int(item.get("file_count", item.get("count", 0)) or 0) > 0
        and int(item.get("db_count") or 0) > 0
    }


def default_import_selection(employees: list[dict]) -> set[str]:
    """Обычный выбор — уже известные БД; в пустую БД грузим весь файл.

    Пустая БД — штатный первый импорт, поэтому оставлять оператора без единой
    галочки там неудобно. Если же портфели в БД уже есть, отсутствие
    пересечения показываем честно и ничего нового автоматически не выбираем.
    """
    existing = existing_import_selection(employees)
    database_is_populated = any(
        int(item.get("db_count") or 0) > 0 for item in employees or []
    )
    if database_is_populated:
        return existing
    return {
        str(item.get("key") or "")
        for item in employees or []
        if item.get("key")
        and int(item.get("file_count", item.get("count", 0)) or 0) > 0
    }


def employee_note(emp: dict) -> str:
    """Подпись к галочке сотрудника в списке импорта."""
    file_count = int(emp.get("file_count") or 0)
    db_count = int(emp.get("db_count") or 0)
    if file_count and db_count:
        state = "есть в файле и БД"
    elif file_count:
        state = "новый для БД"
    else:
        state = "только в БД"
    return (
        f"в файле: {fmt_n(file_count)} · сейчас в БД: {fmt_n(db_count)} · {state}"
        + (f" · написаний в файле: {emp['variants']}"
           if int(emp.get("variants") or 1) > 1 else "")
    )


def selected_rows(book: dict, selected: set[str]) -> int:
    return sum(int(e.get("file_count", e.get("count", 0)) or 0)
               for e in (book or {}).get("employees") or []
               if e["key"] in selected)


def preview_button_text(book: dict, selected: set[str], full: bool) -> str:
    mode = "полного среза" if full else "выбранных"
    return (f"Предпросмотр {mode} ({len(selected)} чел · "
            f"{selected_rows(book, selected)} дог.)")


# --- книга -------------------------------------------------------------------
def grid_layout(grid: list[list]) -> tuple[int, int, int, int]:
    """(строка заголовков, первая строка данных, колонка КД, колонка ФИО).

    Строка профилей («сх»/«хард»/«софт») под заголовком — часть формы книги,
    а не клиент. Распознаём её ТОЧНО ТАК ЖЕ, как сервер: только в контрактной
    позиции и только по тегу в колонке-ключе. Наивное «первая строка после
    шапки — уже данные» давало лишний договор с ответственным «сх», а сервер
    его пропускал — импорт валился на scope_mismatch, который винит реестр.
    """
    import exchange_contract as contract

    from . import header_map

    # Колонки ищем НЕ по буквальному заголовку, а по ключу поля: офис вправе
    # переименовать колонку, и с сентября 2026 так и сделал («ответственный
    # Сотрудник» → «…ХАРД»). Карта колонок и алиасы контракта отвечают на
    # вопрос «какая колонка файла и есть это поле» — здесь мы просто их
    # спрашиваем, теми же правилами, что применит сервер при импорте.
    mapping = header_map.title_to_key()
    key_title = contract.WORKBOOK_CONTRACT_TITLE
    who_title = contract.WORKBOOK_RESPONSIBLE_TITLE
    head = c_id = c_who = None
    for i, row in enumerate(grid[:contract.WORKBOOK_HEADER_SEARCH_ROWS]):
        keys = [mapping.get(enrich.norm_title(c)) for c in row]
        if contract.WORKBOOK_CONTRACT_KEY in keys:
            head = i
            c_id = keys.index(contract.WORKBOOK_CONTRACT_KEY)
            c_who = (keys.index(contract.WORKBOOK_RESPONSIBLE_KEY)
                     if contract.WORKBOOK_RESPONSIBLE_KEY in keys else None)
            break
    if head is None:
        raise ValueError(f"в книге нет колонки «{key_title}»")
    if c_who is None:
        raise ValueError(
            f"в книге нет колонки «{who_title}». Если офис её переименовал — "
            "свяжи новый заголовок с полем на экране «Колонки реестра»")
    first = head + 1
    type_row_index = head + contract.WORKBOOK_TYPE_ROW_OFFSET
    tags = {enrich.norm_title(t) for t in contract.WORKBOOK_TYPE_ROW_TAGS}
    if type_row_index < len(grid) and c_id < len(grid[type_row_index]) \
            and enrich.norm_title(grid[type_row_index][c_id]) in tags:
        first = type_row_index + 1
    return head, first, c_id, c_who


def scope_from_grid(grid: list[list], *, allow_empty: bool = False) -> list[dict[str, str]]:
    """[{contract_id, responsible}] из книги — ровно то, что разрешаем грузить.

    Пробелы и регистр не режем сами: контракт нормализует их одинаково с обеих
    сторон. Именно на этом спотыкался ручной scope — хвостовой пробел в
    «ответственный Сотрудник» валил импорт целиком с `scope_invalid`.
    """
    head, first, c_id, c_who = grid_layout(grid)
    scope, seen = [], set()
    for row in grid[first:]:
        if len(row) <= max(c_id, c_who):
            continue
        cid = enrich.norm_cid(row[c_id])
        if not cid or cid in seen:
            continue
        seen.add(cid)
        scope.append({"contract_id": cid,
                      "responsible": enrich.cell_text(row[c_who])})
    if not scope and not allow_empty:
        raise ValueError("в книге нет ни одной строки с номером КД")
    return scope


def grid_for(book: dict, picked: set[str], *, allow_empty: bool = False) -> list[list]:
    """Грид только из строк отмеченных сотрудников — шапка целиком, данные срезом.

    Резать надо именно ГРИД, а не один scope: сервер сверяет утверждённый
    список с тем, что реально разобрал из книги, и scope-подмножество при
    полном гриде честно отбивается как `scope_mismatch`.
    """
    if not picked:
        raise ValueError("не выбран ни один сотрудник")
    grid, c_who = book["grid"], book["c_who"]
    out = list(grid[:book["first"]])
    for row in grid[book["first"]:]:
        if len(row) > c_who and emp_key(row[c_who]) in picked:
            out.append(row)
    # Сравниваем с `first`, а не с `head`: в шапке ТРИ строки, и «len > head»
    # было истиной даже для книги без единой строки данных.
    if len(out) <= book["first"] and not allow_empty:
        raise ValueError("после отбора не осталось ни одной строки")
    return out


def read_registry(path: str) -> dict:
    """Прочитать книгу и разложить её по сотрудникам. Ничего не отправляет.

    Заодно выясняем, есть ли для этого файла publish-receipt registry_cycle.
    Receipt привязан к ТОЧНОМУ пути (`U:\\rbr\\…`) и SHA файла, поэтому копия
    мастера в «Загрузках» его не имеет — и это не поломка, а ровно то, что
    receipt и означает: «этот файл опубликован циклом вот отсюда».
    """
    import limited_test_import as lti

    stable = enrich.read_stable_workbook(path)
    grid = lti._validate_sheets(stable["sheets"])
    rows = scope_from_grid(grid)
    # Группируем по КАНОНИЧЕСКОМУ имени: в реестре одна и та же фамилия живёт
    # и «Егор», и «егор», и «Егор ». Бот их давно склеивает, а список галочек
    # показывал бы двух разных людей — и половина строк молча не уехала бы.
    # В подписи при этом показываем живое написание, а не служебный ключ.
    groups: dict[str, dict] = {}
    for item in rows:
        key = emp_key(item["responsible"])
        spelling = " ".join(str(item["responsible"] or "").split())
        g = groups.setdefault(key, {"key": key, "count": 0, "spellings": {}})
        g["count"] += 1
        g["spellings"][spelling] = g["spellings"].get(spelling, 0) + 1
    employees = []
    for g in groups.values():
        best = max(g["spellings"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        employees.append({
            "key": g["key"],
            "name": best or "(без ответственного)",
            "count": g["count"],
            "file_count": g["count"],
            "db_count": 0,
            "variants": len(g["spellings"]),
        })
    employees.sort(key=lambda e: (-e["count"], e["name"]))
    try:
        published = enrich.prepare_published_master_import(path)
        no_receipt = ""
    except Exception as exc:                           # noqa: BLE001
        published, no_receipt = None, str(exc)
    return {"path": path, "sheets": stable["sheets"], "grid": grid,
            "file_sha256": stable["file_sha256"], "scope": rows,
            "employees": employees,
            "published": published, "no_receipt": no_receipt}


# --- экран «Колонки реестра» -------------------------------------------------
def columns_caption(path: str, analysis: dict) -> str:
    """Подпись под кнопкой: файл, строка шапки, что нашлось."""
    if not analysis:
        return ""
    return (f"{Path(path).name} · шапка в строке {analysis['header_row']} · "
            f"распознано полей: {len(analysis['recognized'])} · "
            f"не читается колонок: {len(analysis['unknown'])} · "
            f"дополнительных: {len(analysis['extras'])}")


def columns_state_text(analysis: dict) -> str:
    """Главное сообщение экрана: пройдёт ли импорт и что решать человеку."""
    if not analysis:
        return ""
    lines: list[str] = []
    if not analysis.get("has_contract_key"):
        lines.append(
            "В книге не нашлась колонка-ключ «Номер КД / ID». Импорт "
            "невозможен, пока она не будет связана с полем ниже.")
    missing_required = analysis.get("missing_required") or []
    if missing_required:
        lines.append(
            "Не хватает обязательных колонок: "
            + ", ".join(item["title"] for item in missing_required)
            + ". Импорт заблокируется, пока они не найдены. Если офис их "
            "переименовал — выбери новый заголовок в таблице ниже.")
    unknown = analysis.get("unknown") or []
    if unknown:
        suggested = [item for item in unknown if item.get("suggest")]
        lines.append(
            f"Бот не читает {len(unknown)} колонок книги."
            + (f" Для {len(suggested)} из них подсказано поле — проверь и сохрани."
               if suggested else
               " Если какая-то из них нужна выезднику, включи её как "
               "дополнительное поле."))
    if not lines:
        lines.append("Все колонки книги разобраны: решать нечего.")
    return "\n".join(lines)


def source_kind_text(book: dict) -> str:
    return ("опубликованный master (есть расписка цикла)"
            if book.get("published") else
            "утверждённое подмножество: расписки registry_cycle для этого "
            "файла нет")


def file_caption(book: dict) -> str:
    return (f"{Path(book['path']).name} · строк {len(book['scope'])} · "
            f"сотрудников {len(book['employees'])}\n"
            f"Вид источника: {source_kind_text(book)}")


# --- подготовка запроса ------------------------------------------------------
def prepare_import(book: dict, picked: list[str], *, full_registry: bool = False,
                   force_test_subset: bool = False) -> tuple[dict, str | None]:
    """Книга + отмеченные сотрудники → (source_kind, proof, sheets) и путь proof.

    Весь файл целиком и с receipt'ом — это `published_master`, боевой путь.
    Как только выбраны НЕ все сотрудники, строки режутся, и файл грузится как
    утверждённое подмножество со своим scope. Флаг полного среза на эту нарезку
    не влияет: сервер использует его после проверки proof, чтобы снять только
    отсутствующие договоры сотрудников из exact scope.

    Второй элемент — куда положен proof, чтобы его можно было зарегистрировать
    руками, если HTTP-ручка регистратора на сервере выключена (для
    published_master proof не пишется — None).
    """
    import exchange_contract as contract
    import limited_test_import as lti

    by_key = {e["key"]: e for e in book["employees"]}
    unknown = sorted(set(picked) - set(by_key))
    if unknown:
        raise ValueError("неизвестные сотрудники в выборе: " + ", ".join(unknown))
    authoritative_employees = [by_key[key]["name"] for key in picked]
    file_emps = {
        e["key"] for e in book["employees"]
        if int(e.get("file_count", e.get("count", 0)) or 0) > 0
    }
    whole = set(picked) == file_emps

    if whole and book.get("published") and not force_test_subset:
        prepared = book["published"]
        grid = lti._validate_sheets(prepared["sheets"])
        digest = contract.validate_import_source_proof(
            prepared["source_proof"],
            source_kind=IMPORT_SOURCE_PUBLISHED_MASTER,
            grid=grid)["digest"]
        return ({"source_kind": IMPORT_SOURCE_PUBLISHED_MASTER,
                 "source_proof": prepared["source_proof"],
                 "sheets": prepared["sheets"], "proof_digest": digest,
                 "authoritative_employees": authoritative_employees}, None)

    grid = (grid_for(book, set(picked), allow_empty=full_registry)
            if not whole else book["grid"])
    scope = scope_from_grid(grid, allow_empty=full_registry)
    # Один и тот же файл законно импортируется повторно после изменения БД
    # (например, оператор снял портфель и явно решил вернуть актуальные строки).
    # Receipt одноразовый, поэтому каждая новая preview-сессия получает новый
    # approval id; preview и apply всё равно используют один exact proof/body.
    approval = (f"pult-{Path(book['path']).stem.strip()[:60]}"
                f"-{book['file_sha256'][:12]}-{len(scope)}"
                f"-{uuid.uuid4().hex[:12]}")
    proof = contract.build_test_subset_source_proof(
        grid, approval_id=approval, contracts=scope)
    digest = contract.validate_import_source_proof(
        proof, source_kind=IMPORT_SOURCE_TEST_SUBSET, grid=grid)["digest"]
    proof_path = None
    try:
        out = Path(tempfile.gettempdir()) / f"hardbot-proof-{digest[:12]}.json"
        out.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True),
                       encoding="utf-8")
        proof_path = str(out)
    except OSError:
        proof_path = None
    return ({"source_kind": IMPORT_SOURCE_TEST_SUBSET, "source_proof": proof,
             "sheets": [{"name": contract.WORKBOOK_SHEET_NAME, "grid": grid}],
             "proof_digest": digest,
             "authoritative_employees": authoritative_employees}, proof_path)


def preview_body(prepared: dict, *, full_registry: bool, restore_manual: bool,
                 replace_current_comments: bool,
                 import_historical_comments: bool) -> dict:
    return {
        "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
        "source_kind": prepared["source_kind"],
        "source_proof": prepared["source_proof"],
        "sheets": prepared["sheets"],
        "full_registry": full_registry,
        "authoritative_employees": (
            prepared["authoritative_employees"] if full_registry else []
        ),
        "restore_manual_unassigned": restore_manual,
        "replace_current_visit_comments": replace_current_comments,
        "import_historical_visit_comments": import_historical_comments,
    }


def expected_from_preview(d: dict, body: dict) -> dict | str:
    """expected_* для apply — или текст ошибки, если сервер не дал точных
    множеств. Пульт fail-closed по КАЖДОМУ эффекту, который сервер объявил:
    признак «в файле есть колонки отзыва» приходит из отчёта предпросмотра
    (recall_columns); старый сервер такого ключа не отдаёт — требование не
    включается, и новый пульт со старым сервером работает как раньше."""
    needs = (
        (bool(body.get("full_registry")), "auto_unassigned",
         "auto_unassigned_digest",
         "Сервер не вернул точное множество снятий"),
        (bool(body.get("restore_manual_unassigned")), "manual_restored",
         "manual_restore_digest",
         "Сервер не вернул точный набор возвратов"),
        (bool(body.get("replace_current_visit_comments")
              or body.get("import_historical_visit_comments")), None,
         "comment_effect_digest",
         "Сервер не вернул точный набор замен комментариев"),
        (bool(d.get("recall_columns")), "recalled", "recalled_digest",
         "Сервер не вернул точное множество отзывов"),
        (bool(d.get("recall_columns")), "recall_restored",
         "recall_restored_digest",
         "Сервер не вернул точный набор возвратов из отзыва"),
    )
    out: dict = {}
    for required, count_key, digest_key, message in needs:
        digest = d.get(digest_key)
        if required and (not isinstance(digest, str) or len(digest) != 64):
            return message + " — нужен новый сервер"
        out["expected_" + digest_key] = digest
        if count_key:
            out["expected_" + count_key] = int(d.get(count_key) or 0)
    return out


# --- отчёт -------------------------------------------------------------------
def import_pairs(d: dict) -> list[tuple[str, object]]:
    return [("строк", d.get("total_rows")), ("добавлено", d.get("inserted")),
            ("обновлено", d.get("updated")), ("без КД", d.get("skipped_no_id")),
            ("дублей", d.get("duplicates")),
            ("будет снято / снято", d.get("auto_unassigned")),
            ("ручных снятий в выбранных строках",
             d.get("manual_restore_candidates")),
            ("будет возвращено / возвращено", d.get("manual_restored")),
            ("текущих комментариев сохранено",
             d.get("current_comments_preserved")),
            ("текущих комментариев заменено",
             d.get("current_comments_replaced")),
            ("текущих комментариев очищено",
             d.get("current_comments_cleared")),
            ("историчных комментариев загружено",
             d.get("historical_comments_imported")),
            ("историчных комментариев очищено",
             d.get("historical_comments_cleared")),
            ("без ответственного", d.get("no_responsible")),
            ("офисных правок", d.get("office_cells")),
            ("call-полей изолировано", d.get("isolated_call_cells")),
            ("отозвано реестром (у сотрудников)", d.get("recalled_had_owner")),
            ("пришло уже отозванным", d.get("recalled_without_owner")),
            ("отзыв держится", d.get("recall_held")),
            ("возвращено из отзыва", d.get("recall_restored")),
            # None (старый сервер без счётчиков) KVGrid покажет как «—», а не 0
            ("отклонено защитой", d.get("protected_cells"))]


def import_notes(d: dict) -> list[str]:
    notes = []
    if "recall_columns" in d and not d.get("recall_columns"):
        notes.append(
            "В файле НЕТ колонок отзыва («Дата отзыва», «Причина отзыва», "
            "«Передача на отзыв», «Вывод из работы») — открепление по "
            "отзыву в этой книге не сработает."
        )
    # Поимённые разбивки защит: office_cells был нулевым всю жизнь и служил
    # сигналом; после контракта v3 в оба счётчика попадают поля другой
    # природы — голое число без имён полей прочтётся как авария.
    for label, key in (("Принято офисных правок", "office_by_key"),
                       ("Отклонено защитой", "protected_by_key")):
        per = d.get(key) or {}
        if per:
            titles = d.get("key_titles") or {}
            notes.append(label + ": " + ", ".join(
                f"{titles.get(name, name)} — {fmt_n(n)}"
                for name, n in per.items()) + ".")
    unparsed_promise = int(d.get("office_promise_unparsed") or 0)
    if unparsed_promise:
        notes.append(
            f"Офис написал в колонку обещания не-дату/не-число: "
            f"{fmt_n(unparsed_promise)} ячеек (значение бота сохранено).")
    recalled_owned = int(d.get("recalled_had_owner") or 0)
    if recalled_owned:
        by_emp = d.get("recalled_by_employee") or {}
        breakdown = ", ".join(f"{name}: {fmt_n(n)}"
                              for name, n in by_emp.items())
        notes.append(
            f"ОТЗЫВ: снимается с сотрудников {fmt_n(recalled_owned)}."
            + (f" По сотрудникам: {breakdown}." if breakdown else "")
            + f" Уже были сняты ранее: "
              f"{fmt_n(d.get('recalled_previously_unassigned'))}."
            + f" Пришло уже отозванным (ответственного не было): "
              f"{fmt_n(d.get('recalled_without_owner'))}."
            + (f" Дата отзыва в будущем: "
               f"{fmt_n(d.get('recalled_future_dated'))}."
               if int(d.get("recalled_future_dated") or 0) else "")
            + (f" Нераспознанная дата: "
               f"{fmt_n(d.get('recalled_unparsed_date'))}."
               if int(d.get("recalled_unparsed_date") or 0) else "")
        )
    unrec = int(d.get("recalled_unrecognized") or 0)
    if unrec:
        sample = d.get("recalled_unrecognized_sample") or []
        examples = "; ".join(
            f"{s.get('column')}=«{s.get('value')}» ({s.get('contract_id')})"
            for s in sample[:5])
        notes.append(
            f"Значений в колонках отзыва, не признанных отзывом: {fmt_n(unrec)}"
            + (f" (примеры: {examples})" if examples else "")
            + ". Если их много — офис пишет туда что-то, чего предикат не знает."
        )
    back_owner = int(d.get("recall_restored") or 0)
    back_none = int(d.get("recall_restored_without_owner") or 0)
    back_manual = int(d.get("recall_restored_to_manual") or 0)
    if back_owner or back_none or back_manual:
        parts = []
        if back_owner:
            parts.append(f"{fmt_n(back_owner)} вернутся ответственным из файла")
        if back_none:
            parts.append(f"{fmt_n(back_none)} вышли из отзыва БЕЗ "
                         "ответственного — в файле ФИО пусто; они не "
                         "попадут ни в чей портфель и исчезнут из карточки "
                         "«Отозванные», раздать их надо вручную")
        if back_manual:
            parts.append(f"{fmt_n(back_manual)} вернулись в РУЧНОЕ снятие "
                         "(их снимал оператор до отзыва)")
        notes.append("ВОЗВРАТ ИЗ ОТЗЫВА: " + "; ".join(parts) + ".")
    partial = int(d.get("recalled_partial_persons") or 0)
    if partial:
        notes.append(
            f"У {fmt_n(partial)} человек отозвана только часть договоров. "
            "Их карточка в приложении меняет номер (представителем "
            "становится другой КД): недосланный визит вернётся с "
            "«отклонено сервером», а фото выпадут из альбома руководителя. "
            "Разобрать руками."
        )
    if int(d.get("no_responsible") or 0) and (
            recalled_owned or int(d.get("recall_held") or 0)):
        notes.append(
            "Счётчик «без ответственного» включает отозванных "
            f"({fmt_n(recalled_owned)} + держится "
            f"{fmt_n(d.get('recall_held'))}) — это не авария."
        )
    manual_candidates = int(d.get("manual_restore_candidates") or 0)
    manual_restored = int(d.get("manual_restored") or 0)
    if manual_candidates:
        by_employee = d.get("manual_restore_by_employee") or {}
        breakdown = ", ".join(
            f"{name}: {fmt_n(count)}" for name, count in by_employee.items()
        )
        if d.get("restore_manual_unassigned"):
            notes.append(
                "ВОЗВРАТ РУЧНЫХ СНЯТИЙ: после применения снова будут "
                f"назначены {fmt_n(manual_restored)} договоров, присутствующих "
                "в выбранных строках с тем же ответственным."
                + (f" По сотрудникам: {breakdown}." if breakdown else "")
            )
        else:
            notes.append(
                f"Ручным снятием удержано: {fmt_n(manual_candidates)} договоров. "
                "Файл содержит того же ответственного, но обычный импорт его "
                "не вернёт. Для возврата включи отдельную галочку и сделай новый "
                "предпросмотр."
                + (f" По сотрудникам: {breakdown}." if breakdown else "")
            )
    if d.get("replace_current_visit_comments"):
        notes.append(
            "Режим начала месяца включён: текущие комментарии заменяются "
            "точно значениями файла; пустые ячейки очищают БД. "
            f"Заменено: {fmt_n(d.get('current_comments_replaced'))}, "
            f"очищено: {fmt_n(d.get('current_comments_cleared'))}, "
            f"без изменений: {fmt_n(d.get('current_comments_unchanged'))}."
        )
    else:
        notes.append(
            "Текущие комментарии существующих договоров защищены: "
            f"сохранено как есть {fmt_n(d.get('current_comments_preserved'))}."
        )
    if d.get("import_historical_visit_comments"):
        notes.append(
            "Разовый импорт историчных комментариев включён: "
            f"загружено {fmt_n(d.get('historical_comments_imported'))}, "
            f"очищено {fmt_n(d.get('historical_comments_cleared'))}, "
            f"без изменений {fmt_n(d.get('historical_comments_unchanged'))}."
        )
    else:
        notes.append(
            "Историчные комментарии этим импортом не трогаются: "
            f"сохранено {fmt_n(d.get('historical_comments_preserved'))}."
        )
    if d.get("idempotent_replay"):
        notes.append(
            "Идемпотентный повтор: это разрешение уже было использовано, "
            "сервер не сделал ни одной записи. Для нового состояния нужен "
            "новый опубликованный master или новый утверждённый test-subset."
        )
    held_manual = (
        manual_candidates if not d.get("restore_manual_unassigned") else 0
    )
    other_unassigned = max(int(d.get("no_responsible") or 0) - held_manual, 0)
    if other_unassigned:
        notes.append(
            f"Без ответственного вне ручных снятий: {other_unassigned} строк. "
            "Эти договоры в базе есть, но их не видит ни один выездник. "
            "Проверь пустые значения в колонке «ответственный Сотрудник»."
        )
    if d.get("office_cells"):
        notes.append(f"Принято офисных правок: {d['office_cells']} ячеек по "
                     f"{d.get('office_contracts') or 0} договорам — файл видел "
                     "обогащение (дата выезда совпала), отличия учтены как правки "
                     "офиса и попадут в карточки и обогащение.")
    if d.get("protected_cells"):
        notes.append(f"Отклонено защитой: {d['protected_cells']} ячеек по "
                     f"{d.get('protected_contracts') or 0} договорам — в файле нет "
                     "свежей даты выезда бота (устаревшая копия?), результат "
                     "выездника не затёрт.")
    unk = d.get("unknown_columns") or []
    if unk:
        notes.append(
            "Бот не читает эти колонки файла: " + ", ".join(unk)
            + ". Реши, что с ними делать, на экране «Колонки реестра»: связать "
            "с полем, включить как дополнительные или скрыть."
        )
    aliases = d.get("operator_aliases") or []
    if aliases:
        notes.append("Связано вручную (карта колонок): " + ", ".join(aliases))
    extra_columns = d.get("extra_columns") or []
    if extra_columns:
        notes.append(
            "Дополнительные колонки прочитаны: " + ", ".join(extra_columns)
            + f" · значений {fmt_n(d.get('extra_cells'))}."
        )
    ignored = d.get("ignored_columns") or []
    if ignored:
        notes.append(
            "Служебные колонки по текущему контракту: "
            + ", ".join(ignored)
        )
    return notes


def preview_headline(d: dict, full_registry: bool) -> str:
    """Первая заметка предпросмотра: полный срез или частичный импорт."""
    if not full_registry:
        return "ЧАСТИЧНЫЙ ИМПОРТ: отсутствующие в файле договоры сниматься не будут."
    by_employee = d.get("auto_unassigned_by_employee") or {}
    breakdown = ", ".join(f"{name}: {fmt_n(count)}"
                          for name, count in by_employee.items())
    return (
        "ПОЛНЫЙ СРЕЗ ВЫБРАННЫХ: у выбранных сотрудников после "
        "применения будет снято договоров: "
        f"{fmt_n(d.get('auto_unassigned'))}. Остальные сотрудники "
        "не затрагиваются."
        + (f"\nПо сотрудникам: {breakdown}." if breakdown else "")
    )


def apply_button_label(d: dict) -> str:
    """«Применить импорт · снять N · комментариев N · вернуть N · …»."""
    unassigned = int(d.get("auto_unassigned") or 0)
    comment_changes = sum(
        int(d.get(key) or 0)
        for key in ("current_comments_replaced", "current_comments_cleared",
                    "historical_comments_imported", "historical_comments_cleared")
    )
    label = "Применить импорт"
    if unassigned:
        label += f" · снять {fmt_n(unassigned)}"
    if comment_changes:
        label += f" · комментариев {fmt_n(comment_changes)}"
    restored = int(d.get("manual_restored") or 0)
    if restored:
        label += f" · вернуть {fmt_n(restored)}"
    recalled_owned = int(d.get("recalled_had_owner") or 0)
    if recalled_owned:
        label += f" · отозвать {fmt_n(recalled_owned)}"
    recall_back = int(d.get("recall_restored") or 0)
    if recall_back:
        label += f" · вернуть из отзыва {fmt_n(recall_back)}"
    return label


def apply_toast(d: dict) -> tuple[str, bool]:
    """Текст и признак ошибки тоста после применения импорта."""
    if d.get("idempotent_replay"):
        return "Этот receipt уже использован; повтор ничего не записал", True
    message = (f"Импорт готов: +{fmt_n(d.get('inserted'))} / "
               f"обновлено {fmt_n(d.get('updated'))}")
    if d.get("auto_unassigned"):
        message += f" / снято {fmt_n(d['auto_unassigned'])}"
    if d.get("manual_restored"):
        message += f" / возвращено {fmt_n(d['manual_restored'])}"
    return message, False


def registration_hint(d: dict, proof_path: str | None) -> str:
    """Подсказка, как зарегистрировать proof руками, если HTTP-ручка
    регистратора на сервере выключена."""
    return (
        "\n\nРазрешение не зарегистрировано — ручка регистратора на "
        "сервере закрыта. Либо открой её (IMPORT_REGISTRAR_HTTP=1 в "
        "/opt/hardbot2/.env), либо зарегистрируй вручную:\n"
        "  docker compose exec -T bot python import_authorization.py "
        f"register --source-kind {d.get('_source_kind') or ''} "
        "--proof /tmp/proof.json\n"
        f"proof лежит здесь: {proof_path or '—'}\n"
        f"digest: {d['_proof_digest']}\n"
        "После регистрации нажми «Применить импорт» ещё раз."
    )


def import_error_text(d: dict, proof_path: str | None, *,
                      registration_hint_wanted: bool = False) -> str:
    extra = ""
    if d.get("header_sample"):
        extra = ". В файле: " + ", ".join(d["header_sample"])
    if (registration_hint_wanted and d.get("_proof_digest")
            and not d.get("_registered")):
        extra += registration_hint(d, proof_path)
    return d["error"] + extra


# --- отозванные --------------------------------------------------------------
RECALLED_COLUMNS = (
    ("cid", "КД", 110), ("client", "Клиент", 220), ("emp", "Снят у", 180),
    ("date", "Дата отзыва", 100), ("reason", "Причина", 220), ("debt", "Долг", 90),
)


def recalled_query(limit: int, employee: str) -> str:
    from urllib.parse import quote

    q = f"/admin/recalled.json?limit={int(limit)}"
    if employee:
        q += f"&employee={quote(employee)}"
    return q


def recalled_pairs(d: dict) -> list[tuple[str, object]]:
    totals = d.get("totals") or {}
    shown = int(d.get("shown") or 0)
    total_recalled = int(totals.get("recalled") or 0)
    return [
        ("отозвано реестром", totals.get("recalled")),
        ("снято вручную", totals.get("manual")),
        ("снято отсутствием строки", totals.get("absent_from_registry")),
        # «1469» и «500» рядом без подписи читались бы как потеря
        ("показано", f"{fmt_n(shown)} из {fmt_n(total_recalled)}"),
    ]


def recalled_row(it: dict) -> tuple:
    return (it.get("contract_id", ""), it.get("client", ""),
            it.get("employee", ""), it.get("recall_date", ""),
            it.get("recall_reason") or it.get("recall_transfer")
            or it.get("recall_withdrawn") or "",
            fmt_n(it.get("debt") or 0))
