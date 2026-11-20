// Production update flow and real Electron downloader against an isolated HTTP feed.
// quitAndInstall is intercepted: synthetic payloads are never executed.
const {app, net} = require('electron');
const {NsisUpdater} = require('electron-updater');
const {ElectronHttpExecutor} = require('electron-updater/out/electronHttpExecutor');
const {build} = require('esbuild');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const vm = require('node:vm');
const dir = path.resolve(process.env.HARD_QA_ROOT || path.join(__dirname, '../../_local/redesign-2026-10-09'), 'update-flow');
fs.mkdirSync(dir, {recursive:true});
app.setPath('userData', path.join(dir, 'electron'));
const pair = crypto.generateKeyPairSync('ed25519');
const publicKey = pair.publicKey.export({type:'spki',format:'pem'});
const bytes = Buffer.from('MZ synthetic bytes; this test never executes an installer');
const sha512 = crypto.createHash('sha512').update(bytes).digest('base64');
let mode = 'valid', downloads = 0, installs = 0;
const results = [];
app.whenReady().then(async () => {
  const {productDefines} = await import('./products.mjs');
  const server = http.createServer((req,res) => {
    const product = req.url.split('/')[1].split(/[.-]/)[0];
    const version = mode === 'downgrade' ? '3.0.0' : mode === 'current' ? '3.0.1' : '3.0.2';
    const file = `${product}-${version}-setup.exe`;
    if(req.url.split('?')[0] === `/${product}-signed.json`){
      if(mode==='missing'){res.statusCode=404;res.end();return;}
      const payload = Buffer.from(JSON.stringify({product:mode==='wrong-product'?'other':product, version,file,sha512,size:bytes.length}));
      const signature = crypto.sign(null,payload,pair.privateKey);
      if(mode==='bad-signature')signature[0]^=1;
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify({payload:payload.toString('base64'),signature:signature.toString('base64')}));
    } else if(req.url.startsWith(`/${product}.yml`)) {
      res.setHeader('Content-Type','text/yaml');
      res.end(`version: ${version}\nfiles:\n  - url: ${mode==='wrong-channel'?'another-3.0.2-setup.exe':file}\n    sha512: ${sha512}\n    size: ${bytes.length}\npath: ${file}\nsha512: ${sha512}\n`);
    } else if(req.url.startsWith(`/${file}`)) {
      downloads++;
      res.setHeader('Content-Length',bytes.length);
      if(mode==='stall')res.write(bytes.subarray(0,2));else res.end(bytes);
    }
    else {res.statusCode=404;res.end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  try {
    for(const product of ['hard','soft','manager','analyst']) {
      const serverAnalyst = product==='analyst' && process.env.HARD_ANALYST_SERVER_CHANNEL==='1';
      const origin = serverAnalyst ? 'https://hard.rimborso.ru/updates/cis-collection-analyst/'
        : 'https://github.com/josephsteuerjr/hard-desktop-releases/releases/latest/download/';
      const data = path.join(dir,product+'-'+Date.now()); fs.mkdirSync(data);
      const config = path.join(data,'app-update.yml');
      fs.writeFileSync(config,`provider: generic\nurl: ${url}\nchannel: ${product}\nupdaterCacheDirName: flow-${product}-${Date.now()}\n`);
      const adapter = {version:'3.0.1',name:'hard-'+product,isPackaged:true,appUpdateConfigPath:config,
        userDataPath:data,baseCachePath:data,whenReady:async()=>{},onQuit:()=>{},quit:()=>{},relaunch:()=>{}};
      let payloadPath;
      class IsolatedUpdater extends NsisUpdater {
        constructor(options){
          assert.equal(options.channel,product);
          assert.equal(options.url,origin);
          super(null,adapter);
          this.httpExecutor = new ElectronHttpExecutor();
          this.setFeedURL({...options,url});
          this.on('update-downloaded',event=>{payloadPath=event.downloadedFile;});
        }
        quitAndInstall(silent,relaunch){assert.equal(silent,true);assert.equal(relaunch,true);installs++;}
      }
      const code = (await build({entryPoints:[path.join(__dirname,'../src/main/updates.ts')],
        bundle:true,platform:'node',format:'cjs',write:false,external:['electron','electron-updater'],
        define:productDefines(product),plugins:[{name:'test-key',setup(build){
          build.onLoad({filter:/release-public-key\.json$/},()=>({contents:JSON.stringify({pem:publicKey}),loader:'json'}));
        }}]})).outputFiles[0].text;
      const module = {exports:{}};
      const timeouts = [], intervals = [], appEvents = new Map();
      vm.runInNewContext(code,{module,exports:module.exports,Buffer,AbortSignal,clearTimeout,
        setTimeout:(fn,ms)=>ms===90_000?setTimeout(fn,mode==='stall'?150:ms):(timeouts.push(ms),{unref(){}}),
        setInterval:(fn,ms)=>(intervals.push(ms),{unref(){}}),require(name){
          if(name==='electron')return {app:{isPackaged:true,on:(event,listener)=>appEvents.set(event,listener)},net:{fetch(feed,options){
            assert.equal(feed,origin+product+'-signed.json');
            return net.fetch(url+product+'-signed.json',options);
          }}};
          if(name==='electron-updater')return {NsisUpdater:IsolatedUpdater};
          return require(name);
        }});
      const states=[];
      const flow = module.exports;flow.startUpdates(state=>states.push(state),()=>{});
      assert.deepEqual(timeouts,[30_000]);
      assert.deepEqual(intervals,[15 * 60 * 1000]);
      assert.equal(typeof appEvents.get('browser-window-focus'),'function');
      const before = downloads;
      for(mode of ['bad-signature','wrong-product','wrong-channel']) {
        assert.equal((await flow.checkUpdates()).phase,'error',mode);
        assert.equal(downloads,before,'Rejected metadata must not download a package');
      }
      mode='downgrade';assert.equal((await flow.checkUpdates()).phase,'idle');assert.equal(downloads,before);
      mode='missing';assert.equal((await flow.checkUpdates()).text,'Доступных обновлений нет.');
      mode='current';assert.equal((await flow.checkUpdates()).text,'У вас установлена последняя версия приложения.');
      assert.equal(downloads,before);
      mode='stall';
      const stalled=await flow.checkUpdates();
      assert.equal(stalled.phase,'error');
      assert.match(stalled.text,/За 90 секунд не поступило новых данных/);
      assert.equal(downloads,before+1);
      mode='valid';const ready=await flow.checkUpdates();
      assert.equal(ready.phase,'ready');assert.match(ready.text,/Перезагрузите приложение/);assert.equal(downloads,before+2);
      assert.ok(states.some(state=>state.phase==='downloading'&&state.transferred===0&&state.total===bytes.length));
      assert.ok(states.some(state=>state.phase==='downloading'&&state.transferred===bytes.length));
      let closeChecked=false,beforeInstall=false;
      assert.equal((await flow.installUpdate(async()=>{closeChecked=true;return false;},()=>{beforeInstall=true;})).ok,false);
      assert.equal(closeChecked,true);assert.equal(beforeInstall,false);assert.equal(flow.updateState().phase,'ready');
      fs.writeFileSync(payloadPath,'corrupted after download');
      assert.equal((await flow.installUpdate(async()=>true,()=>{beforeInstall=true;})).ok,false);
      assert.equal(beforeInstall,false);assert.equal(flow.updateState().phase,'error');
      assert.equal((await flow.checkUpdates()).phase,'ready');assert.equal(downloads,before+3);
      const install = await flow.installUpdate(async()=>true,()=>{beforeInstall=true;});
      assert.equal(install.ok,true);assert.equal(beforeInstall,true);
      results.push({product,origin,signature:true,channel:true,noDowngrade:true,
        automaticDownload:true,downloadProgress:true,stalledDownloadRetry:true,unsavedOrBusyBlocked:true,preInstallChecksum:true,cacheRecovery:true});
    }
    assert.equal(installs,4);
    fs.writeFileSync(path.join(dir,'RESULTS.json'),JSON.stringify({results,downloads,interceptedInstalls:installs},null,2));
    console.log('Production updater flow passed for all 4 products: signatures/channels/downgrade, automatic download, close guard, rehash and damaged-cache recovery. No installer executed.');
    server.close();app.exit(0);
  } catch(error) {console.error(error);server.close();app.exit(1);}
});
