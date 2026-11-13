"""Compiled task workspace, navigation and honest queued completion state."""
from pathlib import Path
import json,sys
from playwright.sync_api import sync_playwright

folder=Path(sys.argv[1]).resolve()
fixture=r'''() => {
const employee=localStorage.getItem('test-role')==='employee';
const worker={employee_uuid:'worker',name:'Сотрудник проверки',role:'employee',work_kinds:['call','visit']};
const manager={employee_uuid:'manager',name:'Руководитель проверки',role:'manager',work_kinds:['call','visit']};
const context={role:employee?'employee':'manager',employee_uuid:employee?'worker':'manager',owner:false,read_all_contracts:true,
 capabilities:['task','call','visit'],staff:employee?[worker]:[manager,worker],timezone:'Asia/Tashkent',fields:[],dropdowns:{}};
const base={contract_uuid:'contract',person_uuid:'person',client:'Клиент проверки',external_id:'КД 001',creditor:'Тестовый кредитор',revision:1,due_at:'2026-11-13T17:00:00Z',assignee_uuid:'worker',assignee:worker.name,can_complete:true};
const rows=[{...base,entity_id:'task-today',title:'Позвонить клиенту',work_kind:'call',status:'open',due_today:true,overdue:false},
 {...base,entity_id:'task-overdue',title:'Проверить адрес',work_kind:'visit',status:'open',due_today:false,overdue:true},
 {...base,entity_id:'task-done',title:'Закрытая задача руководителя',work_kind:'call',status:'done',assignee_uuid:'manager',assignee:manager.name,can_complete:false}];
window.taskReview={rows,entries:[],commands:[]};
window.crm={state:async()=>({version:'workflow-preview',context,connection:{mode:'synthetic'},offline:false}),
 api:async(method,action,body,query={})=>{
 let data={};
 if(action==='tasks'){
  const scoped=rows.filter(r=>(query.employee_uuid==='all'||r.assignee_uuid===query.employee_uuid) &&
   (query.work==='all'||r.work_kind===query.work) && (!query.search||r.title.includes(query.search)||r.client.includes(query.search)));
  const counts={all:scoped.length,open:scoped.filter(r=>r.status==='open').length,done:scoped.filter(r=>r.status==='done').length,
   today:scoped.filter(r=>r.status==='open'&&r.due_today).length,overdue:scoped.filter(r=>r.status==='open'&&r.overdue).length};
  const selected=scoped.filter(r=>query.status==='all'||query.status==='today'&&r.status==='open'&&r.due_today||query.status==='overdue'&&r.status==='open'&&r.overdue||r.status===query.status);
  data={tasks:structuredClone(selected),counts,total:selected.length,has_more:false,timezone:'Asia/Tashkent'};
 }
 if(action==='portfolio')data={people:[{person_uuid:'person',name:'Клиент проверки',contracts:1,currencies:{RUB:'100.00'}}],total_people:1,total_contracts:1};
 if(action==='work-portfolio')data={employee_uuid:'worker',employee:worker.name,counts:{all:1,call:1,visit:1,both:1},contracts:[{contract_uuid:'contract',person_uuid:'person',client_name:'Клиент проверки',external_id:'КД 001',work_kinds:['call','visit'],currency:'RUB',fields:{}}],currencies:{RUB:'100.00'},total_contracts:1,total_people:1,has_more:false};
 if(action==='card')data={person:{person_uuid:'person',name:'Клиент проверки'},contracts:[{contract_uuid:'contract',external_id:'КД 001',creditor_id:'Тестовый кредитор',currency:'RUB',fields:{},revisions:{},allowed_actions:['task','call','visit']}],history:[],entities:[]};
 return {ok:true,data};
 },
 draft:async(key,...args)=>{const name=context.employee_uuid+':'+key;if(args.length)localStorage.setItem(name,JSON.stringify(args[0]));return {ok:true,data:JSON.parse(localStorage.getItem(name)||'null')}},
 command:async body=>{taskReview.commands.push(structuredClone(body));taskReview.entries.push({action:'command',body:structuredClone(body),state:'pending',queued_at:new Date().toISOString()});return {ok:true,queued:true}},
 queue:async()=>({ok:true,data:{entries:structuredClone(taskReview.entries)}}),telemetry:async()=>({ok:true}),win:()=>{}};
}'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport=dict(width=1440,height=1000));errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.add_init_script('('+fixture+')()');page.goto((folder/'index.html').as_uri())
    page.locator('[data-section=tasks]').click();page.locator('[data-task-row]').first.wait_for()
    assert page.locator('[data-task-row]').count()==2
    page.locator('[data-task-filter=overdue]').click();page.wait_for_function("document.querySelectorAll('[data-task-row]').length===1")
    page.locator('[data-task-person]').click();page.locator('#back').wait_for()
    assert page.locator('#back').inner_text()=='← Задачи'
    page.locator('#back').click();page.locator('[data-task-filter=overdue].active').wait_for()
    page.locator('[data-task-complete]').click()
    page.get_by_text('Ожидает подтверждения',exact=True).wait_for()
    assert page.locator('[data-task-complete]').count()==0
    assert page.evaluate('taskReview.commands[0].kind')=='task_complete'
    assert page.evaluate('taskReview.rows[1].status')=='open'
    page.evaluate("taskReview.entries[0].state='attention'");page.locator('#task-refresh').click()
    page.get_by_text('Требует сверки',exact=True).first.wait_for()
    assert page.locator('[data-task-complete]').count()==0
    page.evaluate("taskReview.entries[0].state='accepted';taskReview.rows[1].status='done';taskReview.rows[1].can_complete=false")
    page.locator('[data-task-filter=done]').click();page.wait_for_function("document.querySelectorAll('[data-task-row]').length===2")
    page.screenshot(path=str(folder/'tasks-manager.png'),full_page=True)
    page.locator('[data-task-filter=open]').click();page.locator('#task-employee').select_option('worker')
    page.locator('#task-create').click();page.locator('[data-person]').click()
    page.locator('#task-assignee').wait_for();assert page.locator('#task-assignee').input_value()=='worker'
    page.locator('#free-text').fill('Новая задача по клиенту')
    page.locator('#action-form').evaluate('(form)=>form.requestSubmit()')
    page.locator('#task-send-state').get_by_text('Новая задача по клиенту',exact=False).wait_for()
    assert page.evaluate('taskReview.commands[1].payload.assignee_uuid')=='worker'
    assert page.evaluate('taskReview.commands[1].contract_ids[0]')=='contract'
    assert page.locator('[data-task-row]').count()==1
    page.screenshot(path=str(folder/'tasks-pending.png'),full_page=True)
    page.evaluate("localStorage.setItem('test-role','employee')");page.reload()
    page.locator('[data-section=tasks]').click();page.locator('[data-task-row]').first.wait_for()
    assert page.locator('#task-employee').count()==0
    assert 'Закрытая задача руководителя' not in page.locator('#view').inner_text()
    page.screenshot(path=str(folder/'tasks-employee.png'),full_page=True)
    assert errors==[],errors
    browser.close()
print(json.dumps(dict(passed=True,checks=['role-specific task list','overdue filters','card and return','pending completion remains open','review state does not duplicate completion','confirmed completion','manager assigns through client chooser','queued new task is separate from server facts','employee sees own tasks'],errors=errors)))
