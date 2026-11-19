const http=require('node:http');
window.__errors=[];window.addEventListener('error',e=>window.__errors.push(String(e.error||e.message)));
window.hard={state:async()=>({platform:'desktop'}),pick:async()=> 'next-synthetic',win(){},on(){},local:(path,body={})=>new Promise((resolve,reject)=>{
 const data=Buffer.from(JSON.stringify(body));const r=http.request({hostname:'127.0.0.1',port:Number(process.env.HARD_ENR_FIXTURE_PORT),path,method:'POST',headers:{'Content-Type':'application/json','Content-Length':data.length}},res=>{let out='';res.on('data',b=>out+=b);res.on('end',()=>{try{resolve(JSON.parse(out))}catch(e){reject(e)}})});r.on('error',reject);r.end(data);
})};
