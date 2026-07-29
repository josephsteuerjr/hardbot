import pytest

from pult import restore_from_bot as restore


def test_value_diffs_reports_only_changed_coordinates():
    before = [["КД", "Телефон"], ["1", None], ["2", "старое"]]
    after = [["КД", "Телефон"], ["1", "новое"], ["2", "старое"]]

    assert restore._value_diffs(before, after) == {(2, 2)}


def test_value_diffs_treats_equivalent_empty_cells_as_equal():
    assert restore._value_diffs([[None, ""]], [["   ", None]]) == set()


def test_restore_apply_is_fail_closed_before_settings(monkeypatch, capsys):
    monkeypatch.delenv(restore.LEGACY_WRITE_ENV, raising=False)
    monkeypatch.setattr(
        restore,
        "load_settings",
        lambda: pytest.fail("guard должен сработать до чтения настроек"),
    )

    code = restore.main(["--apply"])

    assert code == restore.LEGACY_WRITE_BLOCKED_EXIT
    assert restore.LEGACY_WRITE_ENV in capsys.readouterr().err


def test_restore_direct_apply_is_guarded(monkeypatch, tmp_path):
    monkeypatch.delenv(restore.LEGACY_WRITE_ENV, raising=False)

    with pytest.raises(restore.LegacyWriteBlocked):
        restore.apply_plan(
            object(),
            tmp_path / "never-opened.xlsb",
            {},
            [],
            lambda _message: None,
        )


def test_restore_read_only_mode_remains_available(monkeypatch):
    monkeypatch.delenv(restore.LEGACY_WRITE_ENV, raising=False)
    called = []

    def no_settings():
        called.append(True)
        return {}

    monkeypatch.setattr(restore, "load_settings", no_settings)

    assert restore.main([]) == 2
    assert called == [True]


def test_restore_emergency_flag_requires_exact_one(monkeypatch):
    for value in ("", "0", "true", "yes", " 1"):
        monkeypatch.setenv(restore.LEGACY_WRITE_ENV, value)
        assert not restore.legacy_write_enabled()
    monkeypatch.setenv(restore.LEGACY_WRITE_ENV, "1")
    assert restore.legacy_write_enabled()
