import {join} from 'node:path';
type Dependencies={
  identity():{owner:string;employee:string;allowed:boolean}|null;
  load(owner:string):any;
  save(owner:string,value:any):void;
  source():Promise<any>;
  write(report:any,path:string):Promise<any>;
  desktop:string;
};
export class ThreeHourReports {
  private busy=false;
  constructor(private deps:Dependencies){}
  async tick(at=new Date()) {
    if(this.busy)return;
    const identity=this.deps.identity();if(!identity?.allowed)return;
    const owner=identity.owner;
    const valid=()=>this.deps.identity()?.owner===owner;
    const slot=new Date(Math.floor(at.getTime()/10800000)*10800000).toISOString();
    let state=this.deps.load(owner) || {};
    if(state.slot===slot&&state.receipt)return;
    // Resume a captured source/file before moving to the next interval.
    if(!state.report||state.receipt)state={slot};
    this.busy=true;
    try{
      if(!state.report){
        state.report=await this.deps.source();if(!valid())return;
        state.path=join(this.deps.desktop,'Трёхчасовые сводки',`3-часовой свод ${state.slot.slice(0,13).replace(/[-:T]/g,'')}-${identity.employee.slice(0,8)}.xlsx`);
        this.deps.save(owner,state);
      }
      if(!valid())return;
      const result=await this.deps.write(state.report,state.path);
      if(!result?.ok||!result.data?.verified)throw new Error(result?.text||'Не подтверждено сохранение отчёта.');
      state.receipt=result.data;state.error='';
    }catch(error){state.error=(error as Error).message;state.last_attempt=at.toISOString();}
    finally{if(valid())this.deps.save(owner,state);this.busy=false;}
  }
}
