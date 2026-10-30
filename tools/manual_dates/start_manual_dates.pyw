"""Current-user startup: wait for the working share, then run local Python."""
import os,runpy,time,sys
sys.dont_write_bytecode=True
from pathlib import Path
directory=Path(__file__).resolve().parent
for attempt in range(180):
    try:
        if (directory/'rbr-manual-dates.json').is_file():
            runpy.run_path(str(directory/'rbr_manual_dates.py'),run_name='__manual_dates_start__')
            break
    except Exception as exc:
        local=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Hard/manual-edits'
        local.mkdir(parents=True,exist_ok=True)
        with (local/'startup.log').open('a',encoding='utf-8') as log:log.write(str(exc)+'\n')
    time.sleep(10)
