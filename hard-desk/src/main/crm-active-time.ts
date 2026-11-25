import {randomUUID} from 'node:crypto';
import * as queue from './crm-queue';

type Sample={owner:string; anchor_id:string; installation_id:string; elapsed:number; kind:'active'|'idle'|null; authority?:queue.AuthorityBinding};
/** Samples bounded monotonic intervals. Never extrapolates across sleep/crash. */
export class ActiveTime {
  private previous:Sample|null=null;
  private open:{owner:string;body:any;authority?:queue.AuthorityBinding}|null=null;
  private seen=new Set<string>();
  constructor(private sample:()=>Sample|null){}
  private finish(){
    if(!this.open)return;
    queue.enqueue(this.open.owner,this.open.body,'active-time',this.open.authority);
    queue.draft(this.open.owner,'active-open',null);
    this.open=null;
  }
  tick(){
    const current=this.sample();
    if(current && !this.seen.has(current.owner)){
      const old=queue.draft(current.owner,'active-open');
      if(old){queue.enqueue(current.owner,old,'active-time',current.authority);queue.draft(current.owner,'active-open',null);}
      this.seen.add(current.owner);
    }
    const prior=this.previous;this.previous=current;
    if(!current || !prior || current.owner!==prior.owner || current.anchor_id!==prior.anchor_id
        || !current.kind || current.kind!==prior.kind || current.elapsed<=prior.elapsed || current.elapsed-prior.elapsed>5000){
      this.finish();return;
    }
    if(this.open && (this.open.owner!==current.owner || this.open.body.kind!==current.kind
        || this.open.body.anchor_id!==current.anchor_id || current.elapsed-this.open.body.start_ms>30000))this.finish();
    if(!this.open)this.open={owner:current.owner,authority:current.authority,body:{schema_version:1,operation_id:randomUUID(),anchor_id:current.anchor_id,
      installation_id:current.installation_id,start_ms:prior.elapsed,end_ms:current.elapsed,kind:current.kind}};
    else this.open.body.end_ms=current.elapsed;
    // A crash recovers only this proven prefix, never time after its checkpoint.
    queue.draft(this.open.owner,'active-open',this.open.body);
    if(this.open.body.end_ms-this.open.body.start_ms>=28000)this.finish();
  }
  stop(){this.finish();this.previous=null;}
}
