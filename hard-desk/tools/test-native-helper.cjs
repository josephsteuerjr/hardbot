const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {execFileSync}=require('node:child_process');
(async()=>{
  if(process.platform!=='win32'){console.log('Native C# helper audit requires Windows');return;}
  const {nativeHelperDigest}=await import('./native-helper-digest.mjs');
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'hard-native-helper-'));
  try{
    const compiler=path.join(process.env.WINDIR,'Microsoft.NET/Framework64/v4.0.30319/csc.exe');
    const source=fs.readFileSync(path.join(__dirname,'../installer/process-check.cs'),'utf8');
    const compile=(id,text)=>{
      const dir=path.join(temp,id);fs.mkdirSync(dir);const input=path.join(dir,'helper.cs'),output=path.join(dir,'process-check.exe');
      fs.writeFileSync(input,text);
      execFileSync(compiler,['/nologo','/target:winexe','/platform:x64','/reference:System.Web.Extensions.dll','/reference:System.Security.dll','/out:'+output,input],{stdio:'pipe',windowsHide:true});
      return fs.readFileSync(output);
    };
    const first=compile('first',source),second=compile('second',source);
    assert.equal(nativeHelperDigest(first),nativeHelperDigest(second));
    // Compilations straddling a clock second must compare identically too.
    const pe=first.readUInt32LE(0x3c),differentTime=Buffer.from(first);
    differentTime.writeUInt32LE((first.readUInt32LE(pe+8)+1)>>>0,pe+8);
    assert.equal(nativeHelperDigest(first),nativeHelperDigest(differentTime));
    const changedCoff=Buffer.from(first);changedCoff[pe+12]^=1;
    assert.notEqual(nativeHelperDigest(first),nativeHelperDigest(changedCoff));
    assert.ok(source.includes('"protect"'));
    const changed=compile('changed',source.replace('"protect"','"changed"'));
    assert.notEqual(nativeHelperDigest(first),nativeHelperDigest(changed));
    const changedHeader=Buffer.from(first);changedHeader[100]^=1;
    assert.notEqual(nativeHelperDigest(first),nativeHelperDigest(changedHeader));
    assert.throws(()=>nativeHelperDigest(Buffer.from('invalid helper')));
    console.log('Native helper audit: independent C# compilations and different creation times match; changed behavior/other headers and malformed PE refused');
  }finally{
    const resolved=fs.realpathSync(temp);
    assert.ok(resolved.startsWith(fs.realpathSync(os.tmpdir())+path.sep+'hard-native-helper-'));
    fs.rmSync(resolved,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
