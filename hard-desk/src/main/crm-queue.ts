import { app, safeStorage } from "electron";
import { createHash, randomUUID } from "node:crypto";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
  copyFileSync,
} from "node:fs";
import { dirname, join, parse } from "node:path";
import { directSealCheckpoint, isCrmRecord, openCrmRecord, sealCrmRecord } from "./windows-dpapi";

export interface Result {
  ok: boolean;
  status: number;
  data?: any;
  text?: string;
  kind?: string;
  stale?: string;
}
export type Transport = (
  method: string,
  action: string,
  body?: any,
  query?: any,
) => Promise<Result>;
type Entry = {
  action?: "command" | "receipt" | "registry-edit" | "active-time";
  body: any;
  queued_at: string;
  state: "pending" | "attention" | "accepted";
  result?: any;
  error?: string;
  code?: string;
  reviewed_at?:string;
};
type Business = { schema: 1; owner: string; entries: Entry[]; importedSoft?: Record<string,string> };
type Technical = { events: any[]; dropped: number; gap_since?: string };
const digest = (v: string) => createHash("sha256").update(v).digest("hex");
const path = (name: string) =>
  join(app.getPath("userData"), "crm", name + ".bin");
const recentlyDecoded = new Map<string, { bytes: Buffer; json: string }>();
function remember(target: string, bytes: Buffer, json: string) {
  recentlyDecoded.delete(target);
  if (bytes.length > 64 * 1024) return;
  recentlyDecoded.set(target, { bytes, json });
  if (recentlyDecoded.size > 16)
    recentlyDecoded.delete(recentlyDecoded.keys().next().value!);
}
function read<T>(name: string, fallback: () => T): T {
  return readAt(path(name),fallback);
}
function readAt<T>(target:string,fallback:()=>T):T{
  if (!existsSync(target)) {
    recentlyDecoded.delete(target);
    return fallback();
  }
  // A damaged or inaccessible protected queue is an error, never an empty queue.
  const bytes = readFileSync(target);
  const previous = recentlyDecoded.get(target);
  if (previous?.bytes.equals(bytes)) return JSON.parse(previous.json);
  recentlyDecoded.delete(target);
  const json = isCrmRecord(bytes) ? openCrmRecord(bytes) : safeStorage.decryptString(bytes);
  const value = JSON.parse(json);
  remember(target, bytes, json);
  return value;
}
function write(name: string, value: any, primary?: typeof directSealCheckpoint) {
  mkdirSync(join(app.getPath("userData"), "crm"), { recursive: true });
  const target = path(name);
  const json = JSON.stringify(value);
  const bytes = sealCrmRecord(json, primary);
  writeFileSync(target + ".tmp", bytes, {
    flush: true,
  });
  renameSync(target + ".tmp", target);
  remember(target, bytes, json);
}
let installationId:string|undefined;
export function installation(): string {
  if(installationId)return installationId;
  const fresh = () => ({ id: randomUUID() });
  let name = "installation";
  let value: { id: string };
  try {
    // Keep using a recovery identity after a restart if one was needed.
    if (existsSync(path('installation-recovery'))) name = 'installation-recovery';
    value = read(name, fresh);
  } catch (error) {
    if (name !== 'installation') throw error;
    // An unreadable installation identifier must not block the login screen.
    // Leave the original and all business files in place.
    name = 'installation-recovery';
    value = read(name, fresh);
  }
  if(!existsSync(path(name)))write(name, value);
  return installationId=value.id;
}
export function cache(owner: string, key: string, value?: any): any {
  const name = "cache-" + digest(owner + "\n" + key);
  if (value !== undefined){
    const record={at:new Date().toISOString(),data:value};
    write(name,record);
    return record;
  }
  try{return read(name, () => null);}catch{
    // Only recreatable cache files enter this path. Business/drafts still fail
    // closed. Retain and verify exact bytes before moving the damaged cache.
    const target=path(name),before=readFileSync(target);
    const backup=target+'.damaged-'+digest(before.toString('base64'));
    if(!existsSync(backup))copyFileSync(target,backup);
    if(!readFileSync(backup).equals(before) || !readFileSync(target).equals(before))throw Error('Кэш изменился во время восстановления.');
    renameSync(target,target+'.quarantined-'+randomUUID());
    return null;
  }
}
const queueName = (owner: string) => "business-" + digest(owner);
const changeListeners = new Set<(owner: string) => void>();
export function onChange(listener: (owner: string) => void) {
  changeListeners.add(listener);
  return () => { changeListeners.delete(listener); };
}
function changed(owner: string) {
  for (const listener of changeListeners) {
    try { listener(owner); } catch { /* A closed UI cannot invalidate a durable save. */ }
  }
}
function business(owner: string): Business {
  const result = read<Business>(queueName(owner), () => ({
    schema: 1,
    owner,
    entries: [],
  }));
  if (
    result.owner !== owner ||
    result.schema !== 1 ||
    !Array.isArray(result.entries)
  )
    throw new Error(
      "Файл очереди требует восстановления. Исходный файл сохранён.",
    );
  return result;
}
export function queueState(owner: string) {
  return business(owner);
}
const entryId = (body: any) => body.operation_id || body.receipt_id;

function softProfileFile(name:string):string|undefined {
  const home=process.env.LOCALAPPDATA;
  if(process.platform!=='win32'||!home||app.getPath('userData')!==join(home,'Hard','hard','data'))return;
  const target=join(home,'Hard','soft','data','crm',name+'.bin');
  if(!existsSync(target))return;
  for(let cursor=target;cursor!==parse(cursor).root;cursor=dirname(cursor)){
    if(lstatSync(cursor).isSymbolicLink())throw Error('Папка прежней очереди перенаправлена. Исходные файлы сохранены.');
  }
  if(!lstatSync(target).isFile())throw Error('Файл прежней очереди требует сверки.');
  return target;
}

export function importSoftQueue(owner:string){
  const name=queueName(owner),source=softProfileFile(name);
  if(!source)return;
  const previous=readAt<Business|null>(source,()=>null);
  if(!previous||previous.schema!==1||previous.owner!==owner||!Array.isArray(previous.entries))
    throw Error('Не удалось подтвердить владельца прежней очереди.');
  const queue=business(owner);
  const merged=mergeBusinessQueues(queue,previous);
  if(merged!==queue){write(name,merged);changed(owner);}
}

export function mergeBusinessQueues(queue:Business,previous:Business):Business{
  if(previous.owner!==queue.owner)throw Error('Прежняя очередь принадлежит другой учётной записи.');
  const entries=new Map(queue.entries.map(entry=>[entryId(entry.body),entry]));
  const imported={...queue.importedSoft};
  const rank={pending:0,attention:1,accepted:2};
  const signature=(entry:Entry)=>digest(JSON.stringify([entry.action||'command',entry.body]));
  let changed=false;
  for(const entry of previous.entries){
    const id=entryId(entry.body);
    if(typeof id!=='string'||!/^[A-Za-z0-9_-]{12,100}$/.test(id)||!Object.hasOwn(rank,entry.state))
      throw Error('Прежняя очередь содержит операцию неизвестного формата.');
    const expected=signature(entry),existing=entries.get(id);
    if((imported[id]&&imported[id]!==expected)||(existing&&signature(existing)!==expected))
      throw Error('Две записи имеют одинаковый ID и разное содержимое. Исходные очереди сохранены.');
    if((!imported[id]&&!existing)||(existing&&rank[entry.state]>rank[existing.state])){
      entries.set(id,entry);changed=true;
    }
    if(!imported[id]){imported[id]=expected;changed=true;}
  }
  return changed?{...queue,entries:[...entries.values()],importedSoft:imported}:queue;
}
export function enqueue(
  owner: string,
  body: any,
  action: "command" | "receipt" | "registry-edit" | "active-time" = "command",
) {
  if (
    typeof entryId(body) !== "string" ||
    !/^[A-Za-z0-9_-]{12,100}$/.test(entryId(body))
  )
    throw new Error("Некорректный ID операции.");
  if (
    Buffer.byteLength(JSON.stringify(body)) >
    (action === "receipt" ? 4 * 1024 * 1024 : 256 * 1024)
  )
    throw new Error("Операция слишком большая.");
  const queue = business(owner),
    previous = queue.entries.find((e) => entryId(e.body) === entryId(body));
  if (previous) {
    if (
      (previous.action || "command") !== action ||
      JSON.stringify(previous.body) !== JSON.stringify(body)
    )
      throw new Error("ID операции уже содержит другие данные.");
    return previous;
  }
  if (queue.entries.filter((e) => e.state !== "accepted").length >= 5000)
    throw new Error(
      "Очередь заполнена. Восстановите связь; сохранённые операции остаются на компьютере.",
    );
  const entry: Entry = {
    action,
    body,
    queued_at: new Date().toISOString(),
    state: "pending",
  };
  queue.entries.push(entry);
  write(queueName(owner), queue);
  changed(owner);
  return entry;
}
const running = new Map<string, Promise<void>>();
export function flush(
  owner: string,
  send: Transport,
  current: () => string,
): Promise<void> {
  if (running.has(owner)) return running.get(owner)!;
  const work = async () => {
    if (current() !== owner) return;
    // Access is freshly checked before each replay. Cached cards never authorize a write.
    const context = await send("GET", "context");
    if (!context.ok || current() !== owner) return;
    for (const snapshot of business(owner).entries.filter(
      (e) => e.state === "pending",
    )) {
      if (current() !== owner) return;
      const response = await send(
        "POST",
        snapshot.action || "command",
        snapshot.body,
      );
      if (current() !== owner) return;
      if (
        response.status === 0 ||
        response.status === 408 ||
        response.status === 429 ||
        response.status >= 500
      )
        return;
      const queue = business(owner),
        entry = queue.entries.find(
          (e) => entryId(e.body) === entryId(snapshot.body),
        );
      if (!entry || entry.state !== "pending") continue;
      if (
        response.ok &&
        ((entry.action === "receipt" &&
          response.data?.ok === true &&
          response.data?.receipt_id === entry.body.receipt_id) ||
          (entry.action !== "receipt" &&
            response.data?.operation_id === entry.body.operation_id &&
            response.data?.status === "accepted"))
      ) {
        entry.state = "accepted";
        entry.result = response.data;
      } else {
        entry.state = "attention";
        entry.error = response.text || "Сервер не подтвердил операцию.";
        entry.code = response.data?.error || "unknown";
      }
      // Retain all unresolved work and a bounded history of exact acknowledgements.
      const accepted = queue.entries.filter((e) => e.state === "accepted");
      if (accepted.length > 1000) {
        const old = new Set(accepted.slice(0, accepted.length - 1000));
        queue.entries = queue.entries.filter((e) => !old.has(e));
      }
      write(queueName(owner), queue);
      changed(owner);
      if (response.status === 401 || response.status === 403) return;
    }
  };
  const promise = work().finally(() => running.delete(owner));
  running.set(owner, promise);
  return promise;
}
export function retry(owner: string, id: string) {
  const queue = business(owner),
    entry = queue.entries.find((e) => entryId(e.body) === id);
  if (entry?.state === "attention" && !entry.reviewed_at) {
    entry.state = "pending";
    delete entry.error;
    delete entry.code;
    write(queueName(owner), queue);
    changed(owner);
  }
}

export function reviewRegistry(owner:string,id:string){
  const q=business(owner),entry=q.entries.find(e=>entryId(e.body)===id);
  if(!entry || entry.action!=='registry-edit' || entry.state!=='attention')throw Error('Отправка не требует сверки.');
  const saved=draft(owner,'registry-input');
  if(!entry.body.rows.every((row:any)=>Object.entries(row.values).every(([key,value])=>saved?.[row.contract_uuid]?.values?.[key]===value)))
    throw Error('Сначала сохраните полный пакет в защищённый черновик.');
  entry.reviewed_at=new Date().toISOString();
  write(queueName(owner),q);changed(owner);
}

export function technical(owner: string, event: any) {
  const name = "technical-" + digest(owner),
    queue = read<Technical>(name, () => ({ events: [], dropped: 0 }));
  if (queue.events.length >= 1000) {
    queue.events.shift();
    queue.dropped++;
    queue.gap_since ||= new Date().toISOString();
  }
  queue.events.push(event);
  write(name, queue);
}
const technicalRunning = new Set<string>();
const backoff = new Map<string, { until: number; attempt: number }>();
export async function flushTechnical(
  owner: string,
  send: Transport,
  current: () => string,
) {
  if (
    technicalRunning.has(owner) ||
    current() !== owner ||
    (backoff.get(owner)?.until || 0) > Date.now()
  )
    return;
  technicalRunning.add(owner);
  try {
    const name = "technical-" + digest(owner),
      queue = read<Technical>(name, () => ({ events: [], dropped: 0 }));
    if (queue.dropped) {
      queue.events.push({
        schema_version: 1,
        event_id: randomUUID(),
        event_type: "queue_gap",
        producer: "desktop",
        installation_id: installation(),
        session_id: randomUUID(),
        client_version: app.getVersion(),
        feature: "shell",
        occurred_at: queue.gap_since,
        dropped_count: queue.dropped,
      });
      queue.dropped = 0;
      delete queue.gap_since;
      write(name, queue);
    }
    const batch = queue.events.slice(0, 100);
    if (!batch.length) return;
    const response = await send("POST", "telemetry", { events: batch });
    if (current() !== owner) return;
    if (response.ok && Array.isArray(response.data?.accepted_ids)) {
      const ids = new Set(response.data.accepted_ids),
        fresh = read<Technical>(name, () => ({ events: [], dropped: 0 }));
      fresh.events = fresh.events.filter((e) => !ids.has(e.event_id));
      write(name, fresh);
      backoff.delete(owner);
    } else {
      const attempt = Math.min(8, (backoff.get(owner)?.attempt || 0) + 1);
      backoff.set(owner, {
        attempt,
        until:
          Date.now() +
          Math.min(300000, 2000 * 2 ** attempt) * (0.8 + Math.random() * 0.4),
      });
    }
  } finally {
    technicalRunning.delete(owner);
  }
}

export function draft(owner: string, key: string, value?: any): any {
  const name = "draft-" + digest(owner + "\n" + key);
  if (value !== undefined) write(name, value, key === "active-open" ? directSealCheckpoint : undefined);
  else if(owner&&key!=='live-cursor'&&!existsSync(path(name))){
    const source=softProfileFile(name);
    if(source){
      const draft=readAt(source,()=>null);
      if(draft!==null)write(name,draft,key==='active-open'?directSealCheckpoint:undefined);
    }
  }
  return read<any>(name, () => null);
}
