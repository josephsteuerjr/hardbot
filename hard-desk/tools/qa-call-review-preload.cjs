process.env.HARD_QA_ROLE='security';process.env.HARD_QA_THEME='dark';
require('./qa-desktop-preload.cjs');
const original=window.hard.api;
let saved=null;
window.__reviewRequests=[];
window.hard.api=async(method,path,query,body)=>{
  if(path==='/api/control/soft')return {ok:true,status:200,data:{range:{from:'2026-10-01',to:'2026-10-10',today:'2026-10-10',preset:'30d'},offices:[],events:[{id:'call9',call_id:9,employee:'Synthetic employee',debtor:'Synthetic client '.repeat(12),rep:'SYN-A',at:'2026-10-09T10:00:00+05:00',day:'2026-10-09',title:'Звонок: Нет контакта',detail:'Synthetic reported result',flags:['late'],op:'syn-call'}]}};
  if(path==='/api/control/call_review'){
    if(method==='POST'){window.__reviewRequests.push(body);saved={...body,revision:1,checker_uid:201};}
    return {ok:true,status:200,data:{call:{id:9},review:saved,history:[],source_notice:'Synthetic source notice'}};
  }
  return original(method,path,query,body);
};
