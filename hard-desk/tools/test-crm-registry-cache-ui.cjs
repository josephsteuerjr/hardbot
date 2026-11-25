// Acknowledgements must survive an offline snapshot reload without regressing newer groups.
const {app,BrowserWindow}=require('electron');
const {buildSync}=require('esbuild'),assert=require('node:assert/strict'),path=require('node:path');
const root=process.env.HARD_CRM_QA_ROOT;assert(root);app.setPath('userData',path.join(root,'profile'));
app.whenReady().then(async()=>{
 const win=new BrowserWindow({show:false,webPreferences:{contextIsolation:true,sandbox:true}});
 try{
  await win.loadURL('data:text/html,<div id="host"></div>');
  const js=s=>win.webContents.executeJavaScript(s,true);
  win.webContents.on('console-message',(_event,details)=>console.log(details.message));
  await js(buildSync({entryPoints:[path.join(__dirname,'../ui/crm-registry.ts')],bundle:true,format:'iife',globalName:'Registry',write:false}).outputFiles[0].text);
  await js(`(async()=>{window.queueReads=0;window.loads=0;window.drafts={};
    window.crm={platform:'desktop',draft:async(key,...value)=>{if(value.length)drafts[key]=value[0];return {ok:true,data:drafts[key]??null}},
      queue:async()=>{queueReads++;return {ok:true,data:{entries:[{action:'registry-edit',state:'accepted',result:{rows:[{contract_uuid:'contract',values:{soft_comment:'Accepted call',comment:'Older visit'},revisions:{call:1,visit:1}}]}}]}}},
      api:async()=>{loads++;return {ok:true,stale:true,data:{total:1,has_more:false,columns:[{key:'soft_comment',title:'Call',group:'call',options:[]},{key:'comment',title:'Visit',group:'visit',options:[]}],rows:[{contract_uuid:'contract',fields:{soft_comment:'Old cache',comment:'Newer visit'},revisions:{call:0,visit:3},writable_fields:['soft_comment','comment']}]}}}};
    window.stop=await Registry.mountRegistry(document.querySelector('#host'),crm);})().catch(e=>{console.error(e.stack);throw e})`);
  const values=()=>js(`[...document.querySelectorAll('[data-cell] > span')].map(e=>e.textContent)`);
  assert.deepEqual(await values(),['Accepted call','Newer visit']);
  await js(`document.querySelector('#registry-reload').click()`);
  for(let n=0;n<100&&await js('loads')<2;n++)await new Promise(r=>setTimeout(r,10));
  assert.equal(await js('loads'),2);
  assert.deepEqual(await values(),['Accepted call','Newer visit']);
  assert.equal(await js('queueReads'),1);
  await js('stop()');console.log('PASS: offline reload retains acknowledged values and newer server groups');win.destroy();app.exit(0);
 }catch(e){console.error(e);win.destroy();app.exit(1);}
});
