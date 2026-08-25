"""Обход портфелей: что берётся в манифест, а что останавливает работу.

Правила поменялись и проверяются здесь именно в новом виде:

* группы под корнем не настраиваются — участвуют все, кроме архивных;
* профиль на этом шаге всегда «workbook»: настоящий определяется позже
  по столбцам самой книги, а не по имени папки или файла;
* резервные копии отсекаются простыми префиксами «~» и «Копия », без регулярных
  выражений.

Два места ниже закрепляют поведение, которое молчит: книга глубже сотрудника
и книга неподдерживаемого формата при штатных настройках просто не попадают
в перенос. Тесты держат это на виду, чтобы изменение настроек было заметно.
"""

from dataclasses import replace
from pathlib import Path

from registry_cycle.config import PortfolioRoot, load_config_bundle
from registry_cycle.discovery import discover


def _bundle_for(tmp_path: Path, **app_overrides):
    bundle = load_config_bundle()
    roots = (
        PortfolioRoot(
            country="Тест",
            path=str(tmp_path),
            groups=(),
        ),
    )
    return replace(
        bundle,
        app=replace(bundle.app, portfolio_roots=roots, **app_overrides),
    )


def _make_directory_unreadable(monkeypatch, blocked_path: Path) -> None:
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == blocked_path:
            raise PermissionError("synthetic access denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)


def _assert_unreadable_issue(report, code: str, path: Path) -> None:
    issues = [issue for issue in report.issues if issue.code == code]
    assert len(issues) == 1
    assert issues[0].context == {
        "path": str(path),
        "error": "synthetic access denied",
    }


# ── что попадает в манифест ────────────────────────────────────────────────


def test_only_the_single_active_workbook_of_an_employee_is_taken(tmp_path):
    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    active = employee / "Иванов Иван.xlsb"
    active.write_bytes(b"active")
    (employee / "Копия Иванов Иван.xlsb").write_bytes(b"copy")
    (employee / "~$Иванов Иван.xlsb").write_bytes(b"lock")

    report = discover(_bundle_for(tmp_path))

    assert not report.blocked
    assert [entry.path for entry in report.entries] == [active]
    entry = report.entries[0]
    assert entry.group == "Город"
    assert entry.employee_folder == "Иванов Иван"
    assert entry.responsible_hint == "Иванов Иван"


def test_profile_is_not_decided_at_discovery_time(tmp_path):
    """Профиль книги определяется по её столбцам, а не по имени файла.

    Прежде префикс «СОФТ » в имени файла переключал профиль. Правило снято
    намеренно: переименованная книга не должна менять состав столбцов, который
    поедет сотруднику.
    """
    employee = tmp_path / "Город" / "Петров Пётр"
    employee.mkdir(parents=True)
    (employee / "СОФТ Петров Пётр.xlsb").write_bytes(b"x")

    report = discover(_bundle_for(tmp_path))

    assert not report.blocked
    assert len(report.entries) == 1
    assert report.entries[0].profile == "workbook"


def test_any_group_under_the_root_participates_without_configuration(tmp_path):
    """Новая группа просто появляется в переносе — правки настроек не нужно."""
    employee = tmp_path / "Новая группа" / "Сотрудник"
    employee.mkdir(parents=True)
    (employee / "Сотрудник.xlsb").write_bytes(b"x")

    report = discover(_bundle_for(tmp_path))

    assert not report.blocked
    assert report.discovered_groups == ["Тест / Новая группа"]
    assert [entry.group for entry in report.entries] == ["Новая группа"]


def test_archive_groups_are_skipped_entirely(tmp_path):
    for archive in ("Архив 2025", "!Архив"):
        folder = tmp_path / archive / "Сотрудник"
        folder.mkdir(parents=True)
        (folder / "Сотрудник.xlsb").write_bytes(b"archived")
    working = tmp_path / "Город" / "Иванов"
    working.mkdir(parents=True)
    (working / "Иванов.xlsb").write_bytes(b"active")

    report = discover(_bundle_for(tmp_path))

    assert not report.blocked
    assert report.discovered_groups == ["Тест / Город"]
    assert [entry.path.parent.parent.name for entry in report.entries] == ["Город"]


# ── что останавливает работу ───────────────────────────────────────────────


def test_two_active_workbooks_in_one_employee_folder_block(tmp_path):
    """Ловушка переезда: старые сайдкар-копии рядом с книгой.

    Прежде «Фамилия (до разброса 27.07 12-00).xlsb» отсекался регулярным
    выражением. Теперь резервные копии узнаются только по префиксам «~»
    и «Копия », поэтому оставшийся с июля файл считается вторым активным
    и останавливает перенос до того, как что-либо будет записано.
    """
    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    (employee / "Иванов Иван.xlsb").write_bytes(b"active")
    (employee / "Иванов Иван (до разброса 27.07 12-00).xlsb").write_bytes(b"old")

    report = discover(_bundle_for(tmp_path))

    assert report.blocked
    issue = next(
        item for item in report.issues if item.code == "multiple_active_files"
    )
    assert len(issue.context["files"]) == 2


def test_active_workbook_directly_in_group_blocks_with_exact_path(tmp_path):
    group = tmp_path / "Город"
    group.mkdir()
    workbook = group / "Иванов Иван.xlsb"
    workbook.write_bytes(b"misplaced")

    report = discover(_bundle_for(tmp_path))

    assert report.blocked
    issue = next(
        item
        for item in report.issues
        if item.code == "unexpected_active_workbook_path"
    )
    assert issue.context == {
        "path": str(workbook),
        "reason": "active_file_directly_in_group",
        "expected_layout": "root/group/employee/*.xlsb",
    }
    assert not report.entries


def test_root_workbook_is_an_unexpected_branch_and_blocks(tmp_path):
    (tmp_path / "Город").mkdir()
    workbook = tmp_path / "Иванов Иван.xlsb"
    workbook.write_bytes(b"wrong-branch")

    report = discover(_bundle_for(tmp_path))

    assert report.blocked
    issue = next(
        item
        for item in report.issues
        if item.code == "unexpected_active_workbook_path"
    )
    assert issue.context["path"] == str(workbook)
    assert issue.context["reason"] == "active_file_directly_in_root"


# ── что молчит при штатных настройках ──────────────────────────────────────


def test_workbook_below_employee_is_silent_until_deep_audit(tmp_path):
    """Штатный обход «direct» не спускается ниже папки сотрудника.

    Книга, унесённая в подпапку, не попадает в перенос и не даёт ни одной
    записи в отчёт: сотрудник просто пропадает из раздачи. Режим «deep»
    находит её и останавливает работу. Тест закрепляет обе стороны, чтобы
    смена режима в настройках не прошла незамеченной.
    """
    nested = tmp_path / "Город" / "Иванов Иван" / "Старые"
    nested.mkdir(parents=True)
    workbook = nested / "Иванов Иван.xlsb"
    workbook.write_bytes(b"too-deep")

    direct = discover(_bundle_for(tmp_path))
    assert not direct.blocked
    assert not direct.entries
    assert not any(
        issue.code == "unexpected_active_workbook_path" for issue in direct.issues
    )

    deep = discover(_bundle_for(tmp_path), audit_mode="deep")
    assert deep.blocked
    issue = next(
        item
        for item in deep.issues
        if item.code == "unexpected_active_workbook_path"
    )
    assert issue.context["path"] == str(workbook)
    assert issue.context["reason"] == "active_file_below_employee"
    assert not deep.entries


def test_unsupported_format_is_ignored_by_default_and_reported_on_demand(tmp_path):
    """У сотрудника только .xlsx — он выпадает из переноса молча.

    Рабочий формат один, .xlsb. При штатной настройке «ignore» чужой формат
    не даёт ни записи, ни отметки; при «report» — отметку без остановки.
    """
    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    (employee / "Иванов Иван.xlsx").write_bytes(b"unsupported")

    ignored = discover(_bundle_for(tmp_path))
    assert not ignored.blocked
    assert not ignored.entries
    assert not ignored.issues

    reported = discover(
        _bundle_for(tmp_path, unsupported_workbook_policy="report")
    )
    assert not reported.blocked
    assert not reported.entries
    issue = next(
        item
        for item in reported.issues
        if item.code == "unsupported_active_workbook_format"
    )
    assert issue.context["extension"] == ".xlsx"
    assert issue.context["supported_extensions"] == [".xlsb"]


def test_backup_and_ignored_subtrees_do_not_create_layout_errors(tmp_path):
    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    active = employee / "Иванов Иван.xlsb"
    active.write_bytes(b"active")

    (tmp_path / "Город" / "Копия Иванов Иван.xlsb").write_bytes(b"direct-backup")
    (tmp_path / "Город" / "~$Иванов Иван.xlsb").write_bytes(b"lock")
    (tmp_path / "Город" / "Копия Иванов Иван.xlsx").write_bytes(b"unsupported")
    (employee / "~$Иванов Иван.xlsm").write_bytes(b"unsupported-lock")
    deep_backup = employee / "Старые"
    deep_backup.mkdir()
    (deep_backup / "Копия Иванов Иван.xlsb").write_bytes(b"deep-backup")
    archived = tmp_path / "!Архив" / "Группа" / "Сотрудник"
    archived.mkdir(parents=True)
    (archived / "Активный.xlsb").write_bytes(b"archived")

    report = discover(_bundle_for(tmp_path))

    assert not report.blocked
    assert [entry.path for entry in report.entries] == [active]
    assert not any(
        issue.code == "unexpected_active_workbook_path"
        for issue in report.issues
    )


# ── нечитаемые каталоги ────────────────────────────────────────────────────


def test_unreadable_portfolio_root_blocks_with_exact_path(tmp_path, monkeypatch):
    (tmp_path / "Город").mkdir()
    bundle = _bundle_for(tmp_path)
    _make_directory_unreadable(monkeypatch, tmp_path)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(report, "portfolio_root_unreadable", tmp_path)


def test_unreadable_group_blocks_with_exact_path(tmp_path, monkeypatch):
    group = tmp_path / "Город"
    group.mkdir()
    bundle = _bundle_for(tmp_path)
    _make_directory_unreadable(monkeypatch, group)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(report, "group_unreadable", group)


def test_unreadable_employee_folder_blocks_and_is_not_empty(tmp_path, monkeypatch):
    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    bundle = _bundle_for(tmp_path)
    _make_directory_unreadable(monkeypatch, employee)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(report, "employee_folder_unreadable", employee)
    assert not report.entries
    assert not any(
        issue.code == "empty_employee_folders"
        and issue.context["path"] == str(tmp_path / "Город")
        for issue in report.issues
    )


def test_unreadable_nested_folder_blocks_only_in_deep_audit(tmp_path, monkeypatch):
    """Ниже сотрудника штатный обход не спускается — и не спотыкается там."""
    nested = tmp_path / "Город" / "Иванов Иван" / "Старые"
    nested.mkdir(parents=True)
    bundle = _bundle_for(tmp_path)
    deep_bundle = _bundle_for(tmp_path)
    _make_directory_unreadable(monkeypatch, nested)

    direct = discover(bundle)
    assert not direct.blocked
    assert not direct.entries

    deep = discover(deep_bundle, audit_mode="deep")
    assert deep.blocked
    _assert_unreadable_issue(deep, "portfolio_subtree_unreadable", nested)
    assert not deep.entries
