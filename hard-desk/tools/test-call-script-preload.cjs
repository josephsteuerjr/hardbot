// Real loopback script API; synthetic existing CRM screens and call queue.
const http=require('node:http');
const manager=process.argv.includes('--call-script-manager');
if(manager){
  require('./test-manager-account-preload.cjs');
  window.__account.enrolled=true;window.__account.stage='approved';
}else require('./qa-soft-preload.cjs');
const prior=window.hard.api;
const endpoint=new URL(process.env.HARD_SCRIPT_QA_URL);
if(endpoint.hostname!=='127.0.0.1')throw Error('Synthetic loopback only');
window.__scriptQa={requests:[],loseReply:false,offline:false};
window.hard.win=action=>require('electron').ipcRenderer.send('call-script-window',action);
const originalState=window.hard.state;
window.hard.state=async()=>({...await originalState(),version:'3.2.21-rc.1',
  ...(manager?{device:{name:'Стенд',role:'manager',username:'synthetic-leader'}}:{})});
window.hard.api=async(method,path,query,body)=>{
  if(path==='/api/manager/call-script'||path==='/api/call-script'){
    if(window.__scriptQa.offline)return {ok:false,status:0,kind:'offline',text:'Тест: нет связи'};
    const url=new URL(path,endpoint);for(const [k,v] of Object.entries(query||{}))url.searchParams.set(k,String(v));
    const response=await new Promise((resolve,reject)=>{
      const req=http.request(url,{method,headers:{'Content-Type':'application/json','X-Client-Version':'3.2.20',
        [manager?'X-Pult-Token':'X-App-Token']:process.env[manager?'HARD_SCRIPT_QA_MANAGER':'HARD_SCRIPT_QA_EMPLOYEE']}},r=>{
        const chunks=[];r.on('data',c=>chunks.push(c));r.on('end',()=>{
          const text=Buffer.concat(chunks).toString('utf8');let data;try{data=JSON.parse(text);}catch{}
          resolve({ok:r.statusCode>=200&&r.statusCode<300,status:r.statusCode,data,text:data?.error||text});
        });
      });req.on('error',reject);if(body)req.write(JSON.stringify(body));req.end();
    });
    window.__scriptQa.requests.push({method,path,body,response});
    if(method==='POST'&&body?.action==='save'&&window.__scriptQa.loseReply){window.__scriptQa.loseReply=false;return {ok:false,status:502,text:'Тест: ответ потерян после сохранения'};}
    return response;
  }
  if(path==='/api/local/draft')return {ok:true,status:200,data:{draft:null}};
  return prior(method,path,query,body);
};
