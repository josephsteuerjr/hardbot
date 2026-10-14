// Мост окна: странице — узкий набор действий, без Node и без токена.
import { contextBridge, ipcRenderer } from "electron";

type Query = Record<string, unknown> | undefined;

contextBridge.exposeInMainWorld("hard", {
  state: () => ipcRenderer.invoke("hard:state"),
  edit: (action: string) => ipcRenderer.send("hard:edit", action),
  update: (action: string) => ipcRenderer.invoke("hard:update", action),
  migrate: () => ipcRenderer.invoke("hard:migrate"),
  trayAction: (action: string) => ipcRenderer.send("hard:trayAction", action),
  setMode: (mode: string) => ipcRenderer.invoke("hard:setMode", mode),
  enroll: (link: string) => ipcRenderer.invoke("hard:enroll", link),
  accountLogin: (server: string, username: string, password: string) => ipcRenderer.invoke("hard:accountLogin", server, username, password),
  logout: () => ipcRenderer.invoke("hard:logout"),
  api: (method: string, path: string, query?: Query, body?: unknown) => ipcRenderer.invoke("hard:api", method, path, query, body),
  image: (path: string, query?: Query) => ipcRenderer.invoke("hard:image", path, query),
  upload: (path: string, query: Query, filePath: string, ext: string) => ipcRenderer.invoke("hard:upload", path, query, filePath, ext),
  download: (path: string, query: Query, name: string) => ipcRenderer.invoke("hard:download", path, query, name),
  open: (file: string) => ipcRenderer.invoke("hard:open", file),
  copy: (text: string) => ipcRenderer.invoke("hard:copy", text),
  theme: (t: string) => ipcRenderer.invoke("hard:theme", t),
  shot: (file: string) => ipcRenderer.invoke("hard:shot", file),
  local: (path: string, body?: unknown) => ipcRenderer.invoke("hard:local", path, body),
  pick: (kind: string, opts?: unknown) => ipcRenderer.invoke("hard:pick", kind, opts),
  xlsx: (name: string, headers: string[], rows: unknown[][]) => ipcRenderer.invoke("hard:xlsx", name, headers, rows),
  autostart: (on?: boolean) => ipcRenderer.invoke("hard:autostart", on),
  win: (action: string) => ipcRenderer.send("hard:win", action),
  on: (name: "revoked" | "win" | "update", cb: (arg?: unknown) => void) => {
    ipcRenderer.on("hard:" + name, (_e, arg) => cb(arg));
  },
});
