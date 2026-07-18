"""repair_comments.build_repair_plan — починка личных реестров после бага
«комментарий выезда — в колонке звонка». Чистая логика, без Excel и сервера."""
import repair_comments as rc

FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID"},
    {"key": "call_comment", "title": "комментарий звонка"},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда"},
]
HDR = ["Номер КД / ID", "комментарий звонка", "Комментарий текущего месяца выезда"]


def _grid(*rows):
    return [HDR, ["сх", "хард", "хард"], *rows]


def test_poison_moved_and_original_restored():
    """Бот-текст уходит из «звонка» в «Комментарий выезда», оригинал — из бэкапа."""
    grid = _grid(["R-1", "Приехали, встретили жену клиента", ""])
    srv = {"R-1": {"visit_comment": "Приехали, встретили жену клиента"}}
    st = rc.build_repair_plan(grid, FIELDS, srv, {"R-1": "06.02 клиент ищет деньги"})
    assert st["visit_filled"] == 1 and st["poison_cleared"] == 1
    assert st["restored_from_backup"] == 1
    assert (2, 2, "Приехали, встретили жену клиента") in st["writes"]
    assert (2, 1, "06.02 клиент ищет деньги") in st["writes"]


def test_poisoned_backup_clears_cell():
    """Бэкап сделан уже после затирания (тот же бот-текст) → ячейку просто чистим."""
    grid = _grid(["R-2", "бот-комментарий длинный", ""])
    srv = {"R-2": {"visit_comment": "бот-комментарий длинный"}}
    st = rc.build_repair_plan(grid, FIELDS, srv, {"R-2": "бот-комментарий длинный"})
    assert st["poison_cleared"] == 1 and st["restored_from_backup"] == 0
    assert (2, 1, None) in st["writes"]


def test_genuine_call_comment_untouched():
    """Настоящий колл-комментарий (текст другой) не трогаем, выезд дописываем."""
    grid = _grid(["R-3", "11.12.2024 позвонил, не берёт", ""])
    srv = {"R-3": {"visit_comment": "видел клиента лично"}}
    st = rc.build_repair_plan(grid, FIELDS, srv, {})
    assert st["genuine_kept"] == 1 and st["poison_cleared"] == 0
    assert st["writes"] == [(2, 2, "видел клиента лично")]


def test_rows_without_server_comment_skipped():
    grid = _grid(["R-4", "что-то из импорта", ""], ["R-5", "", ""])
    st = rc.build_repair_plan(grid, FIELDS, {"R-4": {"visit_comment": ""}}, {})
    assert st["rows_matched"] == 0 and st["writes"] == []


def test_already_repaired_is_idempotent():
    """Повторный прогон по починенному файлу ничего не пишет."""
    grid = _grid(["R-6", "06.02 клиент ищет деньги", "видел клиента лично"])
    srv = {"R-6": {"visit_comment": "видел клиента лично"}}
    st = rc.build_repair_plan(grid, FIELDS, srv, {"R-6": "06.02 клиент ищет деньги"})
    assert st["writes"] == [] and st["genuine_kept"] == 1
