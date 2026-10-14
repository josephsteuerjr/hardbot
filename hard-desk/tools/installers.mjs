// Four local builds. This script never uploads or installs an application.
import { build, Platform, Arch } from 'electron-builder';
import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import yaml from 'js-yaml';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { products, releaseUrl } from './products.mjs';
const here = join(dirname(fileURLToPath(import.meta.url)), '..');
const qa = !!process.env.HARD_SETUP_QA_BUILD;
mkdirSync(join(here,'installer/bin'),{recursive:true});
execFileSync(join(process.env.WINDIR,'Microsoft.NET/Framework64/v4.0.30319/csc.exe'),['/nologo','/target:winexe','/platform:x64','/reference:System.Web.Extensions.dll','/reference:System.Security.dll','/out:'+join(here,'installer/bin/process-check.exe'),join(here,'installer/process-check.cs')],{stdio:'inherit'});
const selected = process.argv.slice(2);
for (const id of (selected.length ? selected : Object.keys(products))) {
  const p = products[id];
  if (!p) throw new Error('Unknown product: ' + id);
  if (id === 'analyst' && !existsSync(join(here, 'backend-dist/hard-backend/hard-backend.exe')))
    throw new Error('Build the analyst backend first: npm run backend');
  execFileSync(process.execPath, [join(here, 'build.mjs')], { cwd: here, env: { ...process.env, HARD_PRODUCT: id }, stdio: 'inherit' });
  await build({ targets: Platform.WINDOWS.createTarget(['nsis'], Arch.x64), projectDir: here,
    config: { appId: p.appId+(qa?'_setupqa':''), productName: p.name, artifactName: `${id}-\${version}-setup.\${ext}`,
      extraMetadata: { name: `hard-${id}`, description: p.description },
      directories: { output: `${qa?'setup-qa':'installers'}/${id}`, buildResources: 'installer' },
      files: ['out/**/*', 'package.json'], asar: true, compression: 'maximum',
      electronVersion: JSON.parse(readFileSync(join(here, 'package.json'))).devDependencies.electron,
      extraResources: [{from:'installer/bin/process-check.exe',to:'process-check.exe'},...(id === 'analyst' ? [{ from: 'backend-dist/hard-backend', to: 'backend' }] : [])],
      win: { icon: 'icons/icon.ico', executableName: `hard-${id}`, target: ['nsis'], signExecutable: false },
      nsis: { oneClick: false, perMachine: false, allowElevation: false, allowToChangeInstallationDirectory: false,
        installerLanguages: ['ru_RU'], language: '1049', createDesktopShortcut: 'always', createStartMenuShortcut: true,
        shortcutName: p.name+(qa?' — проверка':''), uninstallDisplayName: p.name+(qa?' — проверка':''), deleteAppDataOnUninstall: false, include: 'installer/brand.nsh',
        runAfterFinish: false, differentialPackage: false },
      publish: { provider: 'generic', url: releaseUrl, channel: id, useMultipleRangeRequest: false },
    }, publish: 'never' });
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
  writeFileSync(join(inner,'setup-payload.json'),JSON.stringify({product:id,version,sha512:createHash('sha512').update(readFileSync(join(inner,'application-installer.exe'))).digest('base64')}));
  await build({targets:Platform.WINDOWS.createTarget(['portable'],Arch.x64),projectDir:here,
    config:{appId:p.appId+'.setup'+(qa?'_setupqa':''),productName:p.name+' — Установка',artifactName:name,
      extraMetadata:{name:`hard-setup-${id}`,main:'out/setup-main.js'},directories:{output:`${innerRelative}/outer`,buildResources:'installer'},
      files:['out/setup-main.js','out/setup-preload.js','out/setup-registry.json','out/setup/**/*','package.json'],asar:true,compression:'maximum',
      electronVersion:JSON.parse(readFileSync(join(here,'package.json'))).devDependencies.electron,
      extraResources:[{from:`${innerRelative}/application-installer.exe`,to:'application-installer.exe'},{from:`${innerRelative}/setup-payload.json`,to:'setup-payload.json'},{from:'installer/bin/process-check.exe',to:'process-check.exe'}],
      win:{icon:'icons/icon.ico',executableName:`hard-setup-${id}`,signExecutable:false,target:['portable']},portable:{requestExecutionLevel:'user'},publish:null},publish:'never'});
  copyFileSync(join(inner,'outer',name),join(output,name));
  for(const file of metadata.files){file.size=statSync(join(output,name)).size;file.sha512=createHash('sha512').update(readFileSync(join(output,name))).digest('base64');}
  metadata.sha512=metadata.files[0].sha512;writeFileSync(channel,yaml.dump(metadata));
  console.log('Custom local installer ready:', id);
}
