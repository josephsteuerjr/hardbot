// Four local builds. This script never uploads or installs an application.
import { build, Platform, Arch } from 'electron-builder';
import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync,realpathSync,lstatSync,rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import yaml from 'js-yaml';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { includesBackend, products, releaseUrl, stableProducts, usesCrm } from './products.mjs';
import { auditRuntimeDependencies } from './runtime-dependencies.mjs';
const here = join(dirname(fileURLToPath(import.meta.url)), '..');
const qa = !!process.env.HARD_SETUP_QA_BUILD;
const localElectron = process.env.HARD_ELECTRON_DIST;
if (localElectron && readFileSync(join(localElectron, 'version'), 'utf8').trim() !== JSON.parse(readFileSync(join(here, 'package.json'))).devDependencies.electron)
  throw new Error('Local Electron distribution differs from the pinned version');
mkdirSync(join(here,'installer/bin'),{recursive:true});
execFileSync(join(process.env.WINDIR,'Microsoft.NET/Framework64/v4.0.30319/csc.exe'),['/nologo','/target:winexe','/platform:x64','/reference:System.Web.Extensions.dll','/reference:System.Security.dll','/out:'+join(here,'installer/bin/process-check.exe'),join(here,'installer/process-check.cs')],{stdio:'inherit'});
const selected = process.argv.slice(2);
for (const id of (selected.length ? selected : stableProducts)) {
  const p = products[id];
  if (!p) throw new Error('Unknown product: ' + id);
  if (includesBackend(id) && !existsSync(join(here, 'backend-dist/hard-backend/hard-backend.exe')))
    throw new Error('Build the analyst backend first: npm run backend');
  execFileSync(process.execPath, [join(here, 'build.mjs')], { cwd: here, env: { ...process.env, HARD_PRODUCT: id }, stdio: 'inherit' });
  await build({ targets: Platform.WINDOWS.createTarget(['nsis'], Arch.x64), projectDir: here,
    config: { appId: p.appId+(qa?'_setupqa':''), productName: p.name, artifactName: `${id}-\${version}-setup.\${ext}`,
      extraMetadata: { name: `hard-${id}`, description: p.description },
      directories: { output: `${qa?'setup-qa':'installers'}/${id}`, buildResources: 'installer' },
      files: ['out/**/*', 'package.json'], asar: true, compression: 'normal',
      electronVersion: JSON.parse(readFileSync(join(here, 'package.json'))).devDependencies.electron,
      electronDist: localElectron || undefined,
      extraResources: [{from:'installer/bin/process-check.exe',to:'process-check.exe'},...(includesBackend(id) ? [{ from: 'backend-dist/hard-backend', to: 'backend' }] : [])],
      win: { icon: 'icons/icon.ico', executableName: `hard-${id}`, target: ['nsis'], signExecutable: false },
      nsis: { oneClick: false, perMachine: false, allowElevation: false, allowToChangeInstallationDirectory: false,
        installerLanguages: ['ru_RU'], language: '1049', createDesktopShortcut: 'always', createStartMenuShortcut: true,
        shortcutName: p.name+(qa?' — проверка':''), uninstallDisplayName: p.name+(qa?' — проверка':''), deleteAppDataOnUninstall: false, include: 'installer/brand.nsh',
        runAfterFinish: false, differentialPackage: false },
      publish: { provider: 'generic', url: id==='crm'?'https://invalid.local/crm-pilot-unpublished/':releaseUrl, channel: id, useMultipleRangeRequest: false },
    }, publish: 'never' });
  console.log('Packaged runtime audited:', id, auditRuntimeDependencies(join(here,qa?'setup-qa':'installers',id,'win-unpacked/resources/app.asar')));
  // Builder also emits latest.yml for compatibility; only the product channel is distributable.
  const output = join(here, qa?'setup-qa':'installers', id);
  const channel = join(output, id + '.yml');
  if (!existsSync(channel)) throw new Error('Missing product update metadata: ' + id);
  const metadata = yaml.load(readFileSync(channel, 'utf8'));
  for (const file of metadata.files) file.size = statSync(join(output, file.url)).size;
  writeFileSync(channel, yaml.dump(metadata));
  writeFileSync(join(output, 'PRODUCT.txt'), `${p.name}\n${id}\n${p.appId}\n`);
  // Portable bootstrap opens our own Electron wizard; the NSIS transaction is
  // embedded and runs silently. Updater /S arguments pass through unchanged.
  const version=JSON.parse(readFileSync(join(here,'package.json'))).version;
  const name=`${id}-${version}-setup.exe`,innerRelative=`setup-build/${qa?'qa-':''}${id}`,inner=join(here,innerRelative);
  mkdirSync(inner,{recursive:true});copyFileSync(join(output,name),join(inner,'application-installer.exe'));
  if(usesCrm(id)){
    // The completed inner archive has been audited and copied. Bound disk use
    // before assembling the outer wizard; the final EXE is audited afterwards.
    const completed=join(output,'win-unpacked');
    if(lstatSync(completed).isSymbolicLink()||dirname(realpathSync(completed))!==realpathSync(output))throw new Error('Unsafe completed build directory');
    rmSync(completed,{recursive:true});
  }
  writeFileSync(join(inner,'setup-payload.json'),JSON.stringify({product:id,version,sha512:createHash('sha512').update(readFileSync(join(inner,'application-installer.exe'))).digest('base64')}));
  await build({targets:Platform.WINDOWS.createTarget(['portable'],Arch.x64),projectDir:here,
    config:{appId:p.appId+'.setup'+(qa?'_setupqa':''),productName:p.name+' — Установка',artifactName:name,
      extraMetadata:{name:`hard-setup-${id}`,main:'out/setup-main.js'},directories:{output:`${innerRelative}/outer`,buildResources:'installer'},
      // The application installer is already compressed; normal compression
      // avoids spending minutes recompressing it in the outer wizard.
      files:['out/setup-main.js','out/setup-preload.js','out/setup-registry.json','out/setup/**/*','out/licenses/**/*','package.json'],asar:true,compression:'normal',
      electronVersion:JSON.parse(readFileSync(join(here,'package.json'))).devDependencies.electron,
      electronDist:localElectron || undefined,
      extraResources:[{from:`${innerRelative}/application-installer.exe`,to:'application-installer.exe'},{from:`${innerRelative}/setup-payload.json`,to:'setup-payload.json'},{from:'installer/bin/process-check.exe',to:'process-check.exe'}],
      win:{icon:'icons/icon.ico',executableName:`hard-setup-${id}`,signExecutable:false,target:['portable']},portable:{requestExecutionLevel:'user'},publish:null},publish:'never'});
  auditRuntimeDependencies(join(inner,'outer/win-unpacked/resources/app.asar'));
  copyFileSync(join(inner,'outer',name),join(output,name));
  for(const file of metadata.files){file.size=statSync(join(output,name)).size;file.sha512=createHash('sha512').update(readFileSync(join(output,name))).digest('base64');}
  metadata.sha512=metadata.files[0].sha512;writeFileSync(channel,yaml.dump(metadata));
  console.log('Custom local installer ready:', id);
}
