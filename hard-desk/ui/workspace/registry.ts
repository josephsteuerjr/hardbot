import type {
  ResolvedWorkspaceModule,
  WorkspaceModule,
  WorkspaceModuleManifest,
  WorkspaceManifest,
} from './types';

export function workspaceModuleId(entry: string | WorkspaceModuleManifest): string {
  return typeof entry === 'string' ? entry : entry.id || entry.module || entry.route || '';
}

export function workspaceManifestModules(manifest: WorkspaceManifest): (string | WorkspaceModuleManifest)[] {
  if (Array.isArray(manifest.modules)) return [...manifest.modules];
  const result: WorkspaceModuleManifest[] = [];
  const visit = (nodes: readonly WorkspaceModuleManifest[], parent?: string) => {
    for (const node of nodes) {
      result.push(parent ? {...node, hidden: node.hidden ?? true, active: node.active || parent} : node);
      if (node.children) visit(node.children, node.id || node.module || node.route || parent);
    }
  };
  visit(manifest.tree || []);
  return result;
}

export class WorkspaceRegistry {
  private readonly modules = new Map<string, WorkspaceModule>();

  register(...modules: readonly WorkspaceModule[]): this {
    for (const module of modules) {
      if (!module.id) throw new Error('Workspace module id is required.');
      if (this.modules.has(module.id)) throw new Error(`Duplicate workspace module: ${module.id}`);
      this.modules.set(module.id, module);
    }
    return this;
  }

  has(id: string): boolean {
    return this.modules.has(id);
  }

  get(id: string): WorkspaceModule | undefined {
    return this.modules.get(id);
  }

  resolve(entries: readonly (string | WorkspaceModuleManifest)[]): ResolvedWorkspaceModule[] {
    const resolved: ResolvedWorkspaceModule[] = [];
    const seen = new Set<string>();
    for (const entry of entries) {
      const id = workspaceModuleId(entry);
      const registered = this.modules.get(id);
      if (!registered || seen.has(id)) continue;
      const override = typeof entry === 'string' ? {} : entry;
      seen.add(id);
      resolved.push({
        ...registered,
        ...override,
        id,
        label: override.label || override.title || registered.label,
        title: override.title || registered.title || override.label || registered.label,
        mount: registered.mount,
      });
    }
    return resolved;
  }
}
