"""Local startup entry: wait for the configured working share, then bootstrap."""
from pathlib import Path
import json,os,runpy,sys,time
sys.dont_write_bytecode=True
os.environ['PYTHONDONTWRITEBYTECODE']='1'
state=Path(__file__).resolve().parent
for attempt in range(180):
    try:
        pointer=json.loads((state/'config.json').read_text('utf-8'))
        manifest=Path(pointer['manifest'])
        if manifest.is_file():
            directory=manifest.parent
            os.environ['HARD_MANUAL_DATES_CONFIG']=str(manifest)
            sys.path.insert(0,str(directory))
            runpy.run_path(str(directory/'rbr_manual_dates.py'),run_name='__manual_dates_start__')
            break
    except Exception as exc:
        with (state/'startup.log').open('a',encoding='utf-8') as log:log.write(str(exc)+'\n')
    time.sleep(10)
