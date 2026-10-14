const today=new Date(Date.now()+5*3600000).toISOString().slice(0,10);
const options=[{value:'Нет контакта',label:'Нет контакта'},{value:'Обещает оплатить',label:'Обещает оплатить'}];
const person={rep:'SYN-DRAFT',client:'Synthetic client with a long name '.repeat(4),n:1,visited:false,totals:[{sum:1000,unit:'TJS'}],tels:[],ids:['SYN-DRAFT']};
const card={...person,debt:[],promise:[],contracts:[{contract_id:'SYN-DRAFT',partner:'Тест',unit:'TJS',total:1000,debt:[]}],form:[],info:[],history:[],calls:[],photos:0,receipts:[],reported_payments:[],geo:null,
  soft:{status:'Нет контакта',comment_entries:[],next_call_date:''},softFormTyped:{},formTyped:{}};
localStorage.setItem('app_token','TOK-SYNTHETIC-aaaaaaaaaaaaaaaaaaaa');
window.__errors=[];
window.addEventListener('error',e=>window.__errors.push(e.message));
window.addEventListener('unhandledrejection',e=>window.__errors.push(String(e.reason)));
window.fetch=async input=>{
  const url=String(input);let data={ok:true};
  if(url.startsWith('/version.json'))data={shellVersion:0};
  else if(url.startsWith('/api/bootstrap'))data={ok:true,uid:101,employee:'Synthetic employee',kind:'agent',lang:'ru',persons:[person],roles:{soft:true,field:false},today,
    meta:{today,formProtocolVersion:3,dropdowns:{soft_call_status:options},typedFields:[{key:'promise_date',kind:'date',title:'Дата обещания'},{key:'forecast_sum',kind:'number',title:'Сумма обещания'}]}};
  else if(url.startsWith('/api/person'))data=card;
  else if(url.startsWith('/api/cards'))data={cards:{'SYN-DRAFT':card}};
  return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
};
