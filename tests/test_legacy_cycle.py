from types import SimpleNamespace
from tools.legacy_pipeline.cycle import STAGES, run, assignment_dates, text_columns


def test_read_failure_prevents_assignment_scatter_and_cleanup(tmp_path):
    for name in STAGES:
        (tmp_path/name).touch()
    called=[]
    def child(argv):
        called.append(argv[-1])
        return SimpleNamespace(returncode=1 if len(called)==2 else 0)
    assert run(tmp_path, runner=child)==1
    assert [x.rsplit('\\',1)[-1].rsplit('/',1)[-1] for x in called]==list(STAGES[:2])


def test_assignment_only_updates_assignment_dates():
    writes=[]
    grid=SimpleNamespace(set_date=lambda *args:writes.append(args))
    assignment_dates(grid, {'dh':3,'ds':4}, {5:{'h','s'}}, 'today',hard='h',soft='s',date_hard='dh',date_soft='ds')
    assert writes==[(5,3,'today'),(5,4,'today')]


def test_text_format_precedes_data_and_does_not_touch_amounts():
    calls=[]
    class Cell:
        def __setattr__(self,k,v):calls.append((k,v))
    class Sheet:
        def range(self,*args):calls.append(args);return Cell()
    text_columns(Sheet(),['Номер КД / ID','Доп. Адрес/Доп.Номер','Сумма долга'],4,2)
    assert calls==[((4,1),(5,1)),('number_format','@'),((4,2),(5,2)),('number_format','@')]
