require('./qa-desktop-preload.cjs');
window.__access={calls:[],files:[],session:false};
window.hard.state=async()=>({mode:'analyst',fixedProduct:true,productId:'analyst',product:'Взыскание СНГ — Аналитик',theme:'light',version:'3.2.0',kit:false});
window.hard.saveText=async(name,text)=>{window.__access.files.push({name,text});return{ok:true};};
window.hard.local=async(path,body={})=>{
  window.__access.calls.push({path,body});let data;
  if(path==='/state')data={active:'Synthetic',profiles:['Synthetic'],url:'https://synthetic.invalid',account_session:window.__access.session,bound:true};
  else if(path==='/jobs')data=[];
  else if(path==='/account/root_probe')data={host_key:'SHA256:synthetic-host-key'};
  else if(path==='/account/root_access_check')data={effective_uid:0,authority:'sudo',accounts_changed:false};
  else if(path==='/account/root_bootstrap')data={username:body.username,operation_id:body.operation_id,status:'active'};
  else if(path==='/account/login'){window.__access.session=true;data={username:body.username};}
  else if(path==='/account/password')data={ok:true};
  else if(path==='/admin'){
    if(body.path==='/admin/devices.json')data={devices:[]};
    else if(body.path==='/admin/accounts.json')data={accounts:[],approver:'synthetic-real-person',cutover_active:true};
    else if(body.path==='/admin/staff_roster.json')data=body.method==='GET'?{employees:[],managers:[{telegram_id:101,name:'Synthetic Leader',role:'manager'}]}:
      {ok:true,rows:body.body.items.length,current_rows:0,sha256:'synthetic-preview'};
    else if(body.path==='/admin/account_action.json')data={ok:true,employee:'Synthetic Leader',temporary_password:'Syn'+'thetic-issued-456!'};
    else throw Error('Unexpected admin call '+body.path);
  }else if(path==='/fn')data=[];
  else throw Error('Unexpected local call '+path);
  return{ok:true,data};
};
