/** Authenticated, durable invalidation polling; no credentials leave main. */
export class CrmLive {
  private timer:ReturnType<typeof setTimeout>|undefined;
  private generation=0;
  constructor(private send:(query:any)=>Promise<any>,private current:()=>string,
    private load:(owner:string)=>any,private save:(owner:string,cursor:any)=>void,
    private notify:(data:any)=>void){}
  start(){
    this.stop();const generation=this.generation,owner=this.current();if(!owner)return;
    let cursor=this.load(owner) || {assignments:0,facts:0},failures=0;
    const tick=async()=>{
      if(generation!==this.generation || owner!==this.current())return;
      let delay=1000;
      try{
        const result=await this.send(cursor);
        if(generation!==this.generation || owner!==this.current())return;
        if(result.ok && !result.stale){
          const next=result.data.cursor;
          if(JSON.stringify(next)!==JSON.stringify(cursor))this.save(owner,next);
          cursor=next;failures=0;
          this.notify({type:'live',dirty:!!result.data.dirty,resync:!!result.data.resync,as_of:result.data.as_of,connected:true});
        }else{
          failures++;delay=Math.min(4000,1000*2**Math.min(failures,2));
          this.notify({type:'live',dirty:false,connected:false});
          if([401,403,404].includes(result.status))return;
        }
      }catch{failures++;delay=4000;this.notify({type:'live',dirty:false,connected:false});}
      if(generation===this.generation && owner===this.current())this.timer=setTimeout(tick,delay);
    };
    this.timer=setTimeout(tick,50);
  }
  stop(){clearTimeout(this.timer);this.generation++;}
}
