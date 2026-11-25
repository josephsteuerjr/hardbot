import { WorkspaceRegistry, workspaceManifestModules } from './registry';
import type {
  ModuleContext,
  ResolvedWorkspaceModule,
  WorkspaceDispose,
  WorkspaceManifest,
  WorkspaceScope,
} from './types';

export interface WorkspaceHostOptions {
  registry: WorkspaceRegistry;
  view: HTMLElement;
  navigation?: HTMLElement;
  title?: HTMLElement;
  onLeave?(previous: ModuleContext, next: ModuleContext | null): void;
  onBeforeMount?(context: ModuleContext, previous: ModuleContext | null): void | Promise<void>;
  onAfterMount?(context: ModuleContext): void;
  onDispose?(context: ModuleContext): void;
  onRoute?(route: string, module: ResolvedWorkspaceModule): void;
  onError?(error: unknown, context?: ModuleContext): void;
}

interface ActiveModule {
  context: ModuleContext;
  module: ResolvedWorkspaceModule;
  disposers: WorkspaceDispose[];
}

function scopeKey(scope: WorkspaceScope): string {
  if (scope === undefined || scope === null || scope === '') return 'default';
  if (typeof scope !== 'object') return String(scope);
  const normalize = (value: any): any => {
    if (Array.isArray(value)) return value.map(normalize);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.keys(value).sort().map(key => [key, normalize(value[key])]));
    }
    return value;
  };
  return JSON.stringify(normalize(scope));
}

export class WorkspaceHost {
  private manifest: (Omit<WorkspaceManifest, 'modules'> & { modules: ResolvedWorkspaceModule[] }) | null = null;
  private active: ActiveModule | null = null;
  private generation = 0;
  private disposed = false;
  private identity: string | null = null;
  private policy = '0';
  private readonly states = new Map<string, Map<string, Map<string, unknown>>>();
  private readonly routes = new Map<string, string>();

  constructor(private readonly options: WorkspaceHostOptions) {}

  get currentRoute(): string | undefined {
    return this.active?.module.id;
  }

  get currentContext(): ModuleContext | undefined {
    return this.active?.context;
  }

  get identityEpoch(): string | undefined {
    return this.identity ?? undefined;
  }

  get policyEpoch(): string {
    return this.policy;
  }

  async open(manifest: WorkspaceManifest, requested?: string): Promise<string> {
    if (this.disposed) throw new Error('Workspace host is disposed.');
    const modules = this.options.registry.resolve(workspaceManifestModules(manifest));
    if (!modules.length) throw new Error('Workspace manifest has no registered modules.');
    const identity = String(manifest.identity_epoch ?? 'default');
    const policy = String(manifest.policy_epoch ?? manifest.revision ?? '0');
    const identityChanged = this.identity !== null && this.identity !== identity;
    const policyChanged = this.identity !== null && this.policy !== policy;
    if (identityChanged || policyChanged) {
      if (policyChanged && !identityChanged && this.active) this.options.onLeave?.(this.active.context, null);
      this.generation++;
      this.disposeActive();
      if (identityChanged) {
        this.states.clear();
        this.routes.clear();
      }
    }
    this.identity = identity;
    this.policy = policy;
    this.manifest = { ...manifest, modules };
    this.renderNavigation();
    const scope = scopeKey(manifest.scope ?? manifest.scope_id);
    const route = requested || this.routes.get(`${identity}\n${scope}`) || this.home();
    return this.navigate(route);
  }

  home(): string {
    const modules = this.manifest?.modules || [];
    const preferred = this.manifest?.default_module || this.manifest?.defaultModule || this.manifest?.default;
    if (preferred && modules.some(module => module.id === preferred)) return preferred;
    return (modules.find(module => !module.hidden) || modules[0])?.id || 'connection';
  }

  async navigate(requested: string): Promise<string> {
    if (this.disposed || !this.manifest) throw new Error('Workspace host is not open.');
    const modules = this.manifest.modules;
    const module = modules.find(entry => entry.id === requested)
      || modules.find(entry => entry.id === this.home())
      || modules[0];
    const route = module.id;
    const previous = this.active;
    const generation = ++this.generation;
    const identity = this.identity!;
    const policy = this.policy;
    const scope = scopeKey(this.manifest.scope ?? this.manifest.scope_id);
    const context = this.createContext(module, scope, identity, policy, generation);
    if (previous) {
      this.routes.set(`${identity}\n${previous.context.scope}`, previous.module.id);
      this.options.onLeave?.(previous.context, context);
    }
    this.disposeActive();
    const active: ActiveModule = { context, module, disposers: [] };
    this.active = active;
    this.options.onRoute?.(route, module);
    this.renderNavigation();
    if (this.options.title) this.options.title.textContent = module.title;
    try {
      await this.options.onBeforeMount?.(context, previous?.context || null);
      if (!this.isActive(active, generation)) return route;
      const dispose = await module.mount(context);
      if (dispose) active.disposers.push(dispose);
      if (!this.isActive(active, generation)) {
        this.disposeList(active.disposers, context);
        return route;
      }
      this.options.onAfterMount?.(context);
    } catch (error) {
      if (this.isActive(active, generation)) this.disposeActive();
      if ((error as Error)?.name !== 'AbortError') {
        if (this.options.onError) this.options.onError(error, context);
        else throw error;
      }
    }
    return route;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation++;
    this.disposeActive();
    this.options.navigation?.replaceChildren();
    this.states.clear();
    this.routes.clear();
    this.manifest = null;
  }

  private createContext(
    module: ResolvedWorkspaceModule,
    scope: string,
    identity: string,
    policy: string,
    generation: number,
  ): ModuleContext {
    const context: ModuleContext = {
      moduleId: module.id,
      view: this.options.view,
      scope,
      identityEpoch: identity,
      policyEpoch: policy,
      valid: () => !this.disposed && generation === this.generation && this.active?.context === context,
      navigate: route => this.navigate(route),
      getState: <T>(requestedScope?: WorkspaceScope): T | undefined => {
        if (this.identity !== identity) return undefined;
        const key = requestedScope === undefined ? scope : scopeKey(requestedScope);
        return this.states.get(identity)?.get(key)?.get(module.id) as T | undefined;
      },
      setState: <T>(value: T, requestedScope?: WorkspaceScope): void => {
        if (this.identity !== identity) return;
        const key = requestedScope === undefined ? scope : scopeKey(requestedScope);
        let scopes = this.states.get(identity);
        if (!scopes) this.states.set(identity, scopes = new Map());
        let modules = scopes.get(key);
        if (!modules) scopes.set(key, modules = new Map());
        modules.set(module.id, value);
      },
      onDispose: (dispose: WorkspaceDispose): void => {
        if (context.valid() && this.active?.context === context) this.active.disposers.push(dispose);
        else dispose();
      },
    };
    return context;
  }

  private isActive(active: ActiveModule, generation: number): boolean {
    return !this.disposed && this.active === active && generation === this.generation;
  }

  private disposeActive(): void {
    const active = this.active;
    if (!active) return;
    this.active = null;
    this.disposeList(active.disposers, active.context);
    this.options.onDispose?.(active.context);
  }

  private disposeList(disposers: WorkspaceDispose[], context: ModuleContext): void {
    const pending = disposers.splice(0).reverse();
    for (const dispose of pending) {
      try { dispose(); }
      catch (error) { this.options.onError?.(error, context); }
    }
  }

  private renderNavigation(): void {
    const navigation = this.options.navigation;
    if (!navigation || !this.manifest) return;
    const active = this.active?.module;
    const activeRoute = active?.active || active?.id;
    const buttons = this.manifest.modules.filter(module => !module.hidden).map(module => {
      const button = document.createElement('button');
      button.dataset.section = module.id;
      button.textContent = module.label;
      button.classList.toggle('active', module.id === activeRoute);
      button.onclick = () => { void this.navigate(module.id); };
      return button;
    });
    navigation.replaceChildren(...buttons);
  }
}
