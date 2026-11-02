import pytest
from tools.legacy_pipeline.validation import verify_cells


def test_planned_cell_is_the_only_allowed_difference():
    verify_cells({'S':{(0,0):'old',(0,1):'=TODAY()'}},{'S':{(0,0):'new',(0,1):'=TODAY()'}},{('S',0,0)})


def test_changed_formula_is_not_hidden_by_same_cached_value():
    with pytest.raises(ValueError,match='Незапланированное'):
        verify_cells({'S':{(0,0):'=1+2'}},{'S':{(0,0):'=3'}},set())


def test_sheet_loss_is_not_published():
    with pytest.raises(ValueError,match='набор листов'):
        verify_cells({'S':{},'Statistics':{}},{'S':{}},set())
