const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm');
const {buildSync}=require('esbuild');
function load(name,required=require){const text=buildSync({entryPoints:[path.join(__dirname,'../src/main',name)],bundle:true,platform:'node',format:'cjs',write:false}).outputFiles[0].text;const m={exports:{}};vm.runInNewContext(text,{module:m,exports:m.exports,require:required,Buffer,process,__dirname:path.join(__dirname,'../out')});return m.exports;}
const {discoverProfiles,profilesAt}=load('profile-discovery.ts'),{migrateData,importData}=load('migration.ts');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'hard-discovery-test-'));
function fixture(dir,uid=101){fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(path.join(dir,'settings.json'),JSON.stringify({fieldServer:'https://synthetic.invalid',fieldDevice:{id:uid,telegram_id:uid,employee:'Synthetic employee'},theme:'light'}));fs.writeFileSync(path.join(dir,'field.key'),'Synthetic encrypted identity '+uid);fs.writeFileSync(path.join(dir,'calls-pending.bin'),'Synthetic durable work '+uid);}
try{
 const home=path.join(root,'home'),roaming=path.join(root,'Roaming'),desktop=path.join(home,'Desktop');fs.mkdirSync(roaming,{recursive:true});fs.mkdirSync(desktop,{recursive:true});
 const legacy=path.join(root,'arbitrary-folder','data');fixture(legacy);
 const link=path.join(desktop,'Софт — старая программа.lnk');fs.writeFileSync(link,'Synthetic link, never executed');
 const readShortcut=()=>({target:path.join(path.dirname(legacy),'renamed-program.exe')});
 assert.equal(profilesAt(link,'soft',readShortcut).some(p=>p.source===legacy),true);
 const found=discoverProfiles('soft',{appData:roaming,home,shortcuts:[desktop],readShortcut});assert.equal(found.some(p=>p.source===legacy),true);
 const target=path.join(root,'target');migrateData(target,roaming,'soft');assert.equal(JSON.parse(fs.readFileSync(path.join(target,'migration-v1.json'))).source,null);
 fs.mkdirSync(path.join(home,'Downloads'),{recursive:true});const later=path.join(home,'Downloads','Хард прежний','data');fixture(later);
 fs.writeFileSync(path.join(target,'settings.json'),JSON.stringify({theme:'dark'}));migrateData(target,roaming,'soft',{home});assert.equal(JSON.parse(fs.readFileSync(path.join(target,'settings.json'))).fieldDevice.id,101);
 const protectedBytes=fs.readFileSync(path.join(target,'field.key'));migrateData(target,roaming,'soft',{home});assert.deepEqual(fs.readFileSync(path.join(target,'field.key')),protectedBytes);
 const other=path.join(roaming,'Хард-сотрудник');fixture(other,202);const malformed=path.join(roaming,'Хард');fs.mkdirSync(malformed);fs.writeFileSync(path.join(malformed,'settings.json'),'{broken');
 const ambiguous=path.join(root,'ambiguous');migrateData(ambiguous,roaming,'soft',{home});assert.equal(fs.existsSync(path.join(ambiguous,'field.key')),false);assert.equal(fs.existsSync(path.join(ambiguous,'migration-choice.json')),true);
 const rollback=path.join(root,'rollback');fs.mkdirSync(rollback);fs.writeFileSync(path.join(rollback,'settings.json'),'{}');fs.writeFileSync(path.join(rollback,'Local State'),'Original empty encryption profile');
 let failed=false;const fsStub={...fs,renameSync(a,b){if(!failed&&b===path.join(rollback,'settings.json')){failed=true;throw new Error('Synthetic publish failure');}return fs.renameSync(a,b);}};
 const isolated=load('migration.ts',name=>name==='node:fs'?fsStub:require(name));assert.throws(()=>isolated.importData(later,rollback,'soft'));
 assert.equal(fs.readFileSync(path.join(rollback,'settings.json'),'utf8'),'{}');assert.equal(fs.readFileSync(path.join(rollback,'Local State'),'utf8'),'Original empty encryption profile');assert.equal(fs.existsSync(path.join(rollback,'field.key')),false);
 assert.equal(JSON.parse(fs.readFileSync(path.join(later,'settings.json'))).fieldDevice.id,101);
 console.log('Profile discovery passed: folder/EXE/shortcut, portable discovery, retry after empty search, malformed JSON, ambiguous identities, working profile preservation and atomic rollback.');
}finally{assert.ok(root.startsWith(path.join(os.tmpdir(),'hard-discovery-test-')));fs.rmSync(root,{recursive:true,force:true});}
