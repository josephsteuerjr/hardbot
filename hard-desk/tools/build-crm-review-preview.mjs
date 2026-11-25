// Build only the renderer into an explicit local QA directory. No installer/feed writes.
import {build} from 'esbuild';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
const desk=resolve(dirname(fileURLToPath(import.meta.url)),'..');
if(!process.argv[2])throw new Error('Explicit QA output directory required');
const out=resolve(process.argv[2]);
if(out===desk || out.startsWith('U:'))throw new Error('Use a separate local QA directory');
mkdirSync(out,{recursive:true});
await build({entryPoints:[join(desk,'ui/crm-app.ts')],outfile:join(out,'app.js'),bundle:true,format:'iife',target:'chrome130'});
await build({entryPoints:[join(desk,'ui/styles/crm-pilot.css')],outfile:join(out,'app.css'),bundle:true,loader:{'.woff2':'file'},assetNames:'fonts/[name]'});
writeFileSync(join(out,'index.html'),readFileSync(join(desk,'ui/crm-index.html')));
console.log(out);
