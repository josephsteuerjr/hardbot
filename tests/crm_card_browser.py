"""Compiled client card with synthetic IPC, at normal and narrow sizes."""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

qa=Path(sys.argv[1]).resolve()
fixture=r'''() => {
const fields=[['debt_overdue','Просроченный долг'],['interest_overdue','Просроченные проценты'],['duty','Госпошлина']].map(([key,title])=>({key,title,type:'money'}));
const context={role:'employee',employee_uuid:'employee-one',owner:false,read_all_contracts:true,capabilities:['call','note'],fields,dropdowns:{}};
const card={person:{person_uuid:'person-one',name:'Карточка проверки'},contracts:[{contract_uuid:'contract-one',external_id:'КД 001',creditor_id:'Тестовый кредитор',currency:'RUB',allowed_actions:['call','note'],assignments:[{employee_uuid:'employee-one',name:'Сотрудник проверки',capability:'call'}],fields:{phone:'+7 000 000-00-00',address:'Адрес для проверки',kd_date:'2026-01-15',issued_sum:'50000.00',dp:'45',debt_today:'35400.00',debt_overdue:'30000.00',interest_overdue:'5000.00',duty:'400.00',soft_call_status:'Обещает оплатить',soft_comment:'Перезвонить завтра',call_status:'Передан конверт',visit_comment:'Вручен по адресу'},revisions:{call:0}}],history:[],entities:[]};
window.cardFixture={card,context,commands:[]};
window.crm={state:async()=>({version:'local-qa',context,connection:{mode:'synthetic'}}),
api:async(method,action)=>({ok:true,data:structuredClone(action==='portfolio'?{people:[{person_uuid:'person-one',name:card.person.name,contracts:1,currencies:{RUB:'35400.00'}}],total_people:1,total_contracts:1}:action==='card'?card:action==='script'?{script:{title:'Разговор с клиентом',steps:[{title:'Проверить собеседника',text:'Уточните, с кем разговариваете. Проверьте сведения о договоре.'},{title:'Обсудить оплату',text:'Уточните возможную сумму и дату оплаты. Зафиксируйте результат разговора.'}]},published_revision:3,published_at:'2026-11-12T00:00:00Z'}:{})}),
draft:async(key,...args)=>{if(key==='portfolio-view'&&!args.length&&!localStorage.getItem('portfolio-view'))return {ok:true,data:{view:'all'}};if(args.length)localStorage.setItem('card-draft:'+key,JSON.stringify(args[0]));return {ok:true,data:JSON.parse(localStorage.getItem('card-draft:'+key)||'null')}},command:async body=>{cardFixture.commands.push(structuredClone(body));return {ok:true}},telemetry:async()=>({ok:true}),queue:async()=>({ok:true,data:{items:[]}}),win:()=>{}};
}'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport=dict(width=1380,height=1000))
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.add_init_script('('+fixture+')()')
    page.goto((qa/'index.html').as_uri())
    page.locator('[data-person]').click()
    page.locator('.card-script-watermark').wait_for()
    assert page.locator('[data-field=issued_sum]').inner_text()=='50000.00 RUB'
    assert page.locator('[data-field=kd_date]').inner_text()=='15.01.2026'
    assert 'Сотрудник проверки' in page.locator('[data-field=responsibility]').inner_text()
    text=page.locator('#card-content').inner_text()
    assert 'Звонок: Обещает оплатить' in text and 'Выезд: Передан конверт' in text
    page.locator('.contract-details summary').click()
    assert page.locator('[data-field=debt_overdue]').inner_text()=='30000.00 RUB'
    assert 'Просроченные проценты' in page.locator('.debt-articles').inner_text()
    a=page.locator('#card-content').bounding_box();b=page.locator('.card-script-watermark').bounding_box()
    assert b['x']>=a['x']+a['width'],(a,b)
    page.screenshot(path=str(qa/'client-card-wide.png'),full_page=True)
    page.set_viewport_size(dict(width=1050,height=1000))
    a=page.locator('#card-content').bounding_box();b=page.locator('.card-script-watermark').bounding_box()
    assert b['y']>=a['y']+a['height']-1,(a,b)
    page.screenshot(path=str(qa/'client-card-narrow.png'),full_page=True)
    page.evaluate('cardFixture.card.contracts[0].allowed_actions=[]')
    page.locator('#back').click();page.locator('[data-person]').click()
    assert page.locator('[data-action]').count()==0
    assert page.locator('[data-field=issued_sum]').is_visible()
    page.evaluate("cardFixture.context.role='manager';cardFixture.context.capabilities.push('task');cardFixture.context.staff=[{employee_uuid:'employee-one',name:'Руководитель проверки',work_kinds:['call']},{employee_uuid:'employee-two',name:'Исполнитель проверки',work_kinds:['visit']}];cardFixture.card.contracts[0].allowed_actions=['task']")
    page.locator('#back').click();page.locator('[data-person]').click()
    page.locator('[data-action=task]').click()
    page.locator('#free-text').fill('Отработать клиента сегодня')
    page.locator('#task-assignee').select_option('employee-two')
    page.locator('#due').fill('2026-11-12T18:00')
    page.locator('#cancel').click();page.locator('[data-action=task]').click()
    assert page.locator('#task-assignee').input_value()=='employee-two'
    assert page.locator('#task-work').input_value()=='visit'
    page.locator('#action-form button[type=submit]').click()
    page.wait_for_function('cardFixture.commands.length===1')
    body=page.evaluate('cardFixture.commands[0]')
    assert body['contract_ids']==['contract-one'] and body['payload']['assignee_uuid']=='employee-two'
    assert body['payload']['work_kind']=='visit'
    page.evaluate('''{
      const old=crm.api;
      crm.api=async(method,action,...args)=>action==='cabinet'?{ok:true,data:{employee:'Сотрудник проверки',stats:{call:3,visit:1,processed_contracts:2,portfolio_contracts:4},payments:{source_as_of:['2026-11-12T03:00:00Z'],known_contracts:3,missing_contracts:1,totals:[{unit:'RUB',currency:'RUB',amount:'10.25'}],rows:[]},operations:[],history_without_time:0}}:action==='shifts'?{ok:true,data:{shifts:[],meaning:'Серверные отметки'}}:action==='shift-report'?{ok:true,data:{since:'2026-11-01',until:'2026-11-12',timezone:'UTC',meaning:'Работа за период',employees:[{employee:'Исполнитель проверки',shifts:3,declared_seconds:27000,break_seconds:1800,unclosed:1}]}}:old(method,action,...args);
    }''')
    page.locator('[data-section=cabinet]').click()
    page.get_by_text('10.25 RUB',exact=True).wait_for()
    assert 'без данных: 1' in page.locator('#cabinet-result').inner_text()
    page.screenshot(path=str(qa/'employee-cabinet.png'),full_page=True)
    page.locator('[data-section=shift]').click()
    page.get_by_text('7 ч. 30 мин.',exact=True).wait_for()
    assert 'Исполнитель проверки' in page.locator('#shift-report-result').inner_text()
    page.screenshot(path=str(qa/'team-shifts.png'),full_page=True)
    assert not errors,errors
    result=dict(passed=True,checks=['loan and issue date','responsibility','both activity results visible',
      'debt articles','script on card background','wide and narrow layout','shared read-only card hides write actions',
      'daily task links employee and contract','assignee and work kind survive reopening','monthly cabinet and missing payments','team shifts period summary'],errors=errors)
    (qa/'card-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));browser.close()
