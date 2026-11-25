"""Disposable SQLite/loopback API + actual Electron renderer; never uses live profiles."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

DESK=Path(__file__).resolve().parents[1]
ROOT=DESK.parent
PREVIEW='--preview' in sys.argv
QA=Path(os.environ.get('HARD_CALL_SCRIPT_QA_ROOT') or tempfile.mkdtemp(prefix='hard-call-script-'))
QA.mkdir(parents=True,exist_ok=True)
os.environ.update(APP_DATA=str(QA/'server'),ADMIN_ID='700700700',ACCOUNT_CUTOVER_AT_UTC='2999-01-01T00:00:00Z')
sys.path.insert(0,str(ROOT/'server'))
from aiohttp import web as aio
import accounts
import call_scripts
import db
import staff_roster
import web

SAMPLE=dict(title='Повторный контакт: договориться о следующем шаге',steps=[
 dict(title='Начало разговора',text='Здравствуйте, меня зовут [имя]. Удобно сейчас поговорить?\nЕсли неудобно — согласуйте время повторного звонка.'),
 dict(title='Уточнить ситуацию',text='Какой вопрос мешает выполнить прежнюю договорённость?\nВыслушайте ответ и кратко запишите причину в заметке к звонку.'),
 dict(title='Согласовать конкретный шаг',text='Какой следующий шаг и в какой день вам удобно выполнить?\nПовторите договорённость своими словами. Если срок пока неизвестен, назначьте следующий контакт.'),
 dict(title='Завершить и зафиксировать',text='Подтвердите дату и время следующего контакта.\nВыберите статус, внесите заметку и сохраните звонок. Не записывайте обещание, если оно не согласовано.')])


def seed():
    assert not db.DB_PATH.exists(),'Use a fresh isolated QA directory'
    db.init_db()
    db.set_manager(781001,'Синтетический руководитель',[],['Синтетический оператор'])
    data=[dict(employee='Синтетический оператор',products=['hard','soft'],manager_uid=781001)]
    p=staff_roster.preview(data);assert staff_roster.apply(data,p['sha256'],by='synthetic-qa')['ok']
    with db.connect() as c:c.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('SYN-A','Синтетический клиент','Синтетический оператор','Синтетический оператор')")
    assert accounts.create_account('Синтетический руководитель','synthetic-leader','Original123',role='manager',telegram_id=781001)['ok']
    assert accounts.approve_account('synthetic-leader','synthetic-owner')['ok']
    assert accounts.change_password('synthetic-leader','Original123','Manager123')['ok']
    leader=accounts.login('synthetic-leader','Manager123',device=dict(kind='phone',fp='synthetic-manager-phone'),entrance='internal',audience='manager')
    assert leader['action']=='ok'
    assert accounts.create_account('Синтетический оператор','synthetic-staff','Original123')['ok']
    assert accounts.change_password('synthetic-staff','Original123','Employee123')['ok']
    employee=accounts.login('synthetic-staff','Employee123',device=dict(kind='phone',fp='synthetic-employee-phone'),entrance='internal',audience='employee')
    with db.connect() as c:device=c.execute("SELECT id FROM account_devices WHERE fingerprint='synthetic-employee-phone'").fetchone()[0]
    assert accounts.manager_approve(781001,device_id=device)['active']
    call_scripts.mutate(781001,dict(action='save',base_revision=0,operation_id='synthetic-initial-draft',content=SAMPLE))
    if PREVIEW:call_scripts.mutate(781001,dict(action='publish',base_revision=1,operation_id='synthetic-preview-publish'))
    return leader['token'],employee['token']


async def main():
    leader,employee=seed()
    app=web.build_app(None)
    runner=aio.AppRunner(app);await runner.setup()
    site=aio.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    shots=QA/'screenshots';shots.mkdir()
    env={**os.environ,'HARD_SCRIPT_QA_URL':f'http://127.0.0.1:{port}',
         'HARD_SCRIPT_QA_MANAGER':leader,'HARD_SCRIPT_QA_EMPLOYEE':employee,
         'HARD_SCRIPT_QA_SHOTS':str(shots),'HARD_SCRIPT_QA_PROFILE':str(QA/'electron')}
    env.pop('ELECTRON_RUN_AS_NODE',None)
    if PREVIEW:env['HARD_SCRIPT_QA_PREVIEW']='1'
    command=[str(DESK/'node_modules/electron/dist/electron.exe'),str(DESK/'tools/test-call-script-ui.cjs')]
    try:
        with (QA/'electron.log').open('wb') as log:
            process=await asyncio.create_subprocess_exec(*command,cwd=DESK,env=env,stdout=log,stderr=subprocess.STDOUT)
            code=await process.wait() if PREVIEW else await asyncio.wait_for(process.wait(),timeout=150)
        assert code==0,'Inspect '+str(QA/'electron.log')
        if PREVIEW:return
        result=json.loads((shots/'UI-RESULT.private.json').read_text('utf-8'))
        assert result['stage']=='verified'
        (QA/'RESULT.private.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(dict(stage='verified',receipt=str(QA/'RESULT.private.json'),screenshots=str(shots))),flush=True)
    finally:
        if 'process' in locals() and process.returncode is None:
            process.terminate();await process.wait()
        await runner.cleanup()


if __name__=='__main__':asyncio.run(main())
