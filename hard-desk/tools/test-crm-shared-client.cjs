// Real main/preload/renderer and Windows protection; synthetic upstream only.
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto'), assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {app,BrowserWindow,net,dialog}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT, phase=Number(process.env.HARD_CRM_QA_PHASE);
assert(qa && [1,2].includes(phase));
const desk=path.resolve(__dirname,'..'), employee='33333333-3333-4333-8333-333333333333';
const credential='synthetic-manager-credential-for-client-qa-0123456789';
process.env.HARD_USER_DATA=path.join(qa,'profile'); process.env.HARD_CRM_SOURCE=path.dirname(desk);
process.env.LOCALAPPDATA=path.join(qa,'local-app-data');
fs.mkdirSync(qa,{recursive:true});
if (phase===1) {
  const manager=path.join(process.env.LOCALAPPDATA,'Hard/manager/data');fs.mkdirSync(manager,{recursive:true});
  fs.writeFileSync(path.join(manager,'settings.json'),JSON.stringify({server:'https://hard.rimborso.ru:8080'}));
  fs.writeFileSync(path.join(manager,'device.key'),execFileSync(path.join(desk,'installer/bin/process-check.exe'),['seal'],{input:Buffer.from(credential),windowsHide:true}));
}
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'))).version;
const realFetch=net.fetch.bind(net);let mode=phase===1?'active':'offline', exchanges=0;
net.fetch=async (url,init={})=>{
  if (String(url).startsWith('https:')) {
    assert(String(url).startsWith('https://hard.rimborso.ru/crm-pilot/'),'Unexpected external request');
    if(mode==='offline')throw Error('Synthetic unavailable');
    const route=new URL(url).pathname;
    let status=200,value;
    if(route.endsWith('/auth/exchange')) {
      assert.equal(init.headers['X-Pult-Token'],credential);exchanges++;
      value={ok:true,token:'crm1.'+'B'.repeat(180),employee_uuid:employee,expires_at:Math.floor(Date.now()/1000)+1800,name:'Synthetic manager'};
    } else if(mode==='revoked') {status=403;value={error:'manager_required',text:'Synthetic revoked access'};}
    else {
      assert.equal(init.headers.Authorization,'Bearer crm1.'+'B'.repeat(180));
      assert.equal(init.headers['X-Pult-Token'],undefined);
      if(route.endsWith('/context'))value={employee_uuid:employee,team_id:'crm-pilot-managers',role:'manager',capabilities:['call','exchange'],owner:false,fields:[],dropdowns:{},cohorts:[]};
      else if(route.endsWith('/portfolio'))value={people:[],total_people:0,total_contracts:0,truncated:false};
      else if(route.endsWith('/anchor'))value={anchor_id:crypto.randomUUID(),installation_id:'synthetic-installation',server_at:new Date().toISOString()};
      else {status=503;value={text:'Synthetic temporary failure'};}
    }
    return new Response(JSON.stringify(value),{status,headers:{'X-CRM-Namespace':'crm-shared-pilot-v1'}});
  }
  return realFetch(url,init);
};
dialog.showErrorBox=(title,message)=>{fs.writeFileSync(path.join(qa,'ERROR.txt'),title+'\n'+message);app.exit(1);};
require(path.join(desk,'out/main.js'));
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
(async()=>{
  await app.whenReady();let win;
  for(let i=0;i<500;i++){
    win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()==='hard://app/index.html');
    if(win && await win.webContents.executeJavaScript('!!window.crm').catch(()=>false))break;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  assert(win);const js=code=>win.webContents.executeJavaScript(code,true);
  if(phase===1){
    const result=await js('window.crm.connect({action:"existing-manager"})'); assert(result.ok);
    const state=await js('window.crm.state()'); assert.equal(state.context.employee_uuid,employee); assert(state.connection.shared);
    assert(!JSON.stringify(state).includes(credential)); assert.equal(exchanges,1);
    await js('window.crm.connect({action:"offline",value:true})');
    const body={operation_id:crypto.randomUUID(),schema_version:1,kind:'call',contract_ids:['44444444-4444-4444-8444-444444444444'],payload:{values:{soft_comment:'Protected pending work'}},base_revisions:{}};
    assert((await js('window.crm.command('+JSON.stringify(body)+')')).ok);
    const name='business-'+crypto.createHash('sha256').update(state.owner).digest('hex')+'.bin';
    const file=path.join(qa,'profile/crm',name); const before=hash(file);
    await js('window.crm.connect({action:"offline",value:false})');mode='revoked';
    const denied=await js('window.crm.api("GET","context")');assert.equal(denied.status,403);
    const after=await js('window.crm.state()');assert.equal(after.owner,'');assert.equal(after.context,null);
    const settings=JSON.parse(fs.readFileSync(path.join(qa,'profile/settings.json')));assert.equal(settings.crmLast,undefined);
    assert.equal(hash(file),before);
    fs.writeFileSync(path.join(qa,'PHASE1.json'),JSON.stringify({file,sha256:before,revocationClearedContext:true,pendingPreserved:true,productionRequests:0}));
  }else{
    const before=JSON.parse(fs.readFileSync(path.join(qa,'PHASE1.json')));
    const state=await js('window.crm.state()');assert.equal(state.context,null);assert.equal(state.owner,'');
    assert.equal(hash(before.file),before.sha256);
    assert(!(await js('window.crm.queue()')).ok);
    fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({realMain:true,realRenderer:true,realDPAPI:true,syntheticAuthority:true,revocationSurvivesOfflineRestart:true,pendingPreserved:true,productionRequests:0}));
  }
  app.quit();
})().catch(error=>{fs.writeFileSync(path.join(qa,'ERROR.txt'),String(error.stack));console.error(error);app.exit(1);});
