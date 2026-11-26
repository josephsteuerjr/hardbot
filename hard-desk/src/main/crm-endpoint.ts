import {app,net} from 'electron';
import {readFileSync,renameSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {CRM_REHEARSAL,DEFAULT_SERVER} from './product';
import {fetchBuffered} from './transport';

const rehearsalOrigin = 'http://127.0.0.1:18765';
const rehearsalId = 'crm-rehearsal-20261126';
const settingsFile = () => join(app.getPath('userData'),'rehearsal-origin.json');
let selected: string | undefined;

function validatedOrigin(value: string): string {
  const url = new URL(value.trim());
  if(url.hostname !== '127.0.0.1' || url.protocol !== 'http:' || !url.port ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash)
    throw Error('Укажите локальный адрес стенда в формате http://127.0.0.1:порт.');
  return url.origin;
}

export function primaryOrigin(): string {
  if(!CRM_REHEARSAL)return DEFAULT_SERVER;
  if(!selected){
    try{selected=validatedOrigin(JSON.parse(readFileSync(settingsFile(),'utf8')).origin);}
    catch{selected=rehearsalOrigin;}
  }
  return selected;
}

export const pilotBase = () => primaryOrigin() + '/crm-pilot';

export async function confirmRehearsal(origin = primaryOrigin()): Promise<void> {
  if(!CRM_REHEARSAL)return;
  const response=await fetchBuffered((url,init)=>net.fetch(url,init),validatedOrigin(origin)+'/rehearsal-health',
    {method:'GET',redirect:'error'},5000);
  const value=await response.json();
  if(!response.ok || value.rehearsal_id!==rehearsalId || value.schema_version!==10)
    throw Error('Сервер не подтвердил тестовый контур CRM на схеме 10.');
}

export async function setRehearsalOrigin(value: string, allowChange: () => boolean) {
  if(!CRM_REHEARSAL)throw Error('Выбор адреса доступен в тестовой CRM.');
  const origin=validatedOrigin(value);
  await confirmRehearsal(origin);
  if(!allowChange())throw Error('Перед сменой адреса выйдите из учётной записи.');
  const file=settingsFile();
  writeFileSync(file+'.tmp',JSON.stringify({origin}),{flush:true});
  renameSync(file+'.tmp',file);
  selected=origin;
  return origin;
}
