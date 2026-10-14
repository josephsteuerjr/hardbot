import { contextBridge, ipcRenderer } from "electron";
contextBridge.exposeInMainWorld("setup", {
  state: () => ipcRenderer.invoke("setup:state"),
  choose: (kind: "folder" | "file") => ipcRenderer.invoke("setup:choose", kind),
  apply: (source: string) => ipcRenderer.invoke("setup:apply", source),
  finish: (launch: boolean) => ipcRenderer.invoke("setup:finish", launch),
  window: (action: string) => ipcRenderer.send("setup:window", action),
  progress: (fn: (text: string) => void) => ipcRenderer.on("setup:progress", (_e, text) => fn(text)),
});
