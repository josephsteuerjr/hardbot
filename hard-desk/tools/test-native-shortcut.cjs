const {app,shell}=require('electron');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {buildSync}=require('esbuild'),vm=require('node:vm');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'hard-shortcut-test-'));
app.setPath('userData',path.join(root,'browser'));
app.whenReady().then(()=>{
 try{
  const old=path.join(root,'old program'),data=path.join(old,'data');fs.mkdirSync(data,{recursive:true});
  const exe=path.join(old,'old.exe');fs.copyFileSync(path.join(__dirname,'../installer/bin/process-check.exe'),exe);
  fs.writeFileSync(path.join(data,'settings.json'),JSON.stringify({fieldServer:'https://synthetic.invalid',fieldDevice:{id:101,telegram_id:101,employee:'Synthetic employee'}}));
  const link=path.join(root,'Софт — старый ярлык.lnk');assert.equal(shell.writeShortcutLink(link,'create',{target:exe,cwd:old}),true);
  const code=buildSync({entryPoints:[path.join(__dirname,'../src/main/profile-discovery.ts')],bundle:true,platform:'node',format:'cjs',write:false}).outputFiles[0].text;
  const m={exports:{}};vm.runInNewContext(code,{module:m,exports:m.exports,require,Buffer});
  const info=shell.readShortcutLink(link);
  const found=m.exports.profilesAt(link,'soft',p=>shell.readShortcutLink(p));
  assert.ok(found.some(p=>fs.statSync(p.source,{bigint:true}).ino===fs.statSync(data,{bigint:true}).ino));
  console.log('Real Windows .lnk and EXE discovery passed; shortcut was read without executing its target.');app.exit(0);
 }catch(e){console.error(e.message);app.exit(1);}
});
