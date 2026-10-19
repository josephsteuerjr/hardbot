// Execute production auth and team approval handlers, using synthetic accounts.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../../server/miniapp/index.html'),'utf8');
function source(name){
  const start=html.search(new RegExp(`^(?:async )?function ${name}\\(`,'m'));
  assert(start>=0,name); const end=html.indexOf('\n}',start);assert(end>start);return html.slice(start,end+2);
}
function page(role){
  const data=new Map(),elements=new Map(['app','loginuser','loginpass','loginerr','loginbtn'].map(k=>[k,{value:'',style:{},innerHTML:'',textContent:''}]));
  const requests=[];let geoCalls=0;
  const ctx={navigator:{geolocation:{getCurrentPosition(){geoCalls++;throw Error('GPS must never be requested');},watchPosition(){geoCalls++;throw Error('GPS must never be watched');}}},
    localStorage:{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)},
    $:k=>elements.get(k),esc:s=>String(s||''),AUTH_INPUT_CSS:'',STATE:{},LOGIN_GPS:null,AUTH_TAB:'login',AUTH_EXPIRED:false,AUTH_BUSY:false,AUTH_USER:'',
    hideBottomNav(){},L:()=>({pendingtitle:'Ожидает',pendingwhy:'Ожидает'}),
    deviceProfile:async()=>({fp:'synthetic-phone',label:'Synthetic iPhone'}),
    fetch:async(url,init)=>{requests.push({url,body:JSON.parse(init.body)});return {ok:true,json:async()=>({action:role==='agent'?'ok_pending':'ok',role,token:'synthetic',must_change_password:false})};},
    stampTokenUrl(){},toast(){},boot(){},confirm:()=>true,DG:{},dgTop:()=>'',dgLoad:async()=>{},
  };
  vm.createContext(ctx);
  vm.runInContext(['authErr','authBusy','accountLogin','renderPending','dgAccountEntries','dgApproveAccountDevice'].map(source).join('\n'),ctx);
  return {ctx,data,elements,requests,geoCalls:()=>geoCalls,run:c=>vm.runInContext(c,ctx)};
}
(async()=>{
  for(const role of ['agent','manager','security','full']){
    const p=page(role);p.elements.get('loginuser').value='synthetic';p.elements.get('loginpass').value='Password123';
    await p.run('accountLogin()');assert.equal(p.requests.length,1);assert.equal(p.geoCalls(),0);
    assert.equal(p.requests[0].body.gps,undefined);assert.equal(p.data.get('account_role'),role);
  }
  const p=page('manager');p.run('renderPending("Synthetic Employee","account")');
  assert.match(p.elements.get('app').innerHTML,/Вход команды/);assert.doesNotMatch(p.elements.get('app').innerHTML,/GPS|requestGpsException|геолокац/);
  p.data.set('account_role','manager');p.run('renderPending("Synthetic Leader","account")');
  assert.match(p.elements.get('app').innerHTML,/администратор/);assert.match(p.elements.get('app').innerHTML,/Профили и устройства/);
  const b={innerHTML:''};p.ctx.box=b;
  p.run('dgAccountEntries({entered:[{employee:"Synthetic Employee",can_approve:true,devices:[{id:7,kind:"phone",status:"pending",label:"Synthetic phone"}]}]},box)');
  assert.match(b.innerHTML,/Подтвердить/);assert.doesNotMatch(b.innerHTML,/prompt|причин|срок/);
  const calls=[];p.ctx.api=async(url,init)=>{calls.push({url,body:JSON.parse(init.body)});return {ok:true};};
  p.ctx.button={disabled:false,isConnected:true};
  await p.run('dgApproveAccountDevice(0,0,button)');
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/manager/account_approve');
  assert.deepEqual(calls[0].body,{device_id:7,observed_pc:false});
  assert(!/authGpsHTML\(\)|onclick="authGpsRetry\(\)"/.test(html));
  console.log('PWA: all roles log in without GPS; team phone approval needs one confirmation PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
