"""Compiled UI: dual-work counts, paging, role scope and preserved filters."""
from pathlib import Path
import json,os,sys
from playwright.sync_api import sync_playwright

folder=Path(sys.argv[1]).resolve()
fixture=r'''() => {
const manager=localStorage.getItem('test-role')==='manager';
const worker={employee_uuid:'worker',name:'Сотрудник проверки',role:'employee',work_kinds:['call','visit']};
const supervisor={employee_uuid:'manager',name:'Руководитель проверки',role:'manager',work_kinds:['call','visit']};
const context={role:manager?'manager':'employee',employee_uuid:manager?'manager':'worker',owner:false,read_all_contracts:true,
capabilities:['call','visit','note','promise','reported_payment',...(manager?['analytics']:[])],staff:manager?[supervisor,worker]:[worker],
fields:[{key:'soft_comment',title:'Комментарий звонка',type:'text'},{key:'soft_call_status',title:'Результат звонка',type:'text'},{key:'soft_comm_date',title:'Дата звонка',type:'date'},{key:'call_status',title:'Результат выезда',type:'text'},{key:'visit_comment',title:'Комментарий выезда',type:'text'}],dropdowns:{soft_call_status:['Нет контакта','Обещает оплатить','Оплачен']}};
const rows=Array.from({length:469},(_,i)=>({contract_uuid:'contract-'+i,person_uuid:'person-'+i,external_id:'Q'+String(i).padStart(4,'0'),creditor_id:'registry-partner:Тестовый кредитор',currency:'RUB',client_name:'Клиент '+String(i).padStart(4,'0'),
work_kinds:i<429?['call']:i<435?['visit']:['call','visit'],fields:{partner:'Тестовый кредитор',phone:'55512345',address:'Тестовый адрес',debt_today:'10.00',soft_call_status:'Нет контакта',soft_comment:'Телефонный результат',call_status:'В работе',visit_comment:'Выездной результат'},revisions:{call:0,visit:0}}));
window.workReview={rows,queries:[],commands:[],exports:[]};
window.crm={state:async()=>({owner:context.employee_uuid,version:'queue-qa',context,connection:{mode:'synthetic'}}),
api:async(method,action,body,query={})=>{
 workReview.queries.push({action,...query});let data={};
 if(action==='work-portfolio'){
  const employee=query.employee_uuid||context.employee_uuid,empty=employee!=='worker';
  const all=empty?[]:rows;
  const counts={all:all.length,call:empty?0:463,visit:empty?0:40,both:empty?0:34,delivery:0};
  const selected=all.filter(r=>(query.work==='all'||query.work==='both'&&r.work_kinds.length===2||r.work_kinds.includes(query.work))&&(!query.search||[r.client_name,r.external_id,r.fields.phone,r.fields.address].some(v=>v.includes(query.search))));
  const offset=Number(query.offset||0),limit=Number(query.limit||100);
  data={employee_uuid:employee,employee:employee==='worker'?worker.name:supervisor.name,counts,total_contracts:selected.length,total_people:selected.length,
   contracts:structuredClone(selected.slice(offset,offset+limit)),has_more:offset+limit<selected.length,currencies:{RUB:(selected.length*10).toFixed(2)},unknown_amounts:0,
   filter_options:{partner:['Тестовый кредитор'],pool:['2','10'],call_status:['Нет контакта'],visit_status:['В работе'],debt_currency:['RUB']}};
  if(query.pool==='10') {
   const filtered=selected.filter(r=>r.contract_uuid==='contract-468');
   data={...data,total_contracts:filtered.length,total_people:filtered.length,contracts:structuredClone(filtered),
     has_more:false,currencies:{RUB:(filtered.length*10).toFixed(2)}};
  }
 }
 if(action==='portfolio')data={people:[...rows.map(r=>({person_uuid:r.person_uuid,name:r.client_name,contracts:1,currencies:{RUB:'10.00'}})),{person_uuid:'foreign',name:'Другой сотрудник · клиент',contracts:1,currencies:{RUB:'50.00'}}],total_contracts:470,total_people:470};
 if(action==='card'){
  const row=rows.find(r=>r.person_uuid===query.person_uuid);
  data={person:{person_uuid:row.person_uuid,name:row.client_name},contracts:[{...structuredClone(row),allowed_actions:[...row.work_kinds,'note','promise','reported_payment']}],history:[],entities:[]};
 }
 if(action==='manager-statistics')data={since:query.date_from||'2026-11-23',through:query.date_to||'2026-11-23',as_of:'2026-11-23T08:00:00Z',
   territories:{tj:['Тестовый город']},staff:[{...worker,country:'tj',offices:['Тестовый город'],call:1,visit:2,by_day:{'2026-11-23':2},outcomes:{'Оплачен':2}}]};
 return {ok:true,data};
},
draft:async(key,value)=>{const id=context.employee_uuid+':'+key;if(value!==undefined)localStorage.setItem(id,JSON.stringify(value));return {ok:true,data:JSON.parse(localStorage.getItem(id)||'null')}},
command:async body=>{workReview.commands.push(body);const row=rows.find(r=>r.contract_uuid===body.contract_ids[0]);Object.assign(row.fields,body.payload.values);return {ok:true}},
workbook:async (kind,spec)=>{workReview.exports.push({kind,...spec});return {ok:true}},
telemetry:async()=>({ok:true}),queue:async()=>({ok:true,data:{items:[]}}),win:()=>{}};
}'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel=os.environ.get('CRM_QA_CHROMIUM_CHANNEL'))
    page=browser.new_page(viewport=dict(width=1440,height=1040));errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.add_init_script('('+fixture+')()');page.goto((folder/'index.html').as_uri())
    page.locator('[data-work-contract]').first.wait_for()
    assert page.locator('[data-work-contract]').count()==100
    assert 'registry-partner:' not in page.locator('[data-work-contract]').first.inner_text()
    for key,count in [('all',469),('call',463),('visit',40),('both',34)]:
        assert page.locator(f'[data-work-filter={key}] span').inner_text()==str(count)
    assert '4\u00a0690 RUB' in page.locator('[data-work-total]').inner_text()
    page.locator('#portfolio-filters summary').click()
    page.locator('[name=partner]').select_option(label='Тестовый кредитор')
    page.locator('[name=pool]').select_option(label='10')
    page.locator('[name=call_status]').select_option(label='Нет контакта')
    page.locator('[name=debt_from]').fill('10,00')
    page.locator('#portfolio-filters button[type=submit]').click()
    page.wait_for_function("document.querySelector('[data-work-contract]')?.dataset.workContract==='contract-468'")
    query=page.evaluate("workReview.queries.filter(q=>q.action==='work-portfolio').at(-1)")
    assert query['partner']=='Тестовый кредитор' and query['pool']=='10' and query['call_status']=='Нет контакта' and query['debt_from']=='10.00' and query['offset']==0
    page.reload()
    page.locator('[data-work-contract=contract-468]').wait_for()
    assert page.locator('[name=pool]').input_value()=='"10"'
    page.locator('#portfolio-filters button[type=reset]').click()
    page.wait_for_function("document.querySelectorAll('[data-work-contract]').length===100")
    page.screenshot(path=str(folder/'work-portfolio-all.png'),full_page=False)
    page.locator('#work-next').click();page.wait_for_function("document.querySelector('[data-work-contract]')?.dataset.workContract==='contract-100'")
    page.locator('[data-work-filter=both]').click();page.wait_for_function("document.querySelectorAll('[data-work-contract]').length===34")
    assert '340 RUB' in page.locator('[data-work-total]').inner_text()
    page.screenshot(path=str(folder/'work-portfolio-both.png'),full_page=False)
    page.locator('[data-person]').first.click();page.locator('[data-action=call]').wait_for()
    assert page.locator('[data-action=visit]').count()==1
    page.locator('[data-action=call]').click();page.locator('#v-soft_comment').fill('Новый телефонный результат')
    page.locator('#action-form').evaluate('(form)=>form.requestSubmit()')
    page.wait_for_function("!document.querySelector('#dialog').open")
    assert page.evaluate("workReview.rows[435].fields.visit_comment")=='Выездной результат'
    assert page.evaluate("workReview.commands[0].kind")=='call'
    for status,kind in [('Оплачен','reported_payment'),('Обещает оплатить','promise')]:
        page.locator('[data-action=call]').click()
        page.locator('#v-soft_call_status').select_option(label=status)
        page.locator('#save-with-amount').click()
        page.locator('#money').fill('123,45')
        if kind=='promise':page.locator('#due').fill('2026-11-24T12:00')
        page.locator('#action-form').evaluate('(form)=>form.requestSubmit()')
        page.wait_for_function("!document.querySelector('#dialog').open")
        operation=page.evaluate('workReview.commands.at(-1)')
        assert operation['kind']==kind and operation['payload']['amount']=='123.45'
    page.locator('[data-action=note]').click();page.locator('#free-text').fill('Комментарий к клиенту')
    page.locator('#action-form').evaluate('(form)=>form.requestSubmit()')
    page.wait_for_function("!document.querySelector('#dialog').open")
    assert page.evaluate('workReview.commands.at(-1).payload.text')=='Комментарий к клиенту'
    page.locator('#back').click();page.locator('[data-work-filter=both].active').wait_for()
    page.reload();page.locator('[data-work-filter=both].active').wait_for()
    assert page.locator('[data-work-contract]').count()==34
    page.locator('#search').fill('Q0468');page.locator('#search').press('Enter')
    page.wait_for_function("document.querySelectorAll('[data-work-contract]').length===1")
    assert page.locator('[data-work-contract]').get_attribute('data-work-contract')=='contract-468'
    page.locator('[data-portfolio-view=all]').click();page.get_by_text('Другой сотрудник · клиент',exact=True).wait_for()
    page.evaluate("localStorage.setItem('test-role','manager')");page.reload()
    page.locator('[data-portfolio-view=mine]').click();page.locator('#portfolio-employee').select_option('worker')
    page.wait_for_function("document.querySelectorAll('[data-work-contract]').length===100")
    page.locator('[data-work-filter=both]').click();page.wait_for_function("document.querySelectorAll('[data-work-contract]').length===34")
    assert page.evaluate('workReview.commands.length')==0
    page.screenshot(path=str(folder/'work-portfolio-manager.png'),full_page=False)
    page.get_by_role('button',name='Команда',exact=True).click()
    page.locator('#results-period').select_option('custom')
    page.locator('#results-dates [name=date_from]').fill('2026-11-22')
    page.locator('#results-dates [name=date_to]').fill('2026-11-23')
    page.locator('#results-dates button').click()
    page.wait_for_function("document.querySelector('#results-status')?.textContent.includes('22.11.2026')")
    page.locator('#results-export').click()
    assert page.evaluate('workReview.exports[0].name').endswith('2026-11-22–2026-11-23.xlsx')
    assert errors==[],errors
    browser.close()
print(json.dumps(dict(passed=True,checks=['469 unique contracts','463 calls/40 visits/34 both','debt once','paging','filter before paging','two independent results','filter restored after card and restart','search','common clients remain visible','manager selects employee without acting as them'],errors=errors)))
