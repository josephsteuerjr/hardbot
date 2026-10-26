require('./qa-desktop-preload.cjs');
const previous=window.hard.api;
window.__reportQA={requests:[],downloads:[],fail:false};
window.hard.api=async(method,path,query,body)=>{
  if(path!=='/api/manager/promise_report')return previous(method,path,query,body);
  window.__reportQA.requests.push({method,path,query});
  if(window.__reportQA.fail)return {ok:false,status:503,text:'Synthetic report unavailable'};
  return {ok:true,status:200,stale:window.__reportQA.stale,data:{totals:{ptp:23,kept:0,partial:2,waiting:21,unpaid:0,incomplete:0,kp_rate:0},
    payment_data:{last_import_at:'2026-10-21T21:28:00+00:00'},open_deadlines:true}};
};
window.hard.download=async(path,query,name)=>{
  window.__reportQA.downloads.push({path,query,name});return {ok:true};
};
