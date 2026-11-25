export interface OperationIntent {
  readonly operation_id: string;
}

export type ActionPhase = "validating" | "persisting" | "submitting" | "accepted" | "queued" | "error" | "unknown";

export interface ActionSnapshot {
  target: string;
  operationId: string;
  phase: ActionPhase;
  error?: unknown;
}

export type ActionResult<T> =
  | { target: string; operationId: string; phase: "accepted"; value: T }
  | { target: string; operationId: string; phase: "queued"; value?: T }
  | { target: string; operationId: string; phase: "error" | "unknown"; error: unknown };

export type ActionSubmission<T> =
  | { phase: "accepted"; value: T }
  | { phase: "queued"; value?: T }
  | { phase: "error" | "unknown"; error: unknown };

export interface ActionRequest<T, I extends OperationIntent = OperationIntent> {
  target: string;
  intent: I;
  validate?: (intent: I) => void | Promise<void>;
  persist?: (intent: I) => void | Promise<void>;
  submit: (intent: I) => ActionSubmission<T> | Promise<ActionSubmission<T>>;
}

interface PendingAction {
  operationId: string;
  promise: Promise<ActionResult<unknown>>;
}

export class ActionTargetLockedError extends Error {
  constructor(readonly target: string, readonly operationId: string) {
    super(`Target is busy: ${target}`);
    this.name = "ActionTargetLockedError";
  }
}

export class ActionRunner {
  private pending = new Map<string, PendingAction>();
  private terminal = new Map<string, Map<string, ActionResult<unknown>>>();

  constructor(private onState: (state: ActionSnapshot) => void = () => undefined) {}

  isBusy(target: string): boolean {
    return this.pending.has(target);
  }

  run<T, I extends OperationIntent>(request: ActionRequest<T, I>): Promise<ActionResult<T>> {
    const { target, intent } = request;
    if (!target) throw new TypeError("Action target is required");
    if (!intent.operation_id) throw new TypeError("Stable intent operation_id is required");
    const active = this.pending.get(target);
    if (active) {
      if (active.operationId === intent.operation_id) return active.promise as Promise<ActionResult<T>>;
      const error = new ActionTargetLockedError(target, intent.operation_id);
      this.emit({ target, operationId: intent.operation_id, phase: "error", error });
      return Promise.resolve({ target, operationId: intent.operation_id, phase: "error", error });
    }
    const previous = this.terminal.get(target)?.get(intent.operation_id);
    if (previous) return Promise.resolve(previous as ActionResult<T>);
    const promise = this.execute(request) as Promise<ActionResult<unknown>>;
    this.pending.set(target, { operationId: intent.operation_id, promise });
    void promise.then(result => {
      if (this.pending.get(target)?.promise === promise) this.pending.delete(target);
      if (result.phase === "accepted" || result.phase === "queued" || result.phase === "unknown") {
        let outcomes = this.terminal.get(target);
        if (!outcomes) {
          outcomes = new Map();
          this.terminal.set(target, outcomes);
        }
        outcomes.set(result.operationId, result);
      }
    });
    return promise as Promise<ActionResult<T>>;
  }

  private async execute<T, I extends OperationIntent>(request: ActionRequest<T, I>): Promise<ActionResult<T>> {
    const { target, intent } = request;
    const operationId = intent.operation_id;
    this.emit({ target, operationId, phase: "validating" });
    try {
      await request.validate?.(intent);
    } catch (error) {
      return this.finish({ target, operationId, phase: "error", error });
    }
    this.emit({ target, operationId, phase: "persisting" });
    try {
      await request.persist?.(intent);
    } catch (error) {
      return this.finish({ target, operationId, phase: "error", error });
    }
    this.emit({ target, operationId, phase: "submitting" });
    let submission: ActionSubmission<T>;
    try {
      submission = await request.submit(intent);
    } catch (error) {
      return this.finish({ target, operationId, phase: "unknown", error });
    }
    if (!submission || !["accepted", "queued", "error", "unknown"].includes(submission.phase)) {
      return this.finish({ target, operationId, phase: "unknown", error: new TypeError("Invalid action submission") });
    }
    return this.finish({ target, operationId, ...submission } as ActionResult<T>);
  }

  private finish<T>(result: ActionResult<T>): ActionResult<T> {
    this.emit({ target: result.target, operationId: result.operationId, phase: result.phase, ...("error" in result ? { error: result.error } : {}) });
    return result;
  }

  private emit(state: ActionSnapshot): void {
    try {
      this.onState(state);
    } catch {
      return;
    }
  }
}
