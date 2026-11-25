export type WorkspaceDispose = () => void;
export type WorkspaceEpoch = string | number;
export type WorkspaceScope = string | number | boolean | null | undefined | object;

export interface WorkspaceModuleManifest {
  id?: string;
  module?: string;
  route?: string;
  label?: string;
  title?: string;
  feature?: string;
  hidden?: boolean;
  active?: string;
  children?: readonly WorkspaceModuleManifest[];
  icon?: string;
  order?: number;
  scopes?: readonly string[];
}

export interface WorkspaceManifest {
  modules?: readonly (string | WorkspaceModuleManifest)[];
  tree?: readonly WorkspaceModuleManifest[];
  identity_epoch?: WorkspaceEpoch;
  policy_epoch?: WorkspaceEpoch;
  revision?: WorkspaceEpoch;
  scope?: WorkspaceScope;
  scope_id?: WorkspaceScope;
  default_module?: string;
  defaultModule?: string;
  default?: string;
}

export interface ModuleContext {
  readonly moduleId: string;
  readonly view: HTMLElement;
  readonly scope: string;
  readonly identityEpoch: string;
  readonly policyEpoch: string;
  valid(): boolean;
  navigate(route: string): Promise<string>;
  getState<T>(scope?: WorkspaceScope): T | undefined;
  setState<T>(value: T, scope?: WorkspaceScope): void;
  onDispose(dispose: WorkspaceDispose): void;
}

export interface WorkspaceModule {
  id: string;
  label: string;
  title?: string;
  feature?: string;
  hidden?: boolean;
  active?: string;
  mount(context: ModuleContext): void | WorkspaceDispose | Promise<void | WorkspaceDispose>;
}

export interface ResolvedWorkspaceModule extends WorkspaceModule {
  title: string;
}
