// Synthetic unit and Chromium checks; no server or production credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('esbuild');

function bundle(entry, globalName) {
  return esbuild.buildSync({
    entryPoints: [path.join(__dirname, entry)], bundle: true, format: 'iife',
    globalName, platform: 'browser', write: false,
  }).outputFiles[0].text;
}

function loadUnit() {
  const code = esbuild.buildSync({
    entryPoints: [path.join(__dirname, '../ui/access/role-editor.ts')],
    bundle: true, platform: 'node', format: 'cjs', write: false,
  }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, console, setTimeout, clearTimeout });
  return module.exports;
}

function units() {
  const editor = loadUnit();
  const grants = editor.normalizeGrants([
    { grant_id: 'g1', actor_uid: 7, role: 'employee', territory: 'self', capabilities: ['note', 'call', 'call'], is_default: true },
    { grant_id: 'g2', actor_uid: 7, role: 'manager', territory: 'team', capabilities: ['assign'], employees: ['ignored'] },
  ]);
  assert.equal(grants.length, 2);
  assert.deepEqual(Array.from(grants[0].capabilities), ['call', 'note']);
  assert.deepEqual(Array.from(grants[1].employees), []);
  assert.equal(editor.fingerprint, undefined);
  const diff = editor.diffRoleGrants(grants, [grants[0], { role: 'analyst', territory: 'all', capabilities: ['analytics'], employees: [] }]);
  assert.equal(diff.add.length, 1);
  assert.equal(diff.remove.length, 1);
  assert.equal(diff.update.length, 0);
  assert.equal(diff.add[0].role, 'analyst');
  assert.equal(diff.add[0].territory, 'all');
  const changed = editor.diffRoleGrants(grants, [{ ...grants[0], is_default: false }, grants[1]]);
  assert.equal(changed.update.length, 1);
  assert.equal(changed.add.length + changed.remove.length, 0);
  assert.throws(() => editor.validateRoleGrants([grants[0], { ...grants[0], grant_id: 'duplicate' }]), /не должна повторяться/);
  assert.throws(() => editor.validateRoleGrants([{ role: 'manager', territory: 'employees', capabilities: [], employees: [] }]), /Выберите сотрудников/);
  const catalog = {
    roles: [
      { id: 'employee', capabilities: ['call'] }, { id: 'manager', capabilities: ['assign'] },
      { id: 'owner', capabilities: ['assign'] }, { id: 'root' }, { id: 'server_operator' },
    ],
  };
  assert.deepEqual(Array.from(editor.editableRoles(catalog), role => role.id), ['employee', 'manager']);
  assert.deepEqual(Array.from(editor.editableRoles({ ...catalog, editable_roles: ['manager'] }), role => role.id), ['manager']);
  const css = fs.readFileSync(path.join(__dirname, '../ui/access/styles/access-editor.css'), 'utf8').trim();
  assert.equal(editor.ROLE_EDITOR_CSS.trim(), css);
  console.log('Role editor unit: 10 assertions groups PASS');
}

async function browser() {
  const { app, BrowserWindow } = require('electron');
  app.disableHardwareAcceleration();
  await app.whenReady();
  const win = new BrowserWindow({
    show: false, width: 1100, height: 800,
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false, offscreen: true },
  });
  const js = code => win.webContents.executeJavaScript(code, true);
  const until = async (condition, label) => {
    for (let index = 0; index < 100; index += 1) {
      if (await js(condition)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('Timeout: ' + label + '\n' + await js('document.body.innerText'));
  };
  try {
    await win.loadURL('data:text/html,<html><head></head><body><div id="host"></div></body></html>');
    await js(bundle('../ui/access/role-editor.ts', 'RoleEditor'));
    await js(bundle('../ui/crm-manager-team.ts', 'ManagerTeam'));
    await js(`(()=>{
      let uuidSequence=0;if(!crypto.randomUUID)Object.defineProperty(crypto,'randomUUID',{value:()=> 'synthetic-operation-'+(++uuidSequence)});
      window.calls=[];window.draftWrites=[];window.teamCalls=0;window.listCalls=0;
      window.catalog={access_model:2,roles:[
        {id:'employee',label:'Сотрудник',capabilities:['call','note']},
        {id:'manager',label:'Руководитель',capabilities:['assign']},
        {id:'analyst',label:'Аналитик',capabilities:['analytics']},
        {id:'owner',label:'Владелец',capabilities:['assign']},
        {id:'root',label:'Root',capabilities:[]},
        {id:'server_operator',label:'Оператор',capabilities:[]}
      ],territories:[{territory:'self'},{territory:'employees'},{territory:'team'},{territory:'all',owner_only:true}]};
      window.initialGrants=[
        {grant_id:'g-self',actor_uid:7,role:'employee',territory:'self',capabilities:['call'],employees:[],all_contracts:false,read_all_contracts:false,is_default:true,revision:4},
        {grant_id:'g-team',actor_uid:7,role:'manager',territory:'team',capabilities:['assign'],employees:[],all_contracts:false,read_all_contracts:false,is_default:false,revision:4}
      ];
      window.currentGrants=structuredClone(initialGrants);window.currentRevision=4;
      window.previewConflict=false;window.applyFailures=0;window.applyCalls=0;
      window.listResponder=null;window.liveListener=null;window.editor=null;window.teamHandle=null;
      const ok=data=>({ok:true,status:200,data});
      window.crm={
        platform:'desktop',
        api:async(method,action,body,query)=>{
          calls.push({method,action,body:body?structuredClone(body):undefined,query});
          if(action==='manager-access'){
            teamCalls++;
            return ok({employees:[{employee_id:'e1',employee:'Синтетический сотрудник',account:{actor_uid:7,username:'synthetic',role:'employee',status:'active',devices:[]},can_reset:false,can_approve:false,can_revoke:false,can_remove:false}]});
          }
          if(action==='grant-catalog')return ok(catalog);
          if(action!=='grant-admin')throw Error('Unexpected action '+action);
          if(body.operation==='list'){
            listCalls++;
            if(listResponder)return listResponder(body);
            return ok({actors:[{actor_uid:7,access_model:2,base_revision:currentRevision,grants:structuredClone(currentGrants),manageable:true,warnings:['Серверное предупреждение'],history:[{action:'grant_created',after:initialGrants[0],revision:4,changed_at:'2026-11-24T10:00:00Z'}]}]});
          }
          if(body.operation==='preview'){
            if(previewConflict)return {ok:false,status:409,text:'Версия изменилась.',data:{error:'Версия изменилась.',details:{policy_revision:9}}};
            return ok({actor_uid:7,base_revision:body.base_revision,grants:structuredClone(body.grants),diff:body.grants.length>currentGrants.length?[{action:'create',text:'Добавить: Аналитик / глобально',before:null,after:body.grants.at(-1)}]:[],preview_sha256:'sha-preview-1',operation_id:body.operation_id,warnings:['Предупреждение предпросмотра']});
          }
          if(body.operation==='apply'){
            applyCalls++;
            if(applyFailures>0){applyFailures--;throw Error('Синтетическая потеря квитанции');}
            currentGrants=structuredClone(body.grants);currentRevision++;
            return ok({actor_uid:7,base_revision:body.base_revision,policy_revision:currentRevision,result_grants:structuredClone(currentGrants),diff:body.diff,repeated:applyCalls>1,operation_id:body.operation_id,warnings:[],applied_at:'2026-11-24T10:01:00Z'});
          }
          throw Error('Unexpected operation '+body.operation);
        },
        draft:function(key,value){
          if(arguments.length===1)return Promise.resolve({ok:true,data:window.savedDraft||null});
          draftWrites.push(value?structuredClone(value):value);window.savedDraft=value?structuredClone(value):null;
          return Promise.resolve({ok:true,data:value});
        },
        onChange:listener=>{liveListener=listener;return()=>{liveListener=null;};}
      };
      window.resetFixture=()=>{
        if(editor)editor.dispose();if(teamHandle)teamHandle();editor=null;teamHandle=null;
        document.querySelector('#host').replaceChildren();calls=[];draftWrites=[];savedDraft=null;
        currentGrants=structuredClone(initialGrants);currentRevision=4;previewConflict=false;applyFailures=0;applyCalls=0;listCalls=0;teamCalls=0;
      };
      window.mountEditor=async(asOwner=true)=>{
        editor=await RoleEditor.mountRoleEditor(document.querySelector('#host'),crm,{actorUid:7,employeeName:'Синтетический сотрудник',owner:asOwner,employees:[{employee_uuid:'e1',name:'Синтетический сотрудник'}]});
      };
      window.mountTeam=async()=>{
        teamHandle=await ManagerTeam.mountManagerTeam(document.querySelector('#host'),crm,{distribution:{enabled:false},staff:[]},()=>true,()=>{});
      };
      window.editNewCard=()=>{
        document.querySelector('[data-access-add]').click();
        const cards=document.querySelectorAll('[data-access-card]'),index=cards.length-1;
        const role=document.querySelector('[data-access-role="'+index+'"]');role.value='analyst';role.dispatchEvent(new Event('change',{bubbles:true}));
        const territory=document.querySelector('[data-access-territory="'+index+'"]');territory.value='all';territory.dispatchEvent(new Event('change',{bubbles:true}));
      };
    })()`);

    await js('(async()=>{resetFixture();await mountEditor();return true})()');
    assert.equal(await js('document.querySelectorAll("[data-access-card]").length'), 2);
    assert.equal(await js('[...document.querySelectorAll("[data-access-role] option")].some(option=>["owner","root","server_operator"].includes(option.value))'), false);
    assert.match(await js('document.querySelector("[data-access-history]").textContent'), /Добавлено полномочие/);
    await js('editNewCard();document.querySelector("[data-access-form]").requestSubmit()');
    await until('calls.some(call=>call.body?.operation==="preview")', 'preview');
    const preview = await js('calls.find(call=>call.body?.operation==="preview").body');
    assert.deepEqual(await js('calls.find(call=>call.body?.operation==="list").body'), { operation: 'list' });
    assert.equal(preview.actor_uid, 7);
    assert.equal(preview.base_revision, 4);
    assert.equal(preview.grants.length, 3);
    assert.deepEqual(preview.grants.map(grant => [grant.role, grant.territory]), [['employee', 'self'], ['manager', 'team'], ['analyst', 'all']]);
    assert.equal(preview.grants.some(grant => grant.role === 'employee' && grant.territory === 'all'), false);
    assert.equal(await js('draftWrites.at(-1).preview_sha256'), 'sha-preview-1');
    assert.match(await js('document.querySelector("[data-access-warnings]").textContent'), /Предупреждение предпросмотра/);
    await js('document.querySelector("[data-access-apply]").click()');
    await until('document.querySelector("[data-access-confirm]").open', 'apply confirmation');
    await js('document.querySelector("[data-access-confirm-apply]").click()');
    await until('calls.some(call=>call.body?.operation==="apply")&&!document.querySelector("[data-access-confirm]").open', 'apply');
    const apply = await js('calls.find(call=>call.body?.operation==="apply").body');
    assert.equal(apply.preview_sha256, 'sha-preview-1');
    assert.equal(apply.operation_id, preview.operation_id);
    assert.deepEqual(apply.diff[0].after.role, 'analyst');
    assert.equal(apply.grants.length, 3);
    assert.equal(await js('savedDraft'), null);
    assert.match(await js('document.querySelector("[data-access-status]").textContent'), /применены/i);
    console.log('Role editor Chromium: catalog, role+territory cards, diff, preview/apply, warnings/history PASS');

    await js('(async()=>{resetFixture();await mountEditor();return true})()');
    await js(`(()=>{
      window.originalCard=document.querySelector('[data-access-card="0"]');
      const role=document.querySelector('[data-access-role="0"]');role.value='analyst';role.dispatchEvent(new Event('change',{bubbles:true}));role.focus();
      previewConflict=true;
    })()`);
    await until('draftWrites.at(-1)?.grants[0].role==="analyst"', 'durable conflict input');
    const conflictOperation = await js('draftWrites.at(-1).operation_id');
    await js('document.querySelector("[data-access-form]").requestSubmit()');
    await until('document.querySelector("[data-access-status]").textContent.includes("сохранены")', '409 retention');
    assert.equal(await js(`document.querySelector('[data-access-role="0"]').value`), 'analyst');
    assert.equal(await js(`originalCard===document.querySelector('[data-access-card="0"]')`), true);
    assert.equal(await js('savedDraft.operation_id'), conflictOperation);
    assert.equal(await js('savedDraft.base_revision'), 9);
    assert.equal(await js('savedDraft.grants[0].role'), 'analyst');
    await js('previewConflict=false;currentRevision=10;liveListener({type:"live",dirty:true})');
    await until('listCalls>=2', 'live list refresh');
    assert.equal(await js(`originalCard===document.querySelector('[data-access-card="0"]')`), true);
    assert.equal(await js(`document.querySelector('[data-access-role="0"]').value`), 'analyst');
    assert.equal(await js(`document.activeElement===document.querySelector('[data-access-role="0"]')`), true);
    await js('(async()=>{editor.dispose();document.querySelector("#host").replaceChildren();await mountEditor();return true})()');
    assert.equal(await js(`document.querySelector('[data-access-role="0"]').value`), 'analyst');
    assert.equal(await js('savedDraft.operation_id'), conflictOperation);
    assert.match(await js('document.querySelector("[data-access-status]").textContent'), /Черновик восстановлен/);
    assert.equal(await js('document.querySelector("[data-access-meta]").textContent.includes("ревизия 9")'), true);
    console.log('Role editor Chromium: 409, live refresh and remount retain DOM, focus, input, draft and operation_id PASS');

    await js('(async()=>{resetFixture();await mountEditor();editNewCard();document.querySelector("[data-access-form]").requestSubmit();return true})()');
    await until('calls.some(call=>call.body?.operation==="preview")', 'unknown preview');
    await js('applyFailures=1;document.querySelector("[data-access-apply]").click();document.querySelector("[data-access-confirm-apply]").click()');
    await until('applyCalls===1&&document.querySelector("[data-access-confirm-status]").textContent.includes("Исход неизвестен")', 'unknown apply');
    assert.equal(await js('document.querySelector("[data-access-apply]").textContent'), 'Проверить исход операции');
    assert.equal(await js('savedDraft.operation_id'), await js('calls.find(call=>call.body?.operation==="preview").body.operation_id'));
    await js('document.querySelector("[data-access-confirm-apply]").click()');
    await until('applyCalls===2&&!document.querySelector("[data-access-confirm]").open', 'idempotent receipt');
    const operationIds = await js('calls.filter(call=>call.body?.operation==="apply").map(call=>call.body.operation_id)');
    assert.equal(operationIds.length, 2);
    assert.equal(operationIds[0], operationIds[1]);
    assert.match(await js('document.querySelector("[data-access-status]").textContent'), /прежняя квитанция/);
    console.log('Role editor Chromium: unknown outcome retries one durable operation and accepts repeated receipt PASS');

    await js('(async()=>{resetFixture();catalog.editable_roles=["employee"];await mountEditor(false);return true})()');
    assert.equal(await js(`document.querySelector('[data-access-role="0"]').disabled`), false);
    assert.equal(await js(`document.querySelector('[data-access-role="1"]').disabled`), true);
    await js('document.querySelector("[data-access-add]").click()');
    assert.deepEqual(await js(`[...document.querySelector('[data-access-role="2"]').options].map(option=>option.value)`), ['employee']);
    await js('delete catalog.editable_roles');
    console.log('Role editor Chromium: server catalog limits analyst roles and leaves other grants read-only PASS');

    await js('(async()=>{resetFixture();await mountTeam();return true})()');
    await until(`!!document.querySelector('[data-access-card="0"]')`, 'team role editor');
    await js(`(()=>{
      window.teamCard=document.querySelector('[data-access-card="0"]');
      const role=document.querySelector('[data-access-role="0"]');role.value='analyst';role.dispatchEvent(new Event('change',{bubbles:true}));role.focus();
      window.dispatchEvent(new Event('focus'));
    })()`);
    await until('teamCalls>=2', 'manager live/focus refresh');
    assert.equal(await js(`teamCard===document.querySelector('[data-access-card="0"]')`), true);
    assert.equal(await js(`document.querySelector('[data-access-role="0"]').value`), 'analyst');
    assert.equal(await js(`document.activeElement===document.querySelector('[data-access-role="0"]')`), true);
    assert.equal(await js('document.querySelectorAll("#team-role-editor .access-editor").length'), 1);
    console.log('Manager Team Chromium: account profile refresh does not remount or redraw grant form PASS');
    await js('resetFixture()');
    console.log('Role editor browser: all scenarios PASS');
    win.destroy();
    app.exit(0);
  } catch (error) {
    console.error(error);
    if (!win.isDestroyed()) win.destroy();
    app.exit(1);
  }
}

if (process.versions.electron) {
  browser().catch(error => { console.error(error); process.exit(1); });
} else {
  units();
}
