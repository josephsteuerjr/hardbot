const profile = process.argv.find(x=>x.startsWith('--qa-profile='))?.split('=')[1] || 'hard';
const today = new Date(Date.now()+5*3600000).toISOString().slice(0,10);
window.__requests=[];window.__errors=[];window.__offline=false;window.__drafts={};window.__prefs={};window.__exports=[];window.__delayCard=false;
window.addEventListener('error',e=>window.__errors.push(String(e.message)));
window.addEventListener('unhandledrejection',e=>window.__errors.push(String(e.reason)));
const persons=[
 {rep:'00001',client:'Синтетический должник с длинным двойным именем',address:'Тестовый город, длинная улица, дом 18, корпус 2, квартира 104',city:'Тестовый город',n:2,total:13200.50,totals:[{sum:13200.50,unit:'RUB'}],ids:['00001','00002'],tels:['00000000001'],partners:['Тестовый контрагент'],sc:'Нет контакта',scd:today,office:{next_date:today,contacts:[],promises:[],last:null}},
 {rep:'00003',client:'Синтетический должник Б',address:'Тестовый адрес 2',city:'Другой город',n:1,total:2500,totals:[{sum:2500,unit:'TJS'}],ids:['00003'],tels:['00000000003'],partners:['Другой контрагент'],sc:'Встреча в офисе',scd:today,office:{next_date:'',contacts:[],promises:[],last:null}},
 {rep:'00004',client:'Синтетический должник В',n:1,total:100000,totals:[{sum:100000,unit:'UZS'}],ids:['00004'],tels:[],partners:['Тестовый контрагент'],sc:'В работе',scd:'2099-01-01',office:{next_date:'2099-01-01',contacts:[],promises:[],last:null}},
];
window.__persons=persons;window.__events=[];
const card=rep=>{const person=persons.find(p=>p.rep===rep)||persons[0];return {...person,phones:person.tels,phone:person.tels[0]||'',extra_contact:'Тестовый дополнительный контакт',visited:false,status:'Не обработан',
 contracts:person.ids.map((id,i)=>({contract_id:id,partner:person.partners[0],unit:person.totals[0].unit,total:person.total/person.n,debt:[{title:'Основной долг',value:person.total/person.n}]})),
 info:[{title:'Дата назначения',value:today}],promise:[],payments:person.ids.map(id=>({contract_id:id,unit:person.totals[0].unit,current:0,amount:'0',date:today})),receipts:[],reported_payments:[],visit_comment:'Существующий комментарий месяца',
 soft:{status:person.sc,next_call_date:person.scd,comment_entries:['Предыдущая заметка сотрудника'],extra_phone:'Тестовый дополнительный номер'},softFormTyped:{},calls:[],
 office:{...person.office,actions:{contact:'Контакт состоялся',attempt:'Попытка контакта',information:'Получена информация',note:'Заметка',promise:'Обещание оплаты',payment:'Сообщена оплата',cancel_promise:'Отмена офисного обещания'},choices:{result:['В работе','Нет контакта','Обещает оплатить'],contact_with:['Должник','3 лицо'],client_location:['Клиент в РФ','Клиент на родине'],agreed_payment:['Обещает оплатить клиент','На оплату договориться не удалось']},revision:'revision-'+window.__events.length,history:window.__events.filter(e=>e.rep===rep).slice().reverse(),history_count:window.__events.filter(e=>e.rep===rep).length,addresses:[]}}};
window.hard={
 state:async()=>({mode:'field',productId:profile,fixedProduct:true,product:profile==='soft'?'Софт':'Хард',version:'3.1.0',enrolled:true,theme:'light',deviceName:'Синтетика'}),
 api:async(method,path,query,body)=>{
  window.__requests.push({method,path,query,body});
  if(path==='/api/bootstrap')return {ok:true,status:200,data:{uid:101,employee:'Тестовый сотрудник',today,persons,roles:{soft:true,field:true},meta:{formProtocolVersion:3,promiseMaxDays:7,dropdowns:{soft_call_status:['Нет контакта','Встреча в офисе','Обещает оплатить','В работе'].map(value=>({value,label:value}))}}}};
  if(path==='/api/person'){if(window.__delayCard&&query.rep==='00001')await new Promise(r=>setTimeout(r,200));return {ok:true,status:200,data:card(query.rep)}};
  if(path==='/api/local/draft'){const key=(query?.profile||body?.profile||'soft')+':'+(query?.rep||body?.rep);if(method==='POST')window.__drafts[key]=body.draft;return {ok:true,status:200,data:{draft:window.__drafts[key]||null}}};
  if(path==='/api/local/preferences'){const key=(query?.profile||body?.profile)+':'+(query?.id||body?.id);if(method==='POST')window.__prefs[key]=body.value;return {ok:true,status:200,data:{value:window.__prefs[key]||null}}};
  if(path==='/api/local/call_queue')return {ok:true,status:200,data:{pending:0,errors:[]}};
  if(path==='/api/workspace/promises')return {ok:true,status:200,data:{today,items:[]}};
  if(path==='/api/workspace/report')return {ok:true,status:200,data:{ok:true,employee:'Тестовый сотрудник',profile,today,from:query.from||today,to:query.to||today,portfolio_persons:3,portfolio_contracts:4,money:persons.flatMap(p=>p.totals),promises:[],events:window.__events,visits:[],stats:profile==='soft'?{calls:4,persons:3,rpc:2,ptp:1,kp:0,rpc_rate:.5,ptp_rate:.5,kp_rate:0,operation:{partial:1,incomplete:0,ptp_money:[{sum:100,unit:'RUB'}],kp_money:[],promises:[]}}:{office:window.__events.length,persons:1,visits:0,promised:0}}};
  if(path==='/api/office/action'){
    if(window.__conflict)return {ok:false,status:409,text:'Карточка изменилась. Обновите её и сверьте ввод'};
    if(window.__offline)return {ok:true,status:200,data:{queued:true,op_id:body.op_id}};
    const event={op_id:body.op_id,rep:body.rep,client:persons.find(p=>p.rep===body.rep)?.client,day:today,at:new Date(body.occurred_at).toISOString(),action:body.action,title:card(body.rep).office.actions[body.action],values:body.values,contracts:card(body.rep).ids,employee:'Тестовый сотрудник',source:'Офис Харда'};
    if(!window.__events.some(e=>e.op_id===body.op_id))window.__events.push(event);
    return {ok:true,status:200,data:{ok:true,event,revision:'revision-'+window.__events.length}};
  }
  if(path==='/api/save'){
    if(window.__conflict && body.kind==='call')return {ok:false,status:409,text:'Карточка изменилась. Обновите её и сверьте ввод'};
    if(!window.__offline && body.kind==='call' && !window.__events.some(e=>e.op_id===body.op_id))window.__events.push({id:window.__events.length+1,op_id:body.op_id,rep:body.rep,client:persons.find(p=>p.rep===body.rep)?.client,day:today,title:body.values.soft_call_status,note:body.values.soft_comment,values:body.values,source:'Звонок Софта'});
    return {ok:true,status:200,data:window.__offline?{queued:true}:{ok:true}}
  }
  return {ok:false,status:404,text:'Не предусмотрено синтетическим стендом'};
 },
 update:async()=>({phase:'idle',text:'Тестовая версия'}),copy:async()=>true,on:()=>{},win:()=>{},theme:async()=>true,
 pick:async()=>'',autostart:async()=>false,xlsx:async(name,headers,rows)=>{window.__exports.push({name,headers,rows});return {ok:true}},open:async()=>'',logout:async()=>true,
};
