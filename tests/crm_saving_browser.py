"""Compiled renderer: exact queue states, live cards and an undisturbed form."""
from pathlib import Path
import json,sys
from playwright.sync_api import sync_playwright

folder=Path(sys.argv[1]).resolve()
fixture=r'''() => {
const context={role:'employee',employee_uuid:'employee',owner:false,read_all_contracts:true,
 capabilities:['call','visit','correct'],fields:['soft_comment','soft_call_status','visit_comment','call_status','visit_date'].map(key=>({key,title:key,type:key==='visit_date'?'date':'text'})),dropdowns:{},staff:[]};
const card={person:{person_uuid:'person',name:'Клиент проверки'},contracts:[{contract_uuid:'contract',external_id:'КД 001',creditor_id:'Кредитор',currency:'RUB',allowed_actions:['call','visit','correct'],fields:{soft_comment:'Исходный звонок',visit_comment:'Исходный выезд',debt_today:'100.00'},revisions:{call:0,visit:0}}],entities:[],history:[]};
window.saving={card,entries:[],commands:[],listener:()=>{},denied:false,
 accept(index,status='applied'){
  const entry=this.entries[index];entry.state='accepted';entry.result={event_id:'event-'+index,operation_id:entry.body.operation_id,status:'accepted',projection_status:status};
  if(status==='applied'){Object.assign(card.contracts[0].fields,entry.body.payload.values);card.contracts[0].revisions[entry.body.kind]++;}
  card.history.unshift({event_id:'event-'+index,kind:entry.body.kind,origin:'app',received_at:new Date().toISOString(),contract_ids:['contract'],payload:entry.body.payload,projection_status:status,can_correct:false});
  this.listener({type:'queue'});
 }};
window.crm={state:async()=>({version:'saving-preview',context:saving.denied?null:context,connection:{mode:'synthetic'}}),
 onChange:listener=>{saving.listener=listener;return ()=>{}},
 api:async(method,action)=>{
  if(action==='script' && saving.delayScript)return new Promise(resolve=>{saving.releaseScript=()=>{saving.delayScript=false;resolve({ok:true,data:{}})}});
  if(action==='card' && saving.staleReads){saving.staleReads--;return {ok:true,stale:new Date().toISOString(),data:structuredClone(saving.cachedCard)}};
  return {ok:true,data:structuredClone(action==='card'?card:action==='portfolio'?{people:[{person_uuid:'person',name:card.person.name,contracts:1,currencies:{RUB:'100.00'}}],total_people:1,total_contracts:1}:{})};
 },
 draft:async(key,...args)=>{if(key==='portfolio-view'&&!args.length)return {ok:true,data:{view:'all'}};if(args.length)localStorage.setItem(key,JSON.stringify(args[0]));return {ok:true,data:JSON.parse(localStorage.getItem(key)||'null')}},
 command:async body=>{saving.commands.push(structuredClone(body));saving.entries.push({body:structuredClone(body),state:'pending',queued_at:new Date().toISOString()});saving.listener({type:'queue'});return {ok:true,queued:true}},
 queue:async()=>({ok:true,data:{entries:structuredClone(saving.entries)}}),telemetry:async()=>({ok:true}),win:()=>{}};
}'''
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True);page=browser.new_page(viewport=dict(width=1440,height=1080));errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.add_init_script('('+fixture+')()');page.goto((folder/'index.html').as_uri())
    page.locator('[data-person]').click();page.locator('[data-action=call]').click()
    page.locator('#v-soft_comment').fill('Первый звонок без связи');page.locator('#action-form').evaluate('(form)=>form.requestSubmit()')
    page.locator('[data-save-state=pending]').wait_for()
    assert 'Принято сервером' not in page.locator('#card-saves').inner_text()
    page.locator('#card-saves summary').first.click()
    assert 'Первый звонок без связи' in page.locator('#card-saves').inner_text()
    assert 'Исходный звонок' in page.locator('#card-content').inner_text()
    page.screenshot(path=str(folder/'card-pending.png'),full_page=True)
    page.locator('[data-action=call]').click();page.locator('#v-soft_comment').fill('Второй звонок во время отправки')
    page.evaluate('saving.accept(0)');page.locator('[data-save-state=accepted]').wait_for()
    assert page.locator('#dialog').evaluate('(el)=>el.open')
    assert page.locator('#v-soft_comment').input_value()=='Второй звонок во время отправки'
    page.locator('#action-form').evaluate('(form)=>form.requestSubmit()')
    page.wait_for_function('() => saving.commands.length===2')
    assert page.evaluate('saving.commands[1].base_revisions.contract.call')==0
    page.evaluate("saving.accept(1,'needs_review')")
    page.get_by_text('История принята · текущие поля требуют сверки',exact=True).wait_for()
    page.wait_for_function("() => document.querySelector('#card-content').innerText.includes('Первый звонок без связи')")
    assert 'Второй звонок во время отправки' not in page.locator('#card-content').inner_text()
    page.locator('[data-save-history]').click();page.get_by_text('Открыть историю',exact=True).wait_for()
    assert 'Второй звонок во время отправки' in page.locator('#card-content').inner_text()
    page.locator('[data-tab=overview]').click();page.locator('[data-action=visit]').click()
    page.locator('#v-visit_comment').fill('Результат выезда');page.locator('#action-form').evaluate('(form)=>form.requestSubmit()')
    page.wait_for_function('() => saving.commands.length===3');page.evaluate('saving.cachedCard=structuredClone(saving.card);saving.staleReads=1;saving.accept(2)')
    page.wait_for_function("() => document.querySelector('#connection').innerText.includes('Сохранённая копия')")
    assert 'Результат выезда' not in page.locator('#card-content').inner_text()
    page.wait_for_function("() => document.querySelector('#card-content').innerText.includes('Результат выезда')")
    assert 'Сохранённая копия' not in page.locator('#connection').inner_text()
    text=page.locator('#card-content').inner_text()
    assert 'Первый звонок без связи' in text and 'Результат выезда' in text
    page.screenshot(path=str(folder/'card-accepted.png'),full_page=True)
    page.locator('#back').click();page.evaluate('saving.delayScript=true');page.locator('[data-person]').click()
    page.wait_for_function('() => !!saving.releaseScript')
    page.locator('[data-section=help]').click();page.get_by_text('Сохранение и обмен',exact=True).wait_for()
    page.evaluate('saving.releaseScript()');page.wait_for_timeout(200)
    assert page.locator('#title').inner_text()=='Справка'
    assert not page.locator('[data-action=call]').count()
    page.locator('[data-section=portfolio]').click();page.locator('[data-person]').click()
    page.locator('[data-action=call]').click();page.locator('#v-soft_comment').fill('Неотправленный черновик')
    page.evaluate("saving.denied=true;saving.listener({type:'identity'})")
    page.wait_for_function("() => document.querySelector('#title').innerText==='Подключение'")
    assert not page.locator('#dialog').evaluate('(el)=>el.open')
    assert 'Клиент проверки' not in page.locator('#view').inner_text()
    assert errors==[],errors
    browser.close()
print(json.dumps(dict(passed=True,checks=['pending does not mean accepted','saved values remain inspectable','acknowledgement does not interrupt input','form revision frozen','review history cannot overwrite current card','accepted card refreshes automatically','stale card retries without a second business command','call and visit stay independent','identity change closes stale client form'],errors=errors)))
