import type {BrowserWindow} from 'electron';
import {existsSync} from 'node:fs';
import {join} from 'node:path';
import {execFile} from 'node:child_process';

/** Apply the owned-window DWM attributes without depending on Python or login. */
export async function applyCrmWindowFrame(window:BrowserWindow):Promise<boolean>{
  if(process.platform!=='win32')return true;
  const helper=[join(process.resourcesPath,'process-check.exe'),
    join(__dirname,'..','installer','bin','process-check.exe')].find(existsSync);
  if(!helper)return false;
  return new Promise(resolve=>execFile(helper,['window',window.getNativeWindowHandle().readBigUInt64LE().toString(),
    String(process.pid)],{windowsHide:true,timeout:8000},error=>resolve(!error)));
}
