import { build } from 'esbuild';
import { extractFile, listPackage } from '@electron/asar';
import { nativeHelperDigest } from './native-helper-digest.mjs';
import { auditRuntimeDependencies } from './runtime-dependencies.mjs';
import { readFileSync, existsSync, readdirSync, mkdtempSync, rmSync } from 'node:fs';
import { dirname, join, resolve, relative, normalize, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { products } from './products.mjs';
import { releaseUrl } from './products.mjs';
import yaml from 'js-yaml';
const here = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--assets')) throw new Error('Usage: audit-installers.mjs [--assets directory]');
const assetDirectory=args.length ? resolve(here,args[1]) : null;
const sha = x => createHash('sha256').update(x).digest('hex');
function guid(value){const b=createHash('sha1').update(Buffer.from('50e065bc313411e69bab38c9862bdaf3','hex')).update(value).digest().subarray(0,16);b[6]=(b[6]&15)|80;b[8]=(b[8]&63)|128;const s=b.toString('hex');return [s.slice(0,8),s.slice(8,12),s.slice(12,16),s.slice(16,20),s.slice(20)].join('-');}
const productionRegistry=Object.fromEntries(Object.entries(products).map(([id,p])=>[id,guid(p.appId)]));
const stale = [];
const version=JSON.parse(readFileSync(join(here,'package.json'))).version;
const backendManifest=JSON.parse(execFileSync(process.env.HARD_BUILD_PYTHON || 'python',
  [join(here,'tools/backend_sources.py')],{encoding:'utf8'}));
const nodeOptions={bundle:true,platform:'node',format:'cjs',target:'node22',external:['electron','electron-updater','original-fs'],logLevel:'warning',write:false};
const expectedUi = await build({ entryPoints: [join(here, 'ui/app.ts')], bundle: true, format: 'iife', target: ['chrome130'], logLevel: 'warning', write: false });
const preload=await build({...nodeOptions,entryPoints:[join(here,'src/preload.ts')]});
const tray=await build({entryPoints:[join(here,'ui/tray.ts')],bundle:true,format:'iife',target:['chrome130'],logLevel:'warning',write:false});
const styles=await build({entryPoints:[join(here,'ui/styles/index.css')],bundle:true,outfile:join(here,'out/ui/app.css'),
  loader:{'.woff2':'file','.svg':'dataurl'},assetNames:'fonts/[name]',logLevel:'warning',write:false});
const setupPreload=await build({...nodeOptions,entryPoints:[join(here,'src/setup/preload.ts')]});
const setupUi=await build({entryPoints:[join(here,'setup-ui/setup.ts')],bundle:true,format:'iife',target:['chrome130'],logLevel:'warning',write:false});
const setupStyles=await build({entryPoints:[join(here,'setup-ui/setup.css')],bundle:true,outfile:join(here,'out/setup/setup.css'),loader:{'.woff2':'file','.svg':'dataurl'},assetNames:'fonts/[name]',logLevel:'warning',write:false});
const setupStatic=[['out/setup-preload.js',setupPreload.outputFiles[0].contents],['out/setup/setup.js',setupUi.outputFiles[0].contents],['out/setup/index.html',readFileSync(join(here,'setup-ui/index.html'))],...setupStyles.outputFiles.map(f=>[relative(here,f.path),f.contents])];
const staticFiles=[['out/preload.js',preload.outputFiles[0].contents],['out/ui/tray.js',tray.outputFiles[0].contents],
  ...['index.html','tray.html'].map(name=>['out/ui/'+name,readFileSync(join(here,'ui',name))]),
  ...styles.outputFiles.map(file=>[relative(here,file.path),file.contents])];
staticFiles.push(...setupStatic);
function walk(dir){return readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(join(dir,e.name)):[join(dir,e.name)]);}
// Read the distributable EXE itself. A fresh win-unpacked next to an old EXE
// must not pass the release gate. Extraction executes no installer or app.
const sevenZip=join(here,'node_modules/electron-winstaller/vendor/7z-x64.exe');
if(!existsSync(sevenZip)) throw new Error('The local NSIS audit requires the bundled Windows 7-Zip');
const lab=mkdtempSync(join(tmpdir(),'hard-nsis-audit-'));
try {
for (const id of Object.keys(products)) {
  const installer=join(assetDirectory || join(here,'installers',id),`${id}-${version}-setup.exe`);
  if(!existsSync(installer)){stale.push(id);continue;}
  const extract=join(lab,id);let unpacked=join(extract,'application');
  execFileSync(sevenZip,['e','-y','-bd','-o'+extract,installer,'$PLUGINSDIR\\app-64.7z'],{stdio:'pipe'});
  const archive=join(extract,'app-64.7z');
  if(!existsSync(archive)) throw new Error('Missing NSIS application archive: '+id);
  execFileSync(sevenZip,['x','-y','-bd','-o'+unpacked,archive],{stdio:'pipe'});
  const component=join(unpacked,'resources/application-installer.exe');
  if(existsSync(component)){
    const outerAsar=join(unpacked,'resources/app.asar');
    const outerPackage=JSON.parse(extractFile(outerAsar,'package.json').toString());
    if(JSON.stringify(JSON.parse(extractFile(outerAsar,'out/setup-registry.json').toString()))!==JSON.stringify(productionRegistry))throw new Error('Non-production wizard registry identity: '+id);
    const setupExpected=await build({...nodeOptions,entryPoints:[join(here,'src/setup/entry.ts')],define:{__HARD_PRODUCT__:JSON.stringify(id)}});
    const receipt=JSON.parse(readFileSync(join(unpacked,'resources/setup-payload.json'),'utf8'));
    if(outerPackage.name!==`hard-setup-${id}`||outerPackage.version!==version||outerPackage.main!=='out/setup-main.js'
      ||receipt.product!==id||receipt.version!==version||receipt.sha512!==createHash('sha512').update(readFileSync(component)).digest('base64')
      ||sha(extractFile(outerAsar,'out/setup-main.js'))!==sha(setupExpected.outputFiles[0].contents)
      ||setupStatic.some(([name,bytes])=>sha(extractFile(outerAsar,normalize(name)))!==sha(bytes))) throw new Error('Custom wizard/source/component mismatch: '+id);
    if(nativeHelperDigest(readFileSync(join(unpacked,'resources/process-check.exe')))!==nativeHelperDigest(readFileSync(join(here,'installer/bin/process-check.exe'))))throw new Error('Stale wizard native helper: '+id);
    execFileSync(sevenZip,['e','-y','-bd','-o'+join(extract,'component'),component,'$PLUGINSDIR\\app-64.7z'],{stdio:'pipe'});
    const innerArchive=join(extract,'component/app-64.7z');if(!existsSync(innerArchive))throw new Error('Missing embedded application: '+id);
    unpacked=join(extract,'installed');execFileSync(sevenZip,['x','-y','-bd','-o'+unpacked,innerArchive],{stdio:'pipe'});
    if(JSON.stringify(JSON.parse(extractFile(join(unpacked,'resources/app.asar'),'out/setup-registry.json').toString()))!==JSON.stringify(productionRegistry))throw new Error('Non-production removal identity: '+id);
    if(sha(extractFile(join(unpacked,'resources/app.asar'),'out/setup-main.js'))!==sha(setupExpected.outputFiles[0].contents))throw new Error('Stale removal wizard: '+id);
    if(nativeHelperDigest(readFileSync(join(unpacked,'resources/process-check.exe')))!==nativeHelperDigest(readFileSync(join(here,'installer/bin/process-check.exe'))))throw new Error('Stale app native helper: '+id);
  }
  if(walk(unpacked).some(path=>/(?:^|[/\\])(?:_local|\.env(?:\..*)?|calls-pending\.bin|backend-settings\.json)(?:[/\\]|$)|\.(?:db|xlsb|xlsx|private\.pem)$/i.test(relative(unpacked,path))))
    throw new Error('Forbidden operational data in installer resources: '+id);
  const packed=join(unpacked,'resources/app.asar');
  if (!existsSync(packed)) throw new Error('Missing actual installer ASAR: '+id);
  auditRuntimeDependencies(packed);
  const expected = await build({ bundle: true, platform: 'node', format: 'cjs', target: 'node22', external: ['electron', 'electron-updater', 'original-fs'],
    logLevel: 'warning', entryPoints: [join(here, 'src/main/launcher.ts')], write: false, define: { __HARD_PRODUCT__: JSON.stringify(id) } });
  if (sha(extractFile(packed, join('out', 'main.js'))) !== sha(expected.outputFiles[0].contents)
    || sha(extractFile(packed, join('out', 'ui', 'app.js'))) !== sha(expectedUi.outputFiles[0].contents)
    || staticFiles.some(([name,bytes])=>sha(extractFile(packed,normalize(name)))!==sha(bytes))) stale.push(id);
  const paths = listPackage(packed);
  if (paths.some(p => /(?:^|[/\\])(?:_local|\.env|tests|tools|backend-build)(?:[/\\]|$)|\.db$|\.xlsb$|calls-pending\.bin$|settings\.json$|\.private\.pem$/i.test(p)))
    throw new Error('Forbidden build payload in ' + id);
  const pkg = JSON.parse(extractFile(packed, 'package.json').toString());
  if (pkg.name !== `hard-${id}` || pkg.version !== version) throw new Error('Wrong product identity in ' + id);
  const feed=yaml.load(readFileSync(join(unpacked,'resources/app-update.yml'),'utf8'));
  if(feed.provider!=='generic' || feed.url!==releaseUrl || feed.channel!==id)
    throw new Error('Wrong embedded GitHub update channel in '+id);
  if(id==='analyst'){
    const payload=join(unpacked,'resources/backend/_internal/project');
    if(!existsSync(payload)) throw new Error('Missing analyst source payload');
    const manifestFile=join(unpacked,'resources/backend/SOURCE-MANIFEST.json');
    if(!existsSync(manifestFile) || JSON.stringify(JSON.parse(readFileSync(manifestFile,'utf8')))!==JSON.stringify(backendManifest))
      throw new Error('Missing or stale analyst source manifest');
    const actual=walk(payload).map(path=>relative(payload,path).split(sep).join('/')).sort();
    const required=backendManifest.files.map(source=>source.path).sort();
    if(JSON.stringify(actual)!==JSON.stringify(required)) throw new Error('Incomplete or unexpected analyst source payload');
    for(const path of walk(payload)){
      const rel=relative(payload,path),source=resolve(here,'..',rel);
      if(!existsSync(source)||sha(readFileSync(path))!==sha(readFileSync(source))){
        if(!stale.includes(id)) stale.push(id);
      }
    }
  }
  console.log('Payload audited:', id);
  // Bound temporary disk use to one product. These files were extracted by
  // this run into its fresh private directory and every check above has passed.
  if (dirname(extract) !== lab) throw new Error('Unsafe product audit cleanup path');
  rmSync(extract, {recursive:true, force:true});
}
} finally {
  if(!resolve(lab).startsWith(resolve(tmpdir())+sep)) throw new Error('Unsafe audit cleanup path');
  rmSync(lab,{recursive:true,force:true});
}
console.log(JSON.stringify({ stale }));
if (stale.length) process.exitCode = 2;
