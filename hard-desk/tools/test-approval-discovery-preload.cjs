require('./test-analyst-access-preload.cjs');
window.__access.session=true;
const prior=window.hard.local;
window.hard.local=async(path,body={})=>{
  if(path==='/state')return {ok:true,data:{active:'Synthetic',profiles:['Synthetic'],url:'https://synthetic.invalid',account_session:true,bound:true}};
  if(path==='/admin'&&body.path==='/admin/accounts.json')return {ok:true,data:{approver:'synthetic-admin',cutover_active:true,accounts:[
    {username:'synthetic-staff',employee:'Synthetic Employee',role:'agent',status:'active',created_by:'self-registration',devices:[{id:71,kind:'phone',label:'Synthetic iPhone',status:'pending'}]},
    {username:'synthetic-manager',employee:'Synthetic Leader',role:'manager',status:'active',devices:[{id:72,kind:'phone',label:'Synthetic manager phone',status:'pending'}]},
  ]}};
  if(path==='/admin'&&body.path==='/admin/account_approve.json'){window.__access.calls.push({path,body});return {ok:true,data:{ok:true,active:true}};}
  return prior(path,body);
};
