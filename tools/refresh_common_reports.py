"""Refresh the two existing linked Excel reports and their values edition."""
import argparse
import hashlib
import json
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from shutil import copyfile
from tempfile import mkdtemp

from python_calamine import CalamineWorkbook
from pywintypes import com_error
import xlwings as xw


GENERAL = "Статистика общего реестра.xlsb"
KA = "Статистика по КА общего реестра.xlsb"
VALUES = "Статистика общего реестра — знач.xlsb"
LAYOUT = {GENERAL: ("Статистика",), KA: ("Статистика", "Стат. сокр.")}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def log(folder, event, **detail):
    row = dict(time=datetime.now().astimezone().isoformat(), event=event, **detail)
    with (folder / "report-journal.jsonl").open("a", encoding="utf8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def links(book):
    result = book.api.LinkSources(1) or ()
    return (result,) if isinstance(result, str) else result


def cached(path, tabs):
    with CalamineWorkbook.from_path(str(path)) as workbook:
        return {
            tab: {
                (r, c): v
                for r, row in enumerate(workbook.get_sheet_by_name(tab).to_python(skip_empty_area=False))
                for c, v in enumerate(row)
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
            for tab in tabs
        }


def refresh(master, reports, journal, *, live):
    mode = "live" if live else "test"
    folder = Path(mkdtemp(prefix=f"common-reports-{mode}-", dir=journal))
    paths = {name: reports / name for name in (GENERAL, KA, VALUES)}
    initial = {name: digest(path) for name, path in paths.items()}
    master_hash = digest(master)
    backup = folder / "before"
    backup.mkdir()
    for name, path in paths.items():
        copyfile(path, backup / name)
        assert digest(backup / name) == initial[name]
    log(folder, "intent", mode=mode, backup=str(backup), source_sha256=master_hash, before=initial)
    work = paths if live else {name: folder / name for name in paths}
    source = master if live else folder / master.name
    if not live:
        copyfile(master, source)
        for name, path in work.items():
            copyfile(backup / name, path)
    values_stage = folder / "values-stage"
    values_stage.mkdir()
    values_file = values_stage / "values-candidate.xlsb"
    assert digest(source) == master_hash
    with xw.App(visible=False, add_book=True) as app:
        app.display_alerts = False
        app.screen_updating = False
        app.api.EnableEvents = False
        app.api.AutomationSecurity = 3
        app.api.AskToUpdateLinks = False
        app.calculation = "manual"
        app.api.CalculateBeforeSave = False
        opened = []

        def open_book(path, read_only=False):
            book = app.books.open(str(path), update_links=False, read_only=read_only,
                ignore_read_only_recommended=True, add_to_mru=False, notify=False)
            opened.append(book)
            if not read_only and book.api.ReadOnly:
                raise RuntimeError(f"Excel открыл для чтения: {path.name}")
            return book

        try:
            source_book = open_book(source, read_only=True)
            report_books = {name: open_book(work[name]) for name in (GENERAL, KA)}
            log(folder, "opened", mode=mode)
            for name, book in report_books.items():
                expected = {source.name.casefold(): source_book.fullname}
                if name == KA:
                    expected[GENERAL.casefold()] = report_books[GENERAL].fullname
                current = {Path(link).name.casefold(): str(link) for link in links(book)}
                if set(current) != set(expected):
                    raise RuntimeError(f"Изменился набор источников: {name}")
                for key, link in current.items():
                    if Path(link) != Path(expected[key]):
                        book.api.ChangeLink(link, expected[key], 1)
                for sheet in book.sheets:
                    sheet.api.Calculate()
                log(folder, "calculated", mode=mode, file=name)
            assert digest(master) == master_hash
            assert all(digest(path) == initial[name] for name, path in work.items())
            for name, book in report_books.items():
                log(folder, "save_intent", mode=mode, file=name)
                book.save()
                log(folder, "saved", mode=mode, file=name)
            report_books[GENERAL].api.SaveCopyAs(str(values_file))
            published = open_book(values_file)
            for link in links(published):
                published.api.BreakLink(str(link), 1)
            if links(published):
                raise RuntimeError("В выгрузке значений остались внешние связи.")
            published.save()
            published.close()
            opened.pop()
            value_tabs = LAYOUT[GENERAL]
            assert cached(values_file, value_tabs) == cached(work[GENERAL], value_tabs)
            assert digest(work[VALUES]) == initial[VALUES]
            log(folder, "values_save_intent", mode=mode, file=VALUES, next_sha256=digest(values_file))
            published = open_book(values_file)
            try:
                published.save(str(work[VALUES]))
            except com_error as error:
                log(folder, "values_publish_failed", mode=mode, candidate=str(values_file), error=str(error))
        finally:
            for book in reversed(opened):
                with suppress(Exception):
                    book.close()

    changes = {}
    for name, tabs in LAYOUT.items():
        before = cached(backup / name, tabs)
        after = cached(work[name], tabs)
        changes[name] = {
            tab: sum(before[tab].get(key) != after[tab].get(key) for key in before[tab].keys() | after[tab].keys())
            for tab in tabs
        }
    value_tabs = LAYOUT[GENERAL]
    assert cached(values_file, value_tabs) == cached(work[GENERAL], value_tabs)
    values_published = cached(work[VALUES], value_tabs) == cached(values_file, value_tabs)
    assert digest(master) == master_hash
    log(folder, "verified", mode=mode, numerical_changes=changes, values_published=values_published,
        after={name: digest(path) for name, path in work.items()})
    return values_published


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", required=True, type=Path)
    parser.add_argument("--reports", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path, help="Local directory for backups and verification")
    parser.add_argument("--apply", action="store_true", help="Publish to the existing report files")
    args = parser.parse_args()
    args.journal.mkdir(parents=True, exist_ok=True)
    raise SystemExit(0 if refresh(args.master, args.reports, args.journal, live=args.apply) else 2)
