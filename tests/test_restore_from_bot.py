from pult import restore_from_bot as restore


def test_value_diffs_reports_only_changed_coordinates():
    before = [["КД", "Телефон"], ["1", None], ["2", "старое"]]
    after = [["КД", "Телефон"], ["1", "новое"], ["2", "старое"]]

    assert restore._value_diffs(before, after) == {(2, 2)}


def test_value_diffs_treats_equivalent_empty_cells_as_equal():
    assert restore._value_diffs([[None, ""]], [["   ", None]]) == set()
