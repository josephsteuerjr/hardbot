const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const asar=require('@electron/asar');
(async()=>{
 const {auditRuntimeDependencies}=await import('./runtime-dependencies.mjs');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'hard-runtime-deps-'));
 function pkg(base,directory,value){const dir=path.join(base,directory);fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(path.join(dir,'package.json'),JSON.stringify({version:'1.0.0',main:'index.js',...value}));fs.writeFileSync(path.join(dir,'index.js'),'module.exports = true;');}
 async function fixture(name,kind){const base=path.join(root,name);pkg(base,'',{name:'synthetic-app',dependencies:{a:'^1.0.0'}});pkg(base,'node_modules/a',{name:'a',dependencies:{b:'^1.0.0'}});if(kind!=='missing')pkg(base,kind==='nested'?'node_modules/a/node_modules/b':'node_modules/b',{name:'b',version:kind==='wrong'?'2.0.0':'1.0.0'});if(kind==='entry')fs.unlinkSync(path.join(base,'node_modules/b/index.js'));const file=path.join(root,name+'.asar');await asar.createPackage(base,file);return file;}
 try{
  assert.equal(auditRuntimeDependencies(await fixture('hoisted','valid')).runtimePackages,3);
  assert.equal(auditRuntimeDependencies(await fixture('nested','nested')).runtimePackages,3);
  const missing=await fixture('missing','missing');assert.throws(()=>auditRuntimeDependencies(missing),/Missing runtime dependency: b/);
  const wrong=await fixture('wrong','wrong');assert.throws(()=>auditRuntimeDependencies(wrong),/version mismatch/);
  const entry=await fixture('entry','entry');assert.throws(()=>auditRuntimeDependencies(entry),/entry point/);
  console.log('Packaged runtime gate passed: hoisted/nested dependencies accepted; missing module, wrong version and missing entry point rejected.');
 }finally{const resolved=fs.realpathSync(root);assert.ok(resolved.startsWith(fs.realpathSync(os.tmpdir())+path.sep+'hard-runtime-deps-'));fs.rmSync(resolved,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
