import { contextBridge, ipcRenderer } from "electron";
import type {UpdateState} from './main/updates';
contextBridge.exposeInMainWorld("crm", {
  ready: () => ipcRenderer.invoke('crm:ui-ready'),
  state: () => ipcRenderer.invoke("crm:state"),
  update: (action: 'state' | 'check' | 'install') => ipcRenderer.invoke('crm:update',action),
  onUpdate: (callback: (update: UpdateState) => void) => {
    const listener = (_: unknown, update: UpdateState) => callback(update);
    ipcRenderer.on('crm:update-state',listener);
    return () => ipcRenderer.removeListener('crm:update-state',listener);
  },
  api: (method: string, action: string, body?: unknown, query?: unknown) =>
    ipcRenderer.invoke("crm:api", method, action, body, query),
  command: (body: unknown) => ipcRenderer.invoke("crm:command", body),
  registryEdit: (body: unknown,expectedOwner?:string) => ipcRenderer.invoke("crm:registry-edit", body, expectedOwner),
  diagnostics: (repair=false) => ipcRenderer.invoke('crm:diagnostics', repair),
  diagnosticsReport: () => ipcRenderer.invoke('crm:diagnostics-report'),
  queue: (action?: string, id?: string,expectedOwner?:string) =>
    ipcRenderer.invoke("crm:queue", action, id,expectedOwner),
  onChange: (callback: (event: {type: 'queue' | 'identity' | 'live';dirty?:boolean;as_of?:string;connected?:boolean}) => void) => {
    if (typeof callback !== 'function') throw new Error('Нужен обработчик изменения CRM.');
    const listener = (_: unknown, event: any) => {
      if (event?.type === 'queue' || event?.type === 'identity') callback({type: event.type});
      else if(event?.type==='live')callback({type:'live',dirty:event.dirty===true,connected:event.connected===true,
        as_of:typeof event.as_of==='string'?event.as_of:undefined});
    };
    ipcRenderer.on('crm:changed', listener);
    return () => ipcRenderer.removeListener('crm:changed', listener);
  },
  draft: (key: string, value?: unknown, expectedOwner?:string) =>
    ipcRenderer.invoke("crm:draft", key, value, expectedOwner),
  telemetry: (event: unknown) => ipcRenderer.invoke("crm:telemetry", event),
  connect: (input: unknown) => ipcRenderer.invoke("crm:connect", input),
  workbook: (action: string, input?: unknown) =>
    ipcRenderer.invoke("crm:workbook", action, input),
  document: (contract: string, id?: string) =>
    ipcRenderer.invoke("crm:document", contract, id),
  admin:(action:string,args:unknown[],expectedOwner:string)=>ipcRenderer.invoke('crm:admin',action,args,expectedOwner),
  win: (action: string) => ipcRenderer.send("crm:win", action),
});
