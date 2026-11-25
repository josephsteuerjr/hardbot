export type NativeCapability =
  | 'workbook-exchange'
  | 'workbook-logistics'
  | 'workbook-manager-report'
  | 'workbook-table'
  | 'admin'
  | 'manager-access'
  | 'manager-plan';

type Rule = {
  modules: readonly string[];
  roles: readonly string[];
  capability?: string;
  legacy(context: any): boolean;
};

const rules: Record<NativeCapability, Rule> = {
  'workbook-exchange': {
    modules: ['exchange'], roles: ['manager', 'analyst'], capability: 'exchange',
    legacy: context => ['manager', 'analyst'].includes(context.role),
  },
  'workbook-logistics': {
    modules: ['logistics'], roles: ['logistic'], capability: 'logistics',
    legacy: context => context.role === 'logistic',
  },
  'workbook-manager-report': {
    modules: ['manager-statistics'], roles: ['manager'],
    legacy: context => context.role === 'manager',
  },
  'workbook-table': {
    modules: ['manager-statistics', 'statistics'], roles: ['manager', 'analyst'],
    legacy: context => ['manager', 'analyst'].includes(context.role),
  },
  admin: {
    modules: ['exchange'], roles: ['analyst'], capability: 'exchange',
    legacy: context => context.role === 'analyst',
  },
  'manager-access': {
    modules: ['team'], roles: ['manager', 'analyst'],
    legacy: context => context.role === 'manager',
  },
  'manager-plan': {
    modules: ['team'], roles: ['manager'], capability: 'assign',
    legacy: context => context.role === 'manager',
  },
};

function workspaceModules(workspace: any): Set<string> | null {
  if (!workspace || !Array.isArray(workspace.tree)) return null;
  const result = new Set<string>();
  const visit = (nodes: any[]): boolean => {
    for (const node of nodes) {
      if (!node || typeof node.id !== 'string' || node.children !== undefined && !Array.isArray(node.children)) return false;
      result.add(node.id);
      if (node.children && !visit(node.children)) return false;
    }
    return true;
  };
  return visit(workspace.tree) ? result : null;
}

export function legacyAdministrationAllowed(context: any): boolean {
  return context?.access_model !== 2 || context.owner === true;
}

export function scopedEmployeeAllowed(context:any,name:any):boolean {
  const normalized=String(name||'').trim().toLocaleLowerCase();
  return (context?.staff||[]).some((person:any)=>String(person.name||'').trim().toLocaleLowerCase()===normalized);
}
function scopedAccountAllowed(context:any,account:any):boolean {
  if(!account)return true;
  const actor=account.actor_uid??account.telegram_id;
  if(actor!==undefined&&actor!==null){
    const ids=(context?.staff||[]).flatMap((person:any)=>person.actor_uids||[]);
    if(ids.length)return ids.map(Number).includes(Number(actor));
  }
  return scopedEmployeeAllowed(context,account.employee);
}
export function filterScopedManagerTeam(context:any,result:any):any {
  if(legacyAdministrationAllowed(context)||!result)return result;
  return {...result,employees:(result.employees||[]).filter((row:any)=>
    scopedEmployeeAllowed(context,row.employee)&&scopedAccountAllowed(context,row.account))};
}
export function filterScopedManagerPlan(context:any,result:any):any {
  if(legacyAdministrationAllowed(context)||!result)return result;
  return {...result,employees:(result.employees||[]).filter((row:any)=>scopedEmployeeAllowed(context,row.employee))};
}
export function requireScopedManagerMutation(context:any,resource:string,payload:any,team:any):void {
  if(legacyAdministrationAllowed(context))return;
  if(resource==='plan'){
    for(const name of Object.keys(payload?.plan||{}))if(!scopedEmployeeAllowed(context,name))throw Error('Сотрудник вне доступной территории.');
    return;
  }
  if(resource==='board'){
    if(!scopedEmployeeAllowed(context,payload?.employee))throw Error('Сотрудник вне доступной территории.');
    return;
  }
  const employees=filterScopedManagerTeam(context,team)?.employees||[];
  if(resource==='reset'){
    const username=String(payload?.username||'').toLocaleLowerCase();
    if(!employees.some((row:any)=>String(row.account?.username||'').toLocaleLowerCase()===username))throw Error('Аккаунт вне доступной территории.');
    return;
  }
  if(resource==='revoke'||resource==='approve'){
    if(payload?.device_id!==undefined&&payload?.device_id!==null){
      if(!employees.some((row:any)=>(row.account?.devices||[]).some((device:any)=>device.id===payload.device_id)))throw Error('Устройство вне доступной территории.');
    }else{
      const username=String(payload?.username||'').toLocaleLowerCase();
      if(!employees.some((row:any)=>String(row.account?.username||'').toLocaleLowerCase()===username))throw Error('Аккаунт вне доступной территории.');
    }
  }
}
export function scopedAdminActionAllowed(context:any,action:string,args:any[]):boolean {
  if(legacyAdministrationAllowed(context))return true;
  if(action!=='local')return false;
  const path=args[0],body=args[1]||{};
  return path==='/state'||path==='/busy'||path==='/admin'&&body.method==='GET'&&body.path==='/admin/accounts.json';
}
export function filterScopedAdminResult(context:any,action:string,args:any[],result:any):any {
  if(action==='local'&&args[0]==='/admin'&&(args[1]||{}).path==='/admin/accounts.json'&&!legacyAdministrationAllowed(context)&&result?.accounts){
    return {...result,accounts:result.accounts.filter((account:any)=>scopedAccountAllowed(context,account))};
  }
  return result;
}

export function nativeCapability(context: any, capability: NativeCapability): boolean {
  if (!context) return false;
  if (context.owner === true) return true;
  const rule = rules[capability];
  if (context.workspace === undefined)
    return rule.legacy(context) && (!rule.capability || context.capabilities?.includes(rule.capability));
  const available = workspaceModules(context.workspace);
  return !!available && rule.roles.includes(context.role) && rule.modules.some(module => available.has(module)) &&
    (!rule.capability || context.capabilities?.includes(rule.capability) === true);
}
