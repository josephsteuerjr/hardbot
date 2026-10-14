require('./qa-soft-preload.cjs');
const totals = [{sum:100,unit:'TJS'},{sum:200000,unit:'UZS'},{sum:300,unit:'RUB'}];
const previous = window.hard.api;
window.hard.state = async () => ({mode:'field',productId:'hard',fixedProduct:true,product:'Хард',version:'3.0.0',enrolled:true,theme:'dark',deviceName:'Синтетика'});
window.hard.api = async (method,path,query,body) => {
  if(path==='/api/bootstrap') return {ok:true,status:200,data:{uid:101,employee:'Синтетический выездник',roles:{field:true,soft:false},persons:[
    {rep:'MIXED',client:'Синтетический клиент с тремя валютами',total:200400,n:3,totals},
    {rep:'SINGLE',client:'Синтетический клиент с одной валютой',total:50,n:1,totals:[{sum:50,unit:'TJS'}]},
  ]}};
  if(path==='/api/person') return {ok:true,status:200,data:{rep:'MIXED',client:'Синтетический клиент с тремя валютами',total:200400,totals,contracts:[],receipts:[]}};
  if(path==='/api/local/draft') return {ok:true,status:200,data:{draft:null}};
  return previous(method,path,query,body);
};
