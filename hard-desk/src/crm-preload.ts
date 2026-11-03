import { contextBridge, ipcRenderer } from "electron";
contextBridge.exposeInMainWorld("crm", {
  state: () => ipcRenderer.invoke("crm:state"),
  api: (method: string, action: string, body?: unknown, query?: unknown) =>
    ipcRenderer.invoke("crm:api", method, action, body, query),
  command: (body: unknown) => ipcRenderer.invoke("crm:command", body),
  queue: (action?: string, id?: string) =>
    ipcRenderer.invoke("crm:queue", action, id),
  draft: (key: string, value?: unknown) =>
    ipcRenderer.invoke("crm:draft", key, value),
  telemetry: (event: unknown) => ipcRenderer.invoke("crm:telemetry", event),
  connect: (input: unknown) => ipcRenderer.invoke("crm:connect", input),
  workbook: (action: string, input?: unknown) =>
    ipcRenderer.invoke("crm:workbook", action, input),
  document: (contract: string, id?: string) =>
    ipcRenderer.invoke("crm:document", contract, id),
  win: (action: string) => ipcRenderer.send("crm:win", action),
});
