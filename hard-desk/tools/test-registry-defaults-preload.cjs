window.__errors=[];
window.__requests=[];
window.addEventListener('error',e=>window.__errors.push(String(e.error||e.message)));
const book={caption:'Synthetic registry',profile:'both',employees:[{key:'synthetic',name:'Synthetic employee',note:'Synthetic counts',file_count:3,db_count:2,in_db:true}],selected:['synthetic']};
let result=book;
window.hard={win(){},on(){},pick:async()=> 'C:/synthetic/registry.xlsb',local:async(path,body={})=>{
 window.__requests.push({path,body});
 if(path==='/reg/pick'){result={...book};return {ok:true,data:{job:'synthetic-pick'}};}
 if(path==='/reg/state')return {ok:true,data:book};
 if(path==='/enr/operation')return {ok:true,data:{}};
 if(path==='/reg/select'){book.profile=body.profile||book.profile;return {ok:true,data:{...book}};}
 if(path==='/reg/preview'){result={pairs:[['rows',3]],notes:[],apply:'Apply',unassigned:0};return {ok:true,data:{job:'synthetic-preview'}};}
 if(path==='/reg/apply'){
  const job={id:body.request_id,kind:'apply',started:Date.now()/1000,state:'running',log:['Сервер обрабатывает импорт. Жду подтверждение записи.']};
  sessionStorage.setItem('import-receipt',JSON.stringify(job));return {ok:true,data:{job:job.id}};
 }
 const receipt=JSON.parse(sessionStorage.getItem('import-receipt'));
 if(path==='/jobs')return {ok:true,data:receipt?[receipt]:[]};
 if(path==='/job'&&receipt?.id===body.id){
  if(window.__finishImport){
   receipt.state='done';receipt.result={pairs:[],notes:[],toast:'Импорт готов: +0 / обновлено 3 / снято 1'};
   sessionStorage.setItem('import-receipt',JSON.stringify(receipt));
  }
  return {ok:true,data:receipt};
 }
 if(path==='/job')return {ok:true,data:{state:'done',log:[],result,error:null}};
 throw Error('Unexpected fixture route '+path);
}};
