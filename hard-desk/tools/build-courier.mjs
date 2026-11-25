import {build} from 'esbuild';
import {mkdirSync,copyFileSync} from 'node:fs';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const desk=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const out=resolve(desk,'../server/miniapp/courier');
mkdirSync(out,{recursive:true});
await build({entryPoints:[join(desk,'ui/crm-delivery-browser.ts')],outfile:join(out,'courier.js'),
  bundle:true,format:'iife',target:'chrome100'});
copyFileSync(join(desk,'ui/crm-delivery.css'),join(out,'courier.css'));
console.log('Courier assets built');
