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
