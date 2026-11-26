const path=require('node:path'),Module=require('node:module'),assert=require('node:assert/strict');
const {buildSync}=require('esbuild');
const desk=path.resolve(__dirname,'..'),filename=path.join(desk,'out','qa-crm-native-access.cjs');
const code=buildSync({entryPoints:[path.join(desk,'src/main/crm-native-access.ts')],bundle:true,platform:'node',format:'cjs',write:false}).outputFiles[0].text;
const mod=new Module(filename,module);mod.filename=filename;mod.paths=Module._nodeModulePaths(path.dirname(filename));mod._compile(code,filename);
const {filterScopedAdminResult,filterScopedManagerTeam,legacyAdministrationAllowed,nativeCapability,requireScopedManagerMutation,scopedAdminActionAllowed}=mod.exports;
const features=['workbook-exchange','workbook-logistics','workbook-manager-report','workbook-table','admin','manager-access','manager-plan'];
const context=(role,capabilities,modules,extra={})=>({role,capabilities,workspace:{schema_version:1,revision:4,tree:modules.map(id=>({id,children:[]}))},...extra});
const legacy=(role,capabilities,extra={})=>({role,capabilities,...extra});
const checks=[];
function check(name,value){assert(value,name);checks.push(name);}
for(const feature of features){
  check('owner without a separate product: '+feature,nativeCapability(context('owner',[],[],{owner:true}),feature));
  check('employee cannot borrow another grant workspace: '+feature,!nativeCapability(context('employee',features,features),feature));
}
check('legacy manager default grant keeps exchange',nativeCapability(legacy('manager',['exchange']),'workbook-exchange'));
check('legacy analyst default grant keeps admin',nativeCapability(legacy('analyst',['exchange']),'admin'));
check('legacy manager keeps account access',nativeCapability(legacy('manager',['assign']),'manager-access'));
check('legacy manager keeps plan writes',nativeCapability(legacy('manager',['assign']),'manager-plan'));
check('legacy employee stays out of native admin',!nativeCapability(legacy('employee',['exchange','assign']),'admin'));
check('analyst exchange grant opens admin',nativeCapability(context('analyst',['exchange'],['exchange']),'admin'));
check('manager exchange grant does not become analyst admin',!nativeCapability(context('manager',['exchange'],['exchange','statistics','team']),'admin'));
check('analyst admin requires exchange in the selected grant',!nativeCapability(context('analyst',['analytics'],['exchange']),'admin'));
check('analyst admin requires the server workspace module',!nativeCapability(context('analyst',['exchange'],['statistics','team']),'admin'));
check('analyst team grant can use account access',nativeCapability(context('analyst',['exchange'],['team']),'manager-access'));
check('analyst cannot write manager plan',!nativeCapability(context('analyst',['exchange','assign'],['team']),'manager-plan'));
check('manager plan requires assign in the same selected grant',!nativeCapability(context('manager',['analytics'],['team']),'manager-plan'));
check('manager plan accepts assign grant',nativeCapability(context('manager',['assign'],['team']),'manager-plan'));
check('manager report requires manager workspace',!nativeCapability(context('analyst',['analytics'],['statistics']),'workbook-manager-report'));
check('analyst table uses analyst statistics',nativeCapability(context('analyst',['analytics'],['statistics']),'workbook-table'));
check('manager table uses manager statistics',nativeCapability(context('manager',['analytics'],['manager-statistics']),'workbook-table'));
check('workbook exchange requires selected exchange capability',!nativeCapability(context('manager',['analytics'],['exchange']),'workbook-exchange'));
check('workbook exchange accepts manager exchange grant',nativeCapability(context('manager',['exchange'],['exchange']),'workbook-exchange'));
check('logistics requires logistics grant and module',nativeCapability(context('logistic',['logistics'],['logistics']),'workbook-logistics'));
check('logistics module alone is insufficient',!nativeCapability(context('logistic',[],['logistics']),'workbook-logistics'));
check('other actor grants do not expand native access',!nativeCapability(context('employee',['exchange'],['connection'],{actor_uid:101,grants:[{actor_uid:202,role:'owner',territory:'all',capabilities:features}]}),'admin'));
check('malformed workspace fails closed',!nativeCapability({role:'analyst',capabilities:['exchange'],workspace:{tree:[{id:'exchange',children:{}}]}},'admin'));
check('explicit empty workspace does not reactivate legacy admin',!nativeCapability(context('analyst',['exchange'],[]),'admin'));
check('legacy administration remains available in model 1',legacyAdministrationAllowed(legacy('manager',[],{access_model:1})));
check('scoped model 2 cannot use broad legacy administration',!legacyAdministrationAllowed(legacy('manager',[],{access_model:2})));
check('global owner can use legacy administration',legacyAdministrationAllowed(legacy('owner',[],{access_model:2,owner:true})));
const scoped=legacy('manager',['assign'],{access_model:2,grant_id:'narrow',staff:[{name:'Allowed person',actor_uids:[101]}]});
const team={employees:[{employee:'Allowed person',account:{username:'allowed',actor_uid:101,devices:[{id:55}] }},{employee:'Outside person',account:{username:'outside',actor_uid:202,devices:[{id:66}]}}]};
check('scoped team removes foreign employees',filterScopedManagerTeam(scoped,team).employees.length===1);
assert.throws(()=>requireScopedManagerMutation(scoped,'reset',{username:'outside'},team),/территории/);
assert.throws(()=>requireScopedManagerMutation(scoped,'revoke',{device_id:66},team),/территории/);
requireScopedManagerMutation(scoped,'reset',{username:'allowed'},team);
requireScopedManagerMutation(scoped,'revoke',{device_id:55},team);
const pendingTeam={employees:[{employee:'New employee',registration_pending:true,can_approve:true,
  account:{username:'new',actor_uid:303,devices:[{id:77,status:'pending'},{id:78,status:'active'}]}}]};
const full={...scoped,primary_team_review:true};
check('first device can be reviewed before linking the employee to CRM',
  filterScopedManagerTeam(full,pendingTeam).employees[0]?.account.devices.map(d=>d.id).join()==='77');
requireScopedManagerMutation(full,'approve',{device_id:77},pendingTeam);
assert.throws(()=>requireScopedManagerMutation(scoped,'approve',{device_id:77},pendingTeam),/территории/);
assert.throws(()=>requireScopedManagerMutation(full,'approve',{device_id:78},pendingTeam),/территории/);
assert.throws(()=>requireScopedManagerMutation(full,'revoke',{device_id:77},pendingTeam),/территории/);
assert.throws(()=>requireScopedManagerMutation(full,'reset',{username:'new'},pendingTeam),/территории/);
check('scoped admin permits account list',scopedAdminActionAllowed(scoped,'local',['/admin',{method:'GET',path:'/admin/accounts.json'}]));
check('scoped admin denies broad import',!scopedAdminActionAllowed(scoped,'local',['/enr/apply',{}]));
const accountRequest=['/admin',{method:'GET',path:'/admin/accounts.json'}];
const accountReply={ok:true,data:{approver:'Global approver',password_review_owner:true,accounts:[
  {username:'allowed',employee:'Allowed person',actor_uid:101},
  {username:'outside',employee:'Allowed person',actor_uid:202},
  {username:'unknown',employee:'Allowed person'},
]}};
const scopedReply=filterScopedAdminResult(scoped,'local',accountRequest,accountReply);
assert.deepEqual(scopedReply.data.accounts.map(account=>account.username),['allowed']);
assert.equal(scopedReply.data.approver,'');
assert.equal(scopedReply.data.password_review_owner,false);
check('legacy account reply is unchanged',filterScopedAdminResult(legacy('analyst',['exchange']),'local',accountRequest,accountReply)===accountReply);
check('unexpected account reply fails closed',!filterScopedAdminResult(scoped,'local',accountRequest,{ok:true,accounts:accountReply.data.accounts}).ok);
console.log(JSON.stringify({passed:true,checks:checks.length,ownerFeatures:features.length,legacyFallback:true,selectedGrantRoleCapabilityWorkspace:true,crossGrantDenied:true}));
