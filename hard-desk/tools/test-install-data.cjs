// Real DPAPI fixtures for disposable install/update/uninstall checks.
const {app,safeStorage}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const crypto=require('node:crypto');
const directory=process.env.HARD_INSTALL_QA_DATA;
if(!directory || !path.isAbsolute(directory))throw new Error('Isolated data directory required');
const trace=message=>fs.appendFileSync(path.join(directory,'fixture-trace.log'),message+'\n');
trace('loaded make='+process.env.HARD_INSTALL_QA_MAKE);
app.setPath('userData',directory);
const owner=crypto.createHash('sha256').update('https://synthetic.invalid\nuid:101').digest('hex');
const draftName='draft-'+crypto.createHash('sha256').update(owner+'\nsoft\nSYNTHETIC').digest('hex')+'.bin';
const fixtures={
  'field.key':'synthetic-field-token',
  'device.key':'synthetic-manager-token',
  'calls-pending.bin':JSON.stringify({owner:'synthetic-owner',entries:[{body:{op_id:'synthetic-stable-op',occurred_at:1780000000123,kind:'call',rep:'SYNTHETIC',values:{soft_comment:'Synthetic unsent call'}}}]}),
  [draftName]:JSON.stringify({fields:{'c-comment':{value:'Synthetic unsent draft',checked:false}},touched:['soft_comment']}),
};
if(process.env.HARD_INSTALL_QA_FIELD_ONLY==='1')delete fixtures['device.key'];
app.whenReady().then(()=>{
  trace('ready');
  try{
    assert.equal(safeStorage.isEncryptionAvailable(),true);
    for(const [name,text] of Object.entries(fixtures)){
      trace(name);
      const file=path.join(directory,name);
      if(process.env.HARD_INSTALL_QA_MAKE==='1')fs.writeFileSync(file,safeStorage.encryptString(text));
      assert.equal(safeStorage.decryptString(fs.readFileSync(file)),text);
      assert.equal(fs.readFileSync(file).includes(Buffer.from(text)),false);
    }
    trace('passed');console.log('Real DPAPI tokens, stable unsent call and draft preserved.');
    // Allow Chromium's asynchronous Local State write to finish before exit.
    setTimeout(()=>app.exit(0),1500);
  }catch(error){trace('failed '+error.message);console.error(error);app.exit(1);}
});
