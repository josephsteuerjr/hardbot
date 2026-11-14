// Test driver only. Control binds loopback and requires the synthetic fixture key.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {app,BrowserWindow,dialog,net}=require('electron');
const root=process.env.HARD_CRM_QA_ROOT,actor=process.env.HARD_CRM_QA_ACTOR;assert(root&&['m1','m2','a','c'].includes(actor));
const qa=path.join(root,actor);fs.mkdirSync(qa,{recursive:true});
const desk=path.resolve(__dirname,'..'),key=fs.readFileSync(process.env.HARD_CRM_QA_SECRET_FILE,'utf8').trim();
process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'),'utf8')).version;
let blocked=false;const fetch=net.fetch.bind(net);
net.fetch=(url,init)=>{if(blocked&&String(url).startsWith(process.env.HARD_CRM_QA_SHARED_ORIGIN))throw Error('Synthetic disconnected channel');return fetch(url,init);};
dialog.showErrorBox=(title,message)=>{fs.writeFileSync(path.join(qa,'startup-error.txt'),title+'\n'+message);app.exit(1);};
require(path.join(desk,'out/main.js'));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let i=0;i<300;i++){if(await fn())return;await pause(100);}throw Error('Worker startup timeout');}
(async()=>{
  try{
    await app.whenReady();let win;
    await until(()=>{win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()==='hard://app/index.html');return win;});
    const js=code=>win.webContents.executeJavaScript(code,true);
    await until(()=>js('!!window.crm'));
    const existing=await js('window.crm.state()');
    if(existing.context?.actor_uid!==({m1:401,m2:402,a:101,c:500})[actor]){
      await until(()=>js('!!document.querySelector("#demo-connect")'));
      await js(`(()=>{document.querySelector('#demo-role').value=${JSON.stringify(actor.startsWith('m')?'manager':'employee')};document.querySelector('#demo-connect').click();})()`);
    }
    await until(async()=>{const s=await js('window.crm.state()');return s.context?.actor_uid===({m1:401,m2:402,a:101,c:500})[actor];});
    const server=http.createServer(async(req,res)=>{
      if(req.headers['x-control']!==key){res.writeHead(403);res.end();return;}
      let chunks=[];for await(const chunk of req)chunks.push(chunk);
      try{
        const body=JSON.parse(Buffer.concat(chunks).toString()||'{}');let result;
        if(req.url==='/eval')result=await js(body.code);
        else if(req.url==='/network'){blocked=body.blocked===true;result={blocked};}
        else if(req.url==='/shot'){
          assert(/^[a-z0-9-]+$/.test(body.name));await pause(100);
          fs.writeFileSync(path.join(qa,body.name+'.png'),(await win.webContents.capturePage()).toPNG());result={ok:true};
        }else if(req.url==='/quit'){result={ok:true};setTimeout(()=>{server.close();app.quit();},100);}
        else throw Error('Unknown control');
        res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,result:result??null}));
      }catch(error){res.writeHead(500);res.end(JSON.stringify({ok:false,error:error.message}));}
    });
    server.listen(0,'127.0.0.1',()=>fs.writeFileSync(path.join(root,actor+'.control.json'),JSON.stringify({port:server.address().port,pid:process.pid})));
  }catch(e){fs.writeFileSync(path.join(qa,'FAIL.txt'),e.stack||String(e));app.exit(1);}
})();
