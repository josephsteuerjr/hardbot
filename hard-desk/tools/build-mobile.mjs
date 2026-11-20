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
const generated=/^(?:sw\.js|sw-[a-f0-9]{16}\.js|BUILD\.json|app-[a-f0-9]{16}\.(?:js|css))$/;
const inputs=files(out).filter(p=>!generated.test(relative(out,p))).map(p=>'./'+relative(out,p).replaceAll('\\','/')).sort();
const worker=readFileSync(join(desk,'ui/mobile/service-worker.js'),'utf8');
const version=createHash('sha256').update('static-shell-v4\n'+worker+inputs.map(p=>createHash('sha256').update(readFileSync(join(out,p))).digest('hex')).join('')).digest('hex').slice(0,16);
const names={js:'app-'+version+'.js',css:'app-'+version+'.css'};
for(const ext of ['js','css'])cpSync(join(out,'app.'+ext),join(out,names[ext]));
writeFileSync(join(out,'index.html'),readFileSync(join(out,'index.html'),'utf8').replace('<head>','<head><meta name="crm-build" content="'+version+'">').replace('href="app.css"','href="'+names.css+'"').replace('src="app.js"','src="'+names.js+'"'));
const assets=[...inputs.filter(p=>p!=='./app.js'&&p!=='./app.css'),'./'+names.js,'./'+names.css].sort();
const hashes=Object.fromEntries(assets.map(name=>[name,createHash('sha256').update(readFileSync(join(out,name))).digest('hex')]));
const builtWorker=worker.replace('__CRM_VERSION__',JSON.stringify(version)).replace('__CRM_ASSETS__',JSON.stringify(assets)).replace('__CRM_HASHES__',JSON.stringify(hashes));
for(const name of ['sw.js','sw-'+version+'.js'])writeFileSync(join(out,name),builtWorker);
writeFileSync(join(out,'BUILD.json'),JSON.stringify({version,assets:assets.length,entry:'ui/mobile/main.ts',built_at:new Date().toISOString()},null,2));
console.log('Mobile bundle built: '+version+' / '+assets.length+' assets');
