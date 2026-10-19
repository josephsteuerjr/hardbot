// Synthetic renderer contract. No network, installed profile or real account.
require('./qa-desktop-preload.cjs');
const priorApi = window.hard.api;
const legacy = process.env.HARD_MANAGER_QA_LEGACY === '1';
window.__account = { enrolled: legacy, stage: legacy ? 'approved' : 'entry', role: 'manager', errors: window.__errors,
  requests: [], rejectRequest: false, shortExpiry: false };
window.hard.state = async () => ({ mode: 'manager', productId: 'manager', fixedProduct: true, product: 'Хард — Руководитель',
  version: '3.2.0', enrolled: window.__account.enrolled, theme: 'light', deviceName: 'Стенд', kit: false,
  server: 'https://synthetic.invalid', device: window.__account.enrolled ? { name: 'Стенд', role: window.__account.role,
    ...(legacy ? {} : { username: 'synthetic' }) } : null });
window.hard.accountManagerLogin = async (server, username, password) => {
  window.__account.requests.push({ server, username, password });
  window.__account.enrolled = true;
  window.__account.stage = 'pending';
  return { ok: true, status: 200, data: { action: 'ok_pending', role: 'manager', username, must_change_password: true } };
};
window.hard.api = async (method, path, query, body) => {
  if (path === '/api/account/password') {
    window.__account.requests.push({ path, body });
    return { ok: true, status: 200, data: { ok: true } };
  }
  if (path === '/api/manager/device/me') {
    if (window.__account.stage === 'pending') return { ok: false, status: 403, kind: 'forbidden', text: 'account_pending' };
    return { ok: true, status: 200, data: { caps: { control: false, team_links: true }, me: { name: 'Синтетический руководитель', role: window.__account.role, scope: 'Стенд' } } };
  }
  if (path === '/api/manager/app_links') return { ok: true, status: 200, data: { me: null, links: [] } };
  if(path==='/api/manager/entered')return {ok:true,status:200,data:{entered:[
    {username:'synthetic-staff',employee:'Сотрудник команды',office:'Стенд',can_approve:true,devices:[
      {id:91,kind:'desktop',label:'Рабочий ПК',status:window.__account.pcApproved?'active':'pending'},
      {id:94,kind:'phone',label:'Телефон без GPS',gps_present:false,status:window.__account.phoneApproved?'active':'pending'}]},
    {username:'foreign',employee:'Чужая команда',can_approve:false,devices:[{id:92,kind:'desktop',status:'pending'}]},
    {username:'synthetic',employee:'Сам руководитель',can_approve:false,devices:[{id:93,kind:'desktop',status:'pending'}]}
  ]}};
  if(path==='/api/manager/account_approve'){
    window.__account.requests.push({path,method,body});
    if(body.device_id===91 && body.observed_pc===true)window.__account.pcApproved=true;
    else if(body.device_id===94 && !('gps_exception_reason' in body) && !('gps_exception_minutes' in body))window.__account.phoneApproved=true;
    else throw Error('Unexpected approval payload: '+JSON.stringify(body));
    return {ok:true,status:200,data:{ok:true,active:true}};
  }
  return priorApi(method, path, query, body);
};
window.hard.logout = async () => { window.__account.enrolled = false; return true; };
