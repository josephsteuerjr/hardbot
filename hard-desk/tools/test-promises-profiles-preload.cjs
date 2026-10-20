require('./qa-desktop-preload.cjs');
const previous = window.hard.api;
window.__promisesQA = { failSoft: false, requests: [] };
window.hard.api = async (method, path, query, body) => {
  if (path !== '/api/manager/promises') return previous(method, path, query, body);
  window.__promisesQA.requests.push({method, path, query});
  const soft = query?.mode === 'soft';
  if (soft && window.__promisesQA.failSoft) return {ok:false,status:503,text:'Synthetic Soft unavailable'};
  const items = soft ? Array.from({length:55},(_,i)=>({rep:'SYNTH-'+i,client:'Synthetic Soft '+i,
    promise_date:'2026-10-01',days_overdue:8,money_text:'100 UZS',visited_after_promise:i<20})) : [];
  return {ok:true,status:200,data:{screen:'promises',profile:soft?'soft':'hard',today:'2026-10-09',
    totals:{persons:items.length,worked:soft?20:0,untouched:soft?35:0,deferred:soft?33:18},
    employees:soft?[{employee:'Synthetic operator',persons:55,items}]:[]}};
};
