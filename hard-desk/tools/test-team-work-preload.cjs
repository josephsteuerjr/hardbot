// Synthetic transport for renderer QA. No real profile, server or employee data.
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.error || e.message)));
window.addEventListener('unhandledrejection', e => window.__errors.push(String(e.reason)));
window.__kind = 'manager';
window.__requests = [];
window.__people = [
  {employee:'Тестов Первый', employee_id:'synthetic-tj', direction:'tj', office:'Душанбе', products:['hard'], active:1, username:'test.tj', account_id:11, login_status:'active', desktop:'active', phone:'pending', last_seen:'2026-10-26T12:00:00Z', can_remove:true},
  {employee:'Образцов Второй', employee_id:'synthetic-uz', direction:'uz', office:'Ташкент', products:['soft'], active:1, username:'test.uz', account_id:12, login_status:'awaiting', desktop:'pending', phone:'none', can_remove:true},
  {employee:'Проверочный Третий', employee_id:'synthetic-new', direction:'tj', office:'Худжанд', products:['hard','soft'], active:0, username:'', account_id:null, login_status:'none', desktop:'none', phone:'none', can_remove:true}
];
window.__tasks = [];
let sequence = 0;
async function request(method, path, query, body) {
  window.__requests.push({method,path,query,body});
  const board = path.includes('employee_board');
  if (method === 'GET') {
    if (board) return {ok:true, employees:window.__people.map(x=>({...x,on_board:x.on_board ?? !!x.active})), can_edit:true};
    const id = query?.id || Number(new URL('https://synthetic.invalid'+path).searchParams.get('id'));
    if (id) {
      const task = structuredClone(window.__tasks.find(x=>x.id === Number(id)));
      task.can_manage = window.__kind === 'manager'; task.can_work = window.__kind === 'employee';
      task.can_contribute = window.__kind !== 'analyst' && task.status !== 'confirmed';
      return {ok:true,task};
    }
    return {ok:true,tasks:window.__tasks,can_create:window.__kind==='manager'};
  }
  if (board) {
    const row = window.__people.find(x=>x.employee===body.employee);
    row.on_board=body.action==='add';
    if(body.action==='add')row.active=1;
    if(body.action==='remove'){row.active=0;row.username='';row.account_id=null;row.login_status='none';row.desktop=row.phone='none';}
    return {ok:true};
  }
  let task;
  if(body.action==='create') {
    task={...body,id:++sequence,creator:'Тестовый руководитель',revision:1,status:'open',steps:[],events:[],overdue:false};
    window.__tasks.push(task);
  } else {
    task=window.__tasks.find(x=>x.id===body.id); task.revision++;
    if(body.action==='step')task.steps.push({id:++sequence,text:body.text,done:false,author:window.__kind});
    if(body.action==='toggle_step')task.steps.find(x=>x.id===body.step_id).done=body.done;
    if(body.action==='submit')task.status='submitted';
    if(body.action==='return')task.status='open';
    if(body.action==='confirm')task.status='confirmed';
  }
  task.events.push({kind:body.action,text:body.text||body.title||'',author:window.__kind==='employee'?'Тестов Первый':'Тестовый руководитель',at:'2026-10-26T12:00:00Z'});
  return {ok:true,id:task.id};
}
window.hard={win(){},on(){},
  async api(method,path,query,body){return {ok:true,status:200,data:await request(method,path,query,body)};},
  async local(path, arg){return {ok:true,data:await request(arg.method,arg.path,undefined,arg.body)};}
};
