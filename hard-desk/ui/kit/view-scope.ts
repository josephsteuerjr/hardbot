export type ReadTask<T> = (signal: AbortSignal) => T | Promise<T>;

export class ViewScope {
  private disposed = false;
  private generation = 0;
  private controllers = new Set<AbortController>();

  get valid(): boolean {
    return !this.disposed;
  }

  get revision(): number {
    return this.generation;
  }

  invalidate(): void {
    if (!this.valid) return;
    this.generation += 1;
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation += 1;
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
  }

  isCurrent(revision: number): boolean {
    return this.valid && revision === this.generation;
  }

  async read<T>(task: ReadTask<T>, apply?: (value: T) => void): Promise<T | undefined> {
    if (!this.valid) return undefined;
    this.invalidate();
    const revision = this.generation;
    const controller = new AbortController();
    this.controllers.add(controller);
    const current = () => this.isCurrent(revision) && !controller.signal.aborted;
    try {
      const value = await task(controller.signal);
      if (!current()) return undefined;
      apply?.(value);
      return value;
    } catch (error) {
      if (!current()) return undefined;
      throw error;
    } finally {
      this.controllers.delete(controller);
    }
  }
}
