// Real main/preload/renderer with an isolated synthetic backend; no external authority.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app,BrowserWindow,dialog,net}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT;assert(qa,'Explicit fresh QA root required');
const desk=path.resolve(__dirname,'..');fs.mkdirSync(qa,{recursive:true});
const deadline=setTimeout(()=>{fs.writeFileSync(path.join(qa,'DEADLINE.txt'),'Native workspace QA exceeded 180 seconds');app.exit(1);},180000);deadline.unref();
process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);
assert(!fs.existsSync(process.env.HARD_USER_DATA),'Use a fresh isolated profile');
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'),'utf8')).version;
let transform=null,saveDialogs=0;
const realFetch=net.fetch.bind(net);
net.fetch=async(url,init)=>{
  assert(!String(url).startsWith('https:'),'External requests are forbidden in native workspace QA');
  if(/\/crm\/manager-access$/.test(String(url)) && JSON.parse(String(init?.body||'{}')).body?.resource==='plan')
    return new Response(JSON.stringify({ok:true,employees:[]}),{status:200,headers:{'Content-Type':'application/json'}});
  const response=await realFetch(url,init);
  if(transform && /\/crm\/context$/.test(String(url))){
    const value=await response.json();
    return new Response(JSON.stringify(transform(value)),{status:response.status,headers:response.headers});
  }
  return response;
};
dialog.showSaveDialog=async()=>{saveDialogs++;return{canceled:false,filePath:path.join(qa,'owner-table.xlsx')};};
dialog.showOpenDialog=async()=>({canceled:true,filePaths:[]});
dialog.showErrorBox=(title,message)=>{fs.writeFileSync(path.join(qa,'startup-error.txt'),title+'\n'+message);app.exit(1);};
require(path.join(desk,'out/main.js'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label){for(let i=0;i<600;i++){if(await check())return;await pause(100);}throw Error('Timeout: '+label);}
const workspace=modules=>({schema_version:1,revision:7,defaultModule:'connection',tree:modules.map(id=>({id,title:id,icon:'qa',order:1,children:[],scopes:[]}))});
const selected=(role,capabilities,modules,extra={})=>context=>({...context,role,capabilities,owner:false,workspace:workspace(modules),...extra});
const ownerContext=context=>({...context,role:'owner',owner:true,capabilities:[],workspace:workspace([])});
(async()=>{let win;try{
  await app.whenReady();
  await until(()=>{win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()==='hard://app/index.html');return win&&win.webContents.executeJavaScript('!!window.crm').catch(()=>false);},'CRM renderer');
  const js=code=>win.webContents.executeJavaScript(code,true);
  const connect=async(role,next)=>{
    transform=next;
    const result=await js('window.crm.connect('+JSON.stringify({mode:'synthetic',role})+')');
    assert(result.ok,result.text);return result.data;
  };
  const admin=async()=>js('window.crm.state().then(s=>window.crm.admin("local",["/admin",{method:"GET",path:"/admin/accounts.json"}],s.owner))');
  const access=async(resource='team')=>js('window.crm.api("GET","manager-access",undefined,'+JSON.stringify({resource})+')');
  const plan=async()=>js('window.crm.api("POST","manager-access",'+JSON.stringify({resource:'plan',payload:{plan:{}}})+')');

  await connect('analyst',ownerContext);
  assert.equal((await js('window.crm.state()')).context.owner,true);
  const ownerAdmin=await admin();assert(ownerAdmin.ok,JSON.stringify(ownerAdmin));
  assert(Array.isArray(ownerAdmin.data.accounts));
  const ownerTable=await js('window.crm.workbook("table",{headers:["Synthetic"],rows:[["Synthetic value"]],name:"owner.xlsx"})');
  assert(ownerTable.ok,JSON.stringify(ownerTable));assert(fs.existsSync(path.join(qa,'owner-table.xlsx')));
  const ownerExchange=await js('window.crm.workbook("read")');assert(ownerExchange.cancelled===true);

  await connect('manager',ownerContext);
  const ownerAccess=await access();assert(ownerAccess.ok,JSON.stringify(ownerAccess));
  const ownerPlan=await plan();assert(ownerPlan.ok,JSON.stringify(ownerPlan));

  await connect('manager',selected('analyst',['exchange'],['team']));
  const analystTeam=await access();assert(analystTeam.ok,JSON.stringify(analystTeam));
  const analystPlan=await plan();assert.equal(analystPlan.status,403);

  await connect('manager',selected('manager',['analytics'],['team']));
  assert((await access()).ok);
  assert.equal((await plan()).status,403);
  await connect('manager',selected('manager',['assign'],['team']));
  assert((await plan()).ok);
  transform=selected('manager',['analytics'],['team']);
  assert.equal((await plan()).status,403,'Every account write must recheck the fresh grant');

  await connect('manager',selected('employee',['exchange','assign'],['team','exchange','manager-statistics','statistics']));
  assert.equal((await access()).status,403);
  const beforeDenied=saveDialogs;
  const employeeTable=await js('window.crm.workbook("table",{headers:[],rows:[]})');
  assert.equal(employeeTable.ok,false);assert.equal(saveDialogs,beforeDenied);

  await connect('manager',selected('manager',['assign'],[]));
  assert.equal((await access()).status,403);
  await connect('manager',context=>{const value=selected('manager',['assign'],['team'])(context);delete value.workspace;return value;});
  assert((await access()).ok,'Legacy default manager grant must retain account access');

  await connect('analyst',selected('analyst',['exchange'],['exchange']));
  assert((await admin()).ok);
  transform=selected('manager',['exchange'],['exchange','statistics','team']);
  const crossRole=await admin();assert.equal(crossRole.ok,false);assert.match(crossRole.text,/полномоч/i);
  transform=selected('analyst',[],['exchange']);
  assert.equal((await admin()).ok,false);
  transform=selected('analyst',['exchange'],['statistics','team']);
  assert.equal((await admin()).ok,false);
  transform=selected('analyst',['exchange'],['exchange']);
  assert((await admin()).ok);
  transform=selected('analyst',['exchange'],[]);
  const revoked=await js('window.crm.state().then(s=>window.crm.admin("copy",["must-not-copy"],s.owner))');
  assert.equal(revoked.ok,false);assert.match(revoked.text,/полномоч/i);
  transform=context=>{const value=selected('analyst',['exchange'],['exchange'])(context);delete value.workspace;return value;};
  assert((await admin()).ok,'Legacy analyst default grant must retain admin');

  await connect('manager',selected('manager',['assign'],['team'],{access_model:2,grant_id:'narrow-manager'}));
  assert((await access()).ok,'Scoped manager access uses the scoped proxy');
  await connect('analyst',selected('analyst',['exchange'],['exchange'],{access_model:2,grant_id:'narrow-analyst'}));
  assert((await admin()).ok,'Scoped admin permits filtered account reads');
  await connect('analyst',context=>ownerContext({...context,access_model:2,grant_id:'global-owner'}));
  assert((await admin()).ok,'Global owner retains legacy administration');
  const grantCatalog=await js('window.crm.api("GET","grant-catalog")');
  assert(grantCatalog.ok,JSON.stringify(grantCatalog));
  assert(Array.isArray(grantCatalog.data.roles));

  fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,actualMain:true,actualPreload:true,actualRenderer:true,actualSyntheticBackend:true,
    ownerAdmin:true,ownerWorkbook:true,ownerManagerAccess:true,analystManagerAccess:true,managerPlanCapability:true,
    roleCapabilityWorkspaceRequired:true,legacyDefaultGrants:true,freshAdminRecheck:true,freshManagerAccessRecheck:true,externalRequests:0},null,2));
  app.quit();
}catch(error){fs.writeFileSync(path.join(qa,'FAIL.txt'),error.stack||String(error));console.error(error);app.exit(1);}})();
