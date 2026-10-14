// Actual Chromium DOM with source modules compiled only in memory for this test.
// The API is synthetic: this checks retained UI state, not server authorization.
const { app, BrowserWindow } = require('electron');
const { buildSync } = require('esbuild');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const lab = path.resolve(__dirname, '../../_local/adversarial-review-2026-10-12/runs/crm-identity');
fs.mkdirSync(lab, { recursive: true }); app.setPath('userData', path.join(lab, 'profile-' + Date.now()));
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const window = new BrowserWindow({ show: false, webPreferences: { contextIsolation: true, sandbox: true } });
  try {
    await window.loadURL('data:text/html,' + encodeURIComponent('<div id="head-title"></div><div id="head-kicker"></div><div id="head-tools"></div><div id="view-inner"></div>'));
    const js = code => window.webContents.executeJavaScript(code);
    await js(`window.qaOwner=1;window.hard={state:async()=>({server:'https://synthetic.invalid',device:{id:window.qaOwner}}),api:async(method,path,query,body)=>{
      if(path.startsWith('/api/local/'))return {ok:true,status:200,data:{value:null}};
      if(query?.employee&&query.employee!=='Team '+window.qaOwner)return {ok:false,status:404,text:'employee'};
      return {ok:true,status:200,data:{asof:'2026-10-12',persons:1,contracts:1,items:[{client:'Synthetic person '+window.qaOwner,employee:'Team '+window.qaOwner,rep:'KD-'+window.qaOwner,ids:['KD-'+window.qaOwner],n:1,totals:[],status:'',tels:[],partners:[]}]}};
    },on(){},copy:async()=>true,xlsx:async()=>({ok:true})};void 0;`);
    for (const [file, global] of [['manager-crm.ts', 'ReviewCRM'], ['team-portfolio.ts', 'ReviewSheet']]) {
      const code = buildSync({ entryPoints: [path.join(__dirname, '../ui/views', file)], bundle: true, platform: 'browser', format: 'iife', globalName: global, write: false }).outputFiles[0].text;
      await js(code + ';void 0;');
    }
    const render = (global, name, params = '{}') => js(`document.getElementById('view-inner').innerHTML='<div id="review-page"></div>';${global}.${name}.render(document.getElementById('review-page'),${params})`);
    await render('ReviewCRM', 'managerCRM');
    await js(`const input=document.querySelector('[data-crm-search]');input.value='Previous private search';input.dispatchEvent(new Event('input',{bubbles:true}));window.qaOwner=2;`);
    await render('ReviewCRM', 'managerCRM');
    assert.equal(await js(`document.querySelector('[data-crm-search]').value`), '', 'new login inherited the old CRM search');
    await js('window.qaOwner=1'); await render('ReviewCRM', 'managerCRM');
    assert.equal(await js(`document.querySelector('[data-crm-search]').value`), 'Previous private search', 'original login lost its own CRM state');
    await render('ReviewSheet', 'teamPortfolio', '{employee:"Team 1"}');
    await js('window.qaOwner=2'); await render('ReviewSheet', 'teamPortfolio');
    assert.equal(await js(`!!document.querySelector('#team-portfolio-table')`), true, 'old employee filter blocked a different team');
    fs.writeFileSync(path.join(lab, 'RESULTS.json'), JSON.stringify({ realChromiumDOM: true, syntheticApi: true, newLoginSearchIsolated: true, oldLoginSearchRestored: true, differentTeamFilterReset: true }));
    console.log('CRM identities passed: real Chromium DOM, separate searches and team filters, original owner restore.');
    window.destroy(); app.exit(0);
  } catch (error) { fs.writeFileSync(path.join(lab, 'ERROR.txt'), String(error.stack)); console.error(error); window.destroy(); app.exit(1); }
});
