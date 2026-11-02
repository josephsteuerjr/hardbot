from datetime import date
import pytest
from tools.legacy_pipeline.collection_guard import choose, CollectionConflict


def test_newer_soft_result_wins_over_older_hard_result_in_either_order():
    assert choose('old', 'new', '2026-11-01', '2026-11-02')
    assert not choose('new', 'old', '2026-11-02', '2026-11-01')


@pytest.mark.parametrize('days', [(None, None), (None, '2026-11-02'), ('2026-11-02', None), ('2026-11-02', '2026-11-02')])
def test_unknown_or_same_day_order_is_a_conflict(days):
    with pytest.raises(CollectionConflict):
        choose('first', 'second', *days)


def test_numeric_representation_does_not_invent_conflict():
    assert not choose(12000.0, '12 000,00', '2026-11-02', '2026-11-02')


def test_blank_cannot_erase_and_real_zero_is_not_blank():
    assert not choose(12000, None, '2026-11-01', '2026-11-02')
    assert choose(12000, 0, '2026-11-01', '2026-11-02')
