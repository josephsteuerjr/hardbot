// Мост окна: странице — узкий набор действий, без Node и без токена.
import { contextBridge, ipcRenderer } from "electron";

type Query = Record<string, unknown> | undefined;

contextBridge.exposeInMainWorld("hard", {
  state: () => ipcRenderer.invoke("hard:state"),
  enroll: (link: string) => ipcRenderer.invoke("hard:enroll", link),
  logout: () => ipcRenderer.invoke("hard:logout"),
  api: (method: string, path: string, query?: Query, body?: unknown) => ipcRenderer.invoke("hard:api", method, path, query, body),
  image: (path: string, query?: Query) => ipcRenderer.invoke("hard:image", path, query),
  download: (path: string, query: Query, name: string) => ipcRenderer.invoke("hard:download", path, query, name),
  open: (file: string) => ipcRenderer.invoke("hard:open", file),
  copy: (text: string) => ipcRenderer.invoke("hard:copy", text),
  theme: (t: string) => ipcRenderer.invoke("hard:theme", t),
  shot: (file: string) => ipcRenderer.invoke("hard:shot", file),
  local: (path: string, body?: unknown) => ipcRenderer.invoke("hard:local", path, body),
  pick: (kind: string, opts?: unknown) => ipcRenderer.invoke("hard:pick", kind, opts),
  win: (action: string) => ipcRenderer.send("hard:win", action),
  on: (name: "revoked" | "win", cb: (arg?: unknown) => void) => {
    ipcRenderer.on("hard:" + name, (_e, arg) => cb(arg));
  },
});
