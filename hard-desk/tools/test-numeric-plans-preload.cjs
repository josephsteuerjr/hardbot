require('./qa-desktop-preload.cjs');
window.__norms={value:13,calls:[]};
window.hard.state=async()=>({mode:'analyst',productId:'analyst',fixedProduct:true,product:'Аналитик',version:'3.2.10',theme:'light',kit:false});
window.hard.local=async(path,body={})=>{
 let data={};
 if(path==='/state')data={active:'Synthetic',profiles:['Synthetic'],url:'https://synthetic.invalid',bound:true,account_session:true};
 else if(path==='/jobs')data=[];
 else if(path==='/fn'){
  if(body.name==='activity.FILTERS')data=[['all','Все']];
  else if(body.name==='activity.COLUMNS')data=[];
  else if(body.name==='activity.summary_text')data='Синтетический стенд';
  else if(body.name==='activity.latest_shell')data=103;
  else if(body.name==='activity.filter_counts')data={all:0};
  else data=[];
 }else if(path==='/admin'){
  window.__norms.calls.push(body);
  if(body.path==='/admin/activity')data={workers:[]};
  else if(body.path==='/admin/employees')data={plan_common:10,employees:[{employee:'Synthetic Worker',contracts:2,plan:window.__norms.value||10,plan_personal:!!window.__norms.value}]};
  else if(body.path==='/admin/employee_cities.json')data={cities:{},known:[]};
  else if(body.path==='/admin/visit_plan.json'){window.__norms.value=body.body.plan['Synthetic Worker'];data={ok:true};}
 }
 return{ok:true,data};
};
