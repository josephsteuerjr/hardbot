from dataclasses import replace
from pathlib import Path

from registry_cycle.config import GroupConfig, PortfolioRoot, load_config_bundle
from registry_cycle.discovery import discover


def _bundle_for(tmp_path: Path):
    bundle = load_config_bundle()
    roots = (
        PortfolioRoot(
            country="Тест",
            path=str(tmp_path),
            groups=(
                GroupConfig("Город", "hard", True, True),
                GroupConfig("Soft", "soft", True, True),
                GroupConfig("Отключён", "hard", False, False),
            ),
        ),
    )
    return replace(bundle, app=replace(bundle.app, portfolio_roots=roots))


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


def test_discovery_uses_only_configured_active_files(tmp_path):
    bundle = _bundle_for(tmp_path)
    active_dir = tmp_path / "Город" / "Иванов Иван"
    active_dir.mkdir(parents=True)
    (active_dir / "Иванов Иван.xlsb").write_bytes(b"active")
    (active_dir / "Иванов Иван (до разброса 27.07 12-00).xlsb").write_bytes(b"backup")
    (active_dir / "Копия Иванов Иван.xlsb").write_bytes(b"ambiguous-copy")
    (active_dir / "~$Иванов Иван.xlsb").write_bytes(b"lock")

    soft_dir = tmp_path / "Soft" / "Петров Пётр"
    soft_dir.mkdir(parents=True)
    (soft_dir / "СОФТ Петров Пётр.xlsb").write_bytes(b"soft")

    report = discover(bundle)

    assert not report.blocked
    assert {entry.path.name for entry in report.entries} == {
        "Иванов Иван.xlsb",
        "СОФТ Петров Пётр.xlsb",
    }
    assert {entry.profile for entry in report.entries} == {"hard", "soft"}
    assert not any(
        issue.code == "unexpected_active_workbook_path"
        for issue in report.issues
    )


def test_unconfigured_group_with_active_file_blocks_run(tmp_path):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Город").mkdir()
    (tmp_path / "Soft").mkdir()
    unknown = tmp_path / "Новый город" / "Сотрудник"
    unknown.mkdir(parents=True)
    (unknown / "Сотрудник.xlsb").write_bytes(b"x")

    report = discover(bundle)

    assert report.blocked
    issue = next(
        issue
        for issue in report.issues
        if issue.code == "unconfigured_group_has_files"
    )
    assert issue.context["files"] == [str(unknown / "Сотрудник.xlsb")]


def test_active_workbook_directly_in_group_blocks_with_exact_path(tmp_path):
    bundle = _bundle_for(tmp_path)
    group = tmp_path / "Город"
    group.mkdir()
    (tmp_path / "Soft").mkdir()
    workbook = group / "Иванов Иван.xlsb"
    workbook.write_bytes(b"misplaced")

    report = discover(bundle)

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


def test_active_workbook_below_employee_blocks_without_import(tmp_path):
    bundle = _bundle_for(tmp_path)
    nested = tmp_path / "Город" / "Иванов Иван" / "Старые"
    nested.mkdir(parents=True)
    (tmp_path / "Soft").mkdir()
    workbook = nested / "Иванов Иван.xlsb"
    workbook.write_bytes(b"too-deep")

    report = discover(bundle)

    assert report.blocked
    issue = next(
        item
        for item in report.issues
        if item.code == "unexpected_active_workbook_path"
    )
    assert issue.context["path"] == str(workbook)
    assert issue.context["reason"] == "active_file_below_employee"
    assert not report.entries


def test_root_workbook_is_an_unexpected_branch_and_blocks(tmp_path):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Город").mkdir()
    (tmp_path / "Soft").mkdir()
    workbook = tmp_path / "Иванов Иван.xlsb"
    workbook.write_bytes(b"wrong-branch")

    report = discover(bundle)

    assert report.blocked
    issue = next(
        item
        for item in report.issues
        if item.code == "unexpected_active_workbook_path"
    )
    assert issue.context["path"] == str(workbook)
    assert issue.context["reason"] == "active_file_directly_in_root"


def test_backup_and_ignored_subtrees_do_not_create_layout_errors(tmp_path):
    bundle = _bundle_for(tmp_path)
    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    active = employee / "Иванов Иван.xlsb"
    active.write_bytes(b"active")
    (tmp_path / "Soft").mkdir()

    (tmp_path / "Город" / "Копия Иванов Иван.xlsb").write_bytes(
        b"direct-backup"
    )
    (tmp_path / "Город" / "~$Иванов Иван.xlsb").write_bytes(b"lock")
    (tmp_path / "Город" / "Копия Иванов Иван.xlsx").write_bytes(
        b"unsupported-direct-backup"
    )
    (employee / "~$Иванов Иван.xlsm").write_bytes(
        b"unsupported-lock"
    )
    deep_backup = employee / "Старые"
    deep_backup.mkdir()
    (deep_backup / "Копия Иванов Иван.xlsb").write_bytes(b"deep-backup")
    (
        deep_backup
        / "Иванов Иван (до разброса 27.07 12-00).xls"
    ).write_bytes(b"unsupported-deep-backup")
    ignored_inside = employee / "_служебное"
    ignored_inside.mkdir()
    (ignored_inside / "Активный.xlsb").write_bytes(b"ignored")
    (ignored_inside / "Активный.xlsx").write_bytes(b"ignored-unsupported")
    ignored_root = tmp_path / "!архив" / "Группа" / "Сотрудник"
    ignored_root.mkdir(parents=True)
    (ignored_root / "Активный.xlsb").write_bytes(b"ignored-root")
    (ignored_root / "Активный.xlsm").write_bytes(
        b"ignored-root-unsupported"
    )

    report = discover(bundle)

    assert not report.blocked
    assert [entry.path for entry in report.entries] == [active]
    assert not any(
        issue.code == "unexpected_active_workbook_path"
        for issue in report.issues
    )
    assert not any(
        issue.code == "unsupported_active_workbook_format"
        for issue in report.issues
    )


def test_unsupported_excel_formats_block_at_every_geometry_level(tmp_path):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Soft").mkdir()

    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    nested = employee / "Старые"
    nested.mkdir()
    unknown = tmp_path / "Неизвестная группа" / "Сотрудник"
    unknown.mkdir(parents=True)
    disabled = tmp_path / "Отключён" / "Сотрудник"
    disabled.mkdir(parents=True)

    workbooks = {
        tmp_path / "На корне.xlsm",
        tmp_path / "Город" / "Прямо в группе.xlsx",
        employee / "Agent.xlsx",
        nested / "Agent.xls",
        unknown / "Agent.xlsm",
        disabled / "Agent.xls",
    }
    for workbook in workbooks:
        workbook.write_bytes(b"unsupported-active")

    report = discover(bundle)

    assert report.blocked
    issues = [
        issue
        for issue in report.issues
        if issue.code == "unsupported_active_workbook_format"
    ]
    assert {issue.context["path"] for issue in issues} == {
        str(path)
        for path in workbooks
    }
    assert {
        issue.context["extension"]
        for issue in issues
    } == {".xls", ".xlsx", ".xlsm"}
    assert all(
        issue.context["supported_extensions"] == [".xlsb"]
        for issue in issues
    )
    assert not report.entries
    assert not any(
        issue.code == "unconfigured_empty_group"
        and issue.context["path"] == str(tmp_path / "Неизвестная группа")
        for issue in report.issues
    )


def test_unreadable_portfolio_root_blocks_with_exact_path(tmp_path, monkeypatch):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Город").mkdir()
    (tmp_path / "Soft").mkdir()
    _make_directory_unreadable(monkeypatch, tmp_path)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(
        report,
        "portfolio_root_unreadable",
        tmp_path,
    )


def test_unreadable_configured_group_blocks_with_exact_path(tmp_path, monkeypatch):
    bundle = _bundle_for(tmp_path)
    group = tmp_path / "Город"
    group.mkdir()
    (tmp_path / "Soft").mkdir()
    _make_directory_unreadable(monkeypatch, group)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(report, "group_unreadable", group)


def test_unreadable_employee_folder_blocks_and_is_not_empty(tmp_path, monkeypatch):
    bundle = _bundle_for(tmp_path)
    employee = tmp_path / "Город" / "Иванов Иван"
    employee.mkdir(parents=True)
    (tmp_path / "Soft").mkdir()
    _make_directory_unreadable(monkeypatch, employee)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(
        report,
        "employee_folder_unreadable",
        employee,
    )
    assert not report.entries
    assert not any(
        issue.code == "empty_employee_folders"
        and issue.context["path"] == str(tmp_path / "Город")
        for issue in report.issues
    )


def test_unreadable_nested_folder_blocks_and_is_not_empty(tmp_path, monkeypatch):
    bundle = _bundle_for(tmp_path)
    nested = tmp_path / "Город" / "Иванов Иван" / "Старые"
    nested.mkdir(parents=True)
    (tmp_path / "Soft").mkdir()
    _make_directory_unreadable(monkeypatch, nested)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(
        report,
        "portfolio_subtree_unreadable",
        nested,
    )
    assert not report.entries
    assert not any(
        issue.code == "empty_employee_folders"
        and issue.context["path"] == str(tmp_path / "Город")
        for issue in report.issues
    )


def test_unreadable_unconfigured_group_blocks_and_is_not_empty(
    tmp_path,
    monkeypatch,
):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Город").mkdir()
    (tmp_path / "Soft").mkdir()
    unknown = tmp_path / "Новая группа"
    unknown.mkdir()
    _make_directory_unreadable(monkeypatch, unknown)

    report = discover(bundle)

    assert report.blocked
    _assert_unreadable_issue(
        report,
        "portfolio_subtree_unreadable",
        unknown,
    )
    assert not any(
        issue.code == "unconfigured_empty_group"
        and issue.context["path"] == str(unknown)
        for issue in report.issues
    )


def test_disabled_group_with_file_is_visible_but_not_in_manifest(tmp_path):
    bundle = _bundle_for(tmp_path)
    (tmp_path / "Город").mkdir()
    (tmp_path / "Soft").mkdir()
    disabled = tmp_path / "Отключён" / "Сотрудник"
    disabled.mkdir(parents=True)
    (disabled / "Сотрудник.xlsb").write_bytes(b"x")

    report = discover(bundle)

    assert not report.blocked
    assert not report.entries
    assert any(issue.code == "disabled_group_has_files" for issue in report.issues)


def test_filename_rule_can_override_folder_profile(tmp_path):
    bundle = _bundle_for(tmp_path)
    city = tmp_path / "Город" / "Петров Пётр"
    city.mkdir(parents=True)
    (city / "СОФТ Петров Пётр.xlsb").write_bytes(b"x")
    (tmp_path / "Soft").mkdir()

    report = discover(bundle)

    assert not report.blocked
    assert len(report.entries) == 1
    assert report.entries[0].profile == "soft"


def test_optional_gph_group_is_absence_tolerant_and_uses_hard_profile(tmp_path):
    bundle = load_config_bundle()
    roots = (
        PortfolioRoot(
            country="Тест",
            path=str(tmp_path),
            groups=(
                GroupConfig("Город", "hard", True, True),
                GroupConfig("ГПХ Агент выезд", "hard", True, False),
            ),
        ),
    )
    bundle = replace(bundle, app=replace(bundle.app, portfolio_roots=roots))
    (tmp_path / "Город").mkdir()

    absent = discover(bundle)

    assert not absent.blocked
    issue = next(
        item
        for item in absent.issues
        if item.code == "configured_group_missing"
    )
    assert issue.severity == "warning"

    employee = tmp_path / "ГПХ Агент выезд" / "Нишомов Иброхимжон"
    employee.mkdir(parents=True)
    workbook = employee / "Нишомов Иброхимжон.xlsb"
    workbook.write_bytes(b"hard-gph")

    present = discover(bundle)

    assert not present.blocked
    entry = next(item for item in present.entries if item.path == workbook)
    assert entry.group == "ГПХ Агент выезд"
    assert entry.profile == "hard"
    assert entry.responsible_hint == "Нишомов Иброхимжон"
