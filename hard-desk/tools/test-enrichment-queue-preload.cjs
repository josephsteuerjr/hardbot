const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const config=JSON.parse(fs.readFileSync(path.join(process.env.HARD_ENR_QUEUE_QA_ROOT,'renderer.private.json'),'utf8'));
window.__errors=[];window.addEventListener('error',event=>window.__errors.push(event.message));
window.hard={pick:async()=>config.master,local:(route,body={})=>new Promise((resolve,reject)=>{
 const raw=Buffer.from(JSON.stringify(body));const request=http.request({hostname:'127.0.0.1',port:config.port,path:route,method:'POST',headers:{'Content-Type':'application/json','Content-Length':raw.length,'X-Local-Secret':config.secret}},response=>{
  let data='';response.on('data',part=>data+=part);response.on('end',()=>{try{resolve(JSON.parse(data))}catch(error){reject(error)}});
 });request.on('error',reject);request.end(raw);
})};
