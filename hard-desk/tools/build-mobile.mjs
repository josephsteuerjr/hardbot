import {build} from 'esbuild';
import {cpSync,mkdirSync,readFileSync,writeFileSync,readdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {dirname,resolve,join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
const desk=resolve(dirname(fileURLToPath(import.meta.url)),'..');
// The source static directory can be served by a running preview. Never publish
// into it implicitly when a caller forgot an environment variable or path failed.
if(!process.env.HARD_CRM_MOBILE_OUT?.trim())throw Error('Set HARD_CRM_MOBILE_OUT to an explicit isolated build directory.');
const out=resolve(process.env.HARD_CRM_MOBILE_OUT);
mkdirSync(out,{recursive:true});
await build({entryPoints:[join(desk,'ui/mobile/main.ts')],outfile:join(out,'app.js'),bundle:true,format:'iife',target:['safari16.4','chrome110'],logLevel:'warning'});
await build({entryPoints:[join(desk,'ui/mobile/styles.css')],outfile:join(out,'app.css'),bundle:true,loader:{'.woff2':'file','.svg':'dataurl'},assetNames:'fonts/[name]-[hash]',logLevel:'warning'});
cpSync(join(desk,'ui/mobile/index.html'),join(out,'index.html'));cpSync(join(desk,'icons/icon.svg'),join(out,'icon.svg'));
cpSync(join(desk,'icons/icon-512.png'),join(out,'icon-512.png'));
cpSync(join(desk,'ui/licenses'),join(out,'licenses'),{recursive:true});
writeFileSync(join(out,'manifest.webmanifest'),JSON.stringify({id:'./',name:'Взыскание',short_name:'Взыскание',start_url:'./',scope:'./',display:'standalone',background_color:'#f8eee0',theme_color:'#f8eee0',icons:[{src:'icon-512.png',sizes:'512x512',type:'image/png',purpose:'any'},{src:'icon.svg',sizes:'any',type:'image/svg+xml',purpose:'any'}]},null,2));
function files(dir){return readdirSync(dir,{withFileTypes:true}).flatMap(x=>x.isDirectory()?files(join(dir,x.name)):[join(dir,x.name)]);}
const assets=files(out).filter(p=>!['sw.js','BUILD.json'].includes(relative(out,p))).map(p=>'./'+relative(out,p).replaceAll('\\','/')).sort();
const version=createHash('sha256').update('static-shell-v2\n'+assets.map(p=>createHash('sha256').update(readFileSync(join(out,p))).digest('hex')).join('')).digest('hex').slice(0,16);
writeFileSync(join(out,'sw.js'),`const CACHE='crm-mobile-${version}',ASSETS=${JSON.stringify(assets)};self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS))));self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('crm-mobile-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));self.addEventListener('fetch',e=>{const u=new URL(e.request.url),scope=new URL(self.registration.scope),name='./'+u.pathname.slice(scope.pathname.length);if(e.request.method!=='GET'||u.origin!==self.location.origin)return;if(e.request.mode==='navigate'||u.pathname===scope.pathname||name==='./index.html'){e.respondWith(caches.open(CACHE).then(c=>c.match('./index.html')).then(r=>r||fetch(e.request)));return;}if(ASSETS.includes(name))e.respondWith(caches.open(CACHE).then(c=>c.match(e.request)).then(r=>r||fetch(e.request)));});`);
writeFileSync(join(out,'BUILD.json'),JSON.stringify({version,assets:assets.length,entry:'ui/mobile/main.ts',built_at:new Date().toISOString()},null,2));
console.log('Mobile bundle built: '+version+' / '+assets.length+' assets');
