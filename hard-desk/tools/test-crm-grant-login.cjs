// Real native grant_required flow and explicit workspace selection; synthetic only.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {app,BrowserWindow,net,dialog}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT;assert(qa,'Explicit fresh QA root required');
const desk=path.resolve(__dirname,'..'),employee='33333333-3333-4333-8333-333333333333',credential='synthetic-manager-credential-for-grant-login-0123456789';
fs.mkdirSync(qa,{recursive:true});
process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);process.env.LOCALAPPDATA=path.join(qa,'local-app-data');
const profile=path.join(process.env.LOCALAPPDATA,'Hard','manager','data');fs.mkdirSync(profile,{recursive:true});
const protect=value=>execFileSync(path.join(desk,'installer/bin/process-check.exe'),['seal'],{input:Buffer.from(value),windowsHide:true});
fs.writeFileSync(path.join(profile,'settings.json'),JSON.stringify({server:'https://hard.rimborso.ru:8080'}));
fs.writeFileSync(path.join(profile,'device.key'),protect(credential));
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'))).version;
const grants=[
  {grant_id:'grant-a',actor_uid:303,role:'manager',territory:'self',capabilities:['call','note'],is_default:false},
  {grant_id:'grant-b',actor_uid:303,role:'manager',territory:'team',capabilities:['call','note'],is_default:false},
];
let selected='',offline=false;
const realFetch=net.fetch.bind(net);
net.fetch=async(url,init={})=>{
  if(String(url).startsWith('https:')){
    assert(String(url).startsWith('https://hard.rimborso.ru/crm-pilot/'));
    const route=new URL(url).pathname;
    if(route.endsWith('/auth/exchange')){
      const grant=init.headers['X-CRM-Grant'];
      if(!grant)return new Response(JSON.stringify({error:'grant_required',text:'Выберите полномочие.',details:{grants}}),{status:409,headers:{'X-CRM-Namespace':'crm-shared-pilot-v1'}});
      selected=grant;
      return new Response(JSON.stringify({ok:true,token:'crm1.'+'C'.repeat(180),employee_uuid:employee,actor_uid:303,expires_at:Math.floor(Date.now()/1000)+1800,name:'Synthetic manager',access_model:2,policy_revision:4,grant_id:grant,grants}),{headers:{'X-CRM-Namespace':'crm-shared-pilot-v1'}});
    }
    if(offline)throw Error('Synthetic offline');
    if(route.endsWith('/context'))return Response.json({employee_uuid:employee,actor_uid:303,team_id:'demo-team',role:'manager',capabilities:['call','note'],owner:false,access_model:2,policy_revision:4,grant_id:selected,grants,fields:[],dropdowns:{},cohorts:[],distribution:{enabled:false},workspace:{defaultModule:'portfolio',tree:[{id:'portfolio',children:[]},{id:'connection',children:[]}]}});
    if(route.endsWith('/anchor'))return Response.json({anchor_id:crypto.randomUUID(),installation_id:'synthetic-installation',server_at:new Date().toISOString()});
    if(route.endsWith('/portfolio'))return Response.json({people:[],total_people:0,total_contracts:0,truncated:false});
    if(route.endsWith('/command'))return Response.json({operation_id:JSON.parse(init.body).operation_id,status:'accepted',event_id:crypto.randomUUID(),grant_id:selected});
    if(route.endsWith('/changes'))return Response.json({cursor:{assignments:0,facts:0},dirty:false});
    return Response.json({error:'unexpected',text:'Unexpected synthetic route'},{status:503});
  }
  return realFetch(url,init);
};
dialog.showErrorBox=(title,message)=>{fs.writeFileSync(path.join(qa,'ERROR.txt'),title+'\n'+message);app.exit(1);};
require(path.join(desk,'out/main.js'));
const nap=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn,label){for(let i=0;i<400;i++){if(await fn())return;await nap(100);}throw Error('Timeout: '+label);}
(async()=>{let win;try{
  await app.whenReady();
  await until(async()=>{win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()==='hard://app/index.html');return win&&await win.webContents.executeJavaScript('!!window.crm').catch(()=>false);},'window');
  const js=code=>win.webContents.executeJavaScript(code,true);
  await until(()=>js('!!document.querySelector("#existing-manager-connect")'),'saved login');
  await js('document.querySelector("#existing-manager-connect").click()');
  await until(()=>js('document.querySelectorAll("#grant-choice option").length===2'),'grant candidates');
  await js('document.querySelector("#grant-choice").value="grant-b";document.querySelector("#grant-connect").click()');
  await until(()=>js('window.crm.state().then(s=>s.context?.grant_id==="grant-b")'),'selected context');
  const state=await js('window.crm.state()');
  assert(state.owner.includes('303')&&state.owner.includes('grant-b'));
  await js('window.crm.connect({action:"offline",value:true})');offline=true;
  const operation_id=crypto.randomUUID();
  const result=await js('window.crm.command('+JSON.stringify({schema_version:1,operation_id,kind:'note',contract_ids:['44444444-4444-4444-8444-444444444444'],payload:{text:'Grant-bound pending'},base_revisions:{}})+')');
  assert(result.ok,result.text);
  const entries=(await js('window.crm.queue()')).data.entries;
  assert.equal(entries[0].grant_id,'grant-b');assert.equal(entries[0].actor_uid,303);assert.equal(entries[0].state,'pending');
  fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,grant_required:true,explicit_choice:true,grant_binding:true,owner_isolated:true},null,2));
  app.quit();
}catch(error){fs.writeFileSync(path.join(qa,'FAIL.txt'),String(error.stack||error));console.error(error);app.exit(1);}})();
