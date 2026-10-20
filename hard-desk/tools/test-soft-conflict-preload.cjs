require('./qa-soft-preload.cjs');
const base=window.hard.api;let revision='revision-1';
window.__conflict=false;window.__originals=Array.from({length:29},(_,i)=>({rep:'SYN-A',op_id:'failed-'+i,error:'Сервер ответил ошибкой 409: Карточка изменилась. Обновите её и сверьте ввод перед повтором',body:{rep:'SYN-A',kind:'call',values:{soft_call_status:'Нет контакта',soft_comment:'Исходник '+i}}}));
let draft=null;
window.hard.api=async(method,path,query,body)=>{
 if(path==='/api/local/call_queue')return {ok:true,status:200,data:{pending:29,errors:window.__originals}};
 if(path==='/api/local/draft'){if(method==='POST')draft=body.draft;return {ok:true,status:200,data:{draft}};}
 if(path==='/api/person'){const r=await base(method,path,query,body);r.data.office={revision};return r;}
 if(path==='/api/save'&&window.__conflict){window.__requests.push({method,path,query,body});revision='revision-2';return {ok:false,status:409,kind:'server',text:'Карточка изменилась. Обновите её и сверьте ввод перед повтором'};}
 return base(method,path,query,body);
};
