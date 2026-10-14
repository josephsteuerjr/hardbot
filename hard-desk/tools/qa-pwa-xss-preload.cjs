const payload='<img id="audit-xss" src="invalid://audit" onerror="window.auditXssExecuted=true">';
const card={rep:'SYN-XSS',client:'Synthetic client',birth_date:payload,assign_date:'2026-10-01',visit_date:'2026-10-02',
  contracts:[],form:[],info:[],history:[],calls:[],photos:0,visited:false,receipts:[],total:0,totals:[],soft:{},geo:null};
localStorage.setItem('app_token','TOK-SYNTHETIC-aaaaaaaaaaaaaaaaaaaa');
window.auditXssExecuted=false;
window.auditPayload=payload;
window.fetch=async input=>{
  const url=String(input);
  let data={ok:true};
  if(url.startsWith('/version.json')) data={shellVersion:0};
  else if(url.startsWith('/api/bootstrap'))data={ok:true,uid:101,employee:'Synthetic employee',kind:'agent',lang:'ru',
    persons:[{rep:'SYN-XSS',client:'Synthetic client',n:0,visited:false,totals:[],tels:[],ids:[]}],roles:null,
    meta:{dropdowns:{},formProtocolVersion:3}};
  else if(url.startsWith('/api/cards'))data={cards:{'SYN-XSS':card}};
  else if(url.startsWith('/api/person'))data=card;
  return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
};
