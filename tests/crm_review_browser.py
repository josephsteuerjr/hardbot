"""Actual compiled renderer with synthetic IPC; no production server or profile."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

qa=Path(sys.argv[1]).resolve()
fixture=r'''() => {
const context={role:'employee',employee_uuid:'synthetic-employee',owner:false,capabilities:['call','visit','note','contact','promise','task','reported_payment','correct'],fields:['soft_call_status','soft_comment','soft_comm_date','phone','phone_main','address','extra_address'].map(key=>({key,title:key,type:key==='soft_comm_date'?'date':'text'})),dropdowns:{soft_call_status:['Нет контакта','Контакт']}};
const card={person:{person_uuid:'person-one',name:'Синтетический клиент'},contracts:['A','B'].map(id=>({contract_uuid:id,external_id:'Договор '+id,creditor_id:'Кредитор '+id,currency:id==='A'?null:'TJS',fields:{soft_comment:'Base',debt_total:'100.00'},revisions:{call:0,contact:0,forecast:0}})),history:[{event_id:'synthetic-call',kind:'call',origin:'app',received_at:'2026-11-10T00:00:00Z',contract_ids:['B'],payload:{values:{soft_comment:'Old'}}}],entities:[]};
window.review={commands:[],scriptReads:0,published:{script:{title:'Подсказка первая',steps:[{title:'Первый шаг',text:'Текст первой версии'}]},published_revision:2,published_at:'2026-11-10T00:00:00Z',script_ref:{team_id:'team-A',published_revision:2,script_hash:'a'.repeat(64)}}};
window.crm={state:async()=>({version:'review-local',context,connection:{mode:'synthetic'}}),
api:async(method,action)=>{let data={};if(action==='portfolio')data={people:[{person_uuid:'person-one',name:'Синтетический клиент',contracts:2,currencies:{TJS:'100.00'}}],total_people:1,total_contracts:2};if(action==='card')data=card;if(action==='script'){window.review.scriptReads++;data=window.review.published}return {ok:true,data:structuredClone(data)}},
draft:async(key,...args)=>{if(args.length)localStorage.setItem('review:'+key,JSON.stringify(args[0]));return {ok:true,data:JSON.parse(localStorage.getItem('review:'+key)||'null')}},
command:async body=>{window.review.commands.push(structuredClone(body));return {ok:true}},
telemetry:async()=>({ok:true}),queue:async()=>({ok:true,data:{items:[]}}),win:()=>{}};
}'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1380,'height':1000})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.add_init_script('('+fixture+')()')
    page.goto((qa/'index.html').as_uri())
    page.locator('[data-person]').click()
    page.locator('[data-action="call"]').click()
    page.locator('input[name=contracts][value=A]').uncheck()
    page.locator('[data-clear=soft_comment]').check()
    page.locator('#show-script').click()
    assert 'Подсказка первая' in page.locator('#script-inline').inner_text()
    page.evaluate("review.published.script.title='Подсказка вторая';review.published.published_revision=4;review.published.script_ref.published_revision=4")
    page.locator('#show-script').click()
    assert 'Подсказка первая' in page.locator('#script-inline').inner_text()
    assert page.evaluate('review.scriptReads')==1
    page.locator('#cancel').click()
    page.reload()
    page.locator('[data-person]').click()
    page.locator('[data-action="call"]').click()
    assert not page.locator('input[name=contracts][value=A]').is_checked()
    assert page.locator('input[name=contracts][value=B]').is_checked()
    assert page.locator('[data-clear=soft_comment]').is_checked()
    page.locator('#show-script').click()
    assert page.evaluate('review.scriptReads')==1
    page.locator('#action-form button[type=submit]').click()
    page.wait_for_function('review.commands.length===1')
    body=page.evaluate('review.commands[0]')
    assert body['contract_ids']==['B'] and body['payload']['values']['soft_comment'] is None
    assert body['payload']['script_ref']['published_revision']==2
    page.locator('[data-action="promise"]').click()
    assert page.locator('#currency').input_value()==''
    page.locator('input[name=contracts][value=B]').check()
    page.locator('#money').fill('12,30')
    page.locator('#currency').select_option('TJS')
    page.locator('#due').fill('2026-12-01T12:30')
    page.locator('#cancel').click()
    page.reload()
    page.locator('[data-person]').click()
    page.locator('[data-action="promise"]').click()
    assert page.locator('input[name=contracts][value=B]').is_checked()
    assert page.locator('#currency').input_value()=='TJS'
    assert page.locator('#money').input_value()=='12,30'
    assert page.locator('#due').input_value()=='2026-12-01T12:30'
    page.screenshot(path=str(qa/'promise-draft.png'))
    page.locator('#action-form button[type=submit]').click()
    page.wait_for_function('review.commands.length===1')
    body=page.evaluate('review.commands[0]')
    assert body['contract_ids']==['B'] and body['payload']['currency']=='TJS' and body['payload']['amount']=='12.30'
    page.locator('[data-action="task"]').click()
    page.locator('#free-text').fill('Позвонить по второму договору')
    page.locator('#due').fill('2026-12-02T13:00')
    page.locator('#cancel').click()
    page.locator('[data-action="task"]').click()
    assert page.locator('#free-text').input_value()=='Позвонить по второму договору'
    page.locator('#cancel').click()
    page.locator('[data-tab="history"]').click()
    page.locator('[data-correct]').click()
    assert not page.locator('input[name=contracts][value=A]').is_checked()
    assert page.locator('input[name=contracts][value=B]').is_checked()
    assert page.locator('input[name=contracts][value=A]').is_disabled()
    page.locator('#reason').fill('Уточнение синтетического результата')
    page.locator('#cancel').click()
    page.locator('[data-correct]').click()
    assert page.locator('#reason').input_value()=='Уточнение синтетического результата'
    assert not errors,errors
    result=dict(passed=True,errors=errors,backend='synthetic IPC',checks=['selected contract survives restart','explicit clear survives restart','script stays pinned across publication and restart','call includes pinned script reference','unknown currency not assumed RUB','promise currency/amount/date survive restart','task title retained','correction scope fixed to original','correction reason retained'])
    (qa/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    browser.close()
