import * as vault from './store';
/** Only sampled, proven time is persisted. Separate immutable prefixes per tab. */
export class MobileActivity{
 private previous:any=null;private open:any=null;private anchor:any=null;private owner='';private lastInput=performance.now();private serial=Promise.resolve();
 constructor(private api:(m:string,a:string,b?:any)=>Promise<any>,private enqueue:(a:string,b:any,o:string)=>Promise<any>,private failed:(error:unknown)=>void=()=>{}){
  for(const name of ['pointerdown','keydown','scroll'])document.addEventListener(name,()=>{this.lastInput=performance.now();},{passive:true,capture:true});
  setInterval(()=>{void this.run(()=>this.tick()).catch(this.failed);},2000);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)void this.run(()=>this.pause()).catch(this.failed);});
  window.addEventListener('pagehide',()=>{void this.run(()=>this.pause()).catch(this.failed);});
 }
 private run(work:()=>Promise<void>){const next=this.serial.catch(()=>{}).then(work);this.serial=next;return next;}
 start(owner:string,installation:string){return this.run(async()=>{
  await this.finish();this.previous=null;this.anchor=null;this.owner=owner;
  // Migrate the earlier single-prefix candidate without discarding proven work.
  const legacy=await vault.get(owner,'activity-prefix');
  if(legacy){await this.enqueue('active-time',legacy,owner);await vault.put(owner,'activity-prefix',null);}
  await this.recover(owner);
  const result=await this.api('POST','anchor',{installation_id:installation});
  if(result.ok)this.anchor={...result.data,monotonic:performance.now()};
 });}
 private async pause(){this.previous=null;await this.finish();}
 stop(){return this.run(async()=>{await this.pause();this.owner='';this.anchor=null;});}
 state(){return {clock_quality:this.anchor?'anchored':'unconfirmed',idle_threshold_seconds:120};}
 clock(){return this.anchor?{anchor_id:this.anchor.anchor_id,installation_id:this.anchor.installation_id,elapsed_ms:Math.max(0,Math.round(performance.now()-this.anchor.monotonic))}:null;}
 private async deliver(owner:string,record:any){
  await this.enqueue('active-time',record.body,owner);
  await vault.update(owner,'activity-prefixes',(rows:any)=>{rows||={};const id=record.body.operation_id;if(rows[id]?.sealed)delete rows[id];return rows;});
 }
 private async recover(owner:string){
  const now=Date.now();
  const rows=await vault.update(owner,'activity-prefixes',(rows:any)=>{rows||={};for(const [id,row] of Object.entries<any>(rows))if(id!==this.open?.body.operation_id&&(row.sealed||now-row.at>6000||row.at>now+60000))row.sealed=true;return rows;});
  for(const row of Object.values<any>(rows))if(row.sealed)await this.deliver(owner,row);
 }
 private async finish(){
  const opened=this.open;this.open=null;if(!opened)return;
  const rows=await vault.update(opened.owner,'activity-prefixes',(rows:any)=>{rows||={};const row=rows[opened.body.operation_id];if(row)row.sealed=true;return rows;});
  const row=rows[opened.body.operation_id];if(row)await this.deliver(opened.owner,row);
 }
 private async tick(){
  if(this.owner)await this.recover(this.owner);
  const clock=this.clock(),kind=!document.hidden&&document.hasFocus()?(performance.now()-this.lastInput<120000?'active':'idle'):null;
  const current=clock&&this.owner?{...clock,owner:this.owner,kind}:null,prior=this.previous;this.previous=current;
  if(!prior||!current||!kind||prior.kind!==kind||prior.owner!==current.owner||prior.anchor_id!==current.anchor_id||current.elapsed_ms<=prior.elapsed_ms||current.elapsed_ms-prior.elapsed_ms>5000){await this.finish();return;}
  if(this.open&&(this.open.body.kind!==kind||current.elapsed_ms-this.open.body.start_ms>30000))await this.finish();
  if(this.open){
   const id=this.open.body.operation_id;
   const updated=await vault.update(current.owner,'activity-prefixes',(rows:any)=>{rows||={};if(rows[id]&&!rows[id].sealed){rows[id].body={...rows[id].body,end_ms:current.elapsed_ms};rows[id].at=Date.now();}return rows;});
   if(!updated[id]||updated[id].sealed)this.open=null;
   else this.open.body=updated[id].body;
  }
  if(!this.open){
   const body={schema_version:1,operation_id:crypto.randomUUID(),anchor_id:clock!.anchor_id,installation_id:clock!.installation_id,start_ms:prior.elapsed_ms,end_ms:current.elapsed_ms,kind};
   await vault.update(current.owner,'activity-prefixes',(rows:any)=>({...rows,[body.operation_id]:{body,at:Date.now(),sealed:false}}));
   this.open={owner:current.owner,body};
  }
  if(this.open.body.end_ms-this.open.body.start_ms>=28000)await this.finish();
 }
}

