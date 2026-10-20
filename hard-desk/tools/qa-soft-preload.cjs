const today=new Date(Date.now()+5*3600000).toISOString().slice(0,10);
const persons=[{rep:'SYN-A',client:'Синтетический клиент А',total:1000,n:2,sc:'Нет контакта',scd:today,
  partners:['Альфа','Бета'],pools:['Пул 1','Пул 2'],pool_partners:[{partner:'Альфа',pool:'Пул 1'},{partner:'Бета',pool:'Пул 2'}],tels:['000000001'],totals:[{sum:1000,unit:'TJS'}]},
  {rep:'SYN-B',client:'Синтетический клиент Б',total:500,n:1,partners:['Альфа'],pools:['Пул 2'],pool_partners:[{partner:'Альфа',pool:'Пул 2'}],tels:[],totals:[{sum:500,unit:'TJS'}]},
  {rep:'SYN-C',client:'Синтетический клиент В',total:250,n:1,sc:'Нет контакта',scd:'2099-01-01',partners:['Альфа'],pools:['Пул 10'],pool_partners:[{partner:'Альфа',pool:'Пул 10'}],tels:[],totals:[{sum:250,unit:'TJS'}]}];
window.__requests=[];window.__errors=[];window.__offline=false;
window.addEventListener('error',e=>window.__errors.push(String(e.message)));
window.addEventListener('unhandledrejection',e=>window.__errors.push(String(e.reason)));
window.hard={
  state:async()=>({mode:'field',productId:'soft',fixedProduct:true,product:'Софт',version:'3.0.0',enrolled:true,theme:'light',deviceName:'Синтетика'}),
  api:async(method,path,query,body)=>{
    window.__requests.push({method,path,query,body});
    if(path==='/api/local/preferences'){
      const key='soft-qa-pref:'+String(query?.id||body?.id||'');
      if(method==='POST'){localStorage.setItem(key,JSON.stringify(body.value));return {ok:true,status:200};}
      return {ok:true,status:200,data:{value:JSON.parse(localStorage.getItem(key)||'null')}};
    }
    if(path==='/api/bootstrap') return {ok:true,status:200,data:{uid:101,employee:'Синтетический оператор',today,roles:{soft:true,field:false},persons,meta:{formProtocolVersion:3,dropdowns:{soft_call_status:[{value:'Нет контакта',label:'Нет контакта'},{value:'Встреча в офисе',label:'Встреча в офисе'}]}}}};
    if(path==='/api/local/call_queue') return {ok:true,status:200,data:{pending:0,errors:[]}};
    if(path==='/api/workspace/promises') return {ok:true,status:200,data:{items:[]}};
    if(path==='/api/person') return {ok:true,status:200,data:{rep:query.rep,client:'Синтетический клиент А',address:'Тестовый адрес',phone:'000000001',phones:['000000001'],total:1000,totals:[{sum:1000,unit:'TJS'}],
      contracts:[{contract_id:'SYN-A',partner:'Альфа',unit:'TJS',total:400},{contract_id:'SYN-A2',partner:'Бета',unit:'TJS',total:600}],soft:{status:'Нет контакта',comment_entries:['Тестовый предыдущий комментарий']},calls:[],receipts:[],reported_payments:[]}};
    if(path==='/api/save')return {ok:true,status:200,data:window.__offline?{queued:true}:{ok:true}};
    return {ok:false,status:404,text:'Не предусмотрено стендом'};
  },
  update:async()=>({phase:'idle',text:'Тестовая версия'}),copy:async()=>true,on:()=>{},win:()=>{},theme:async()=>true,
  pick:async()=>'',autostart:async()=>false,xlsx:async()=>({ok:true}),open:async()=>'',logout:async()=>true,
};
