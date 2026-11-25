export interface DialogManagerOptions {
  isDirty?: () => boolean;
  confirmDiscard?: () => boolean | Promise<boolean>;
  returnFocus?: () => HTMLElement | null;
  onClose?: () => void;
}

const FOCUSABLE = "button, input, select, textarea, a[href], area[href], summary, [tabindex], [contenteditable='true']";

export class DialogManager {
  private dirtyState = false;
  private submittingState = false;
  private active = false;
  private returnTo: HTMLElement | null = null;
  private closing: Promise<boolean> | null = null;

  constructor(private dialog: HTMLDialogElement, private options: DialogManagerOptions = {}) {
    dialog.addEventListener("cancel", this.onCancel);
    dialog.addEventListener("close", this.onClose);
    dialog.addEventListener("keydown", this.onKeyDown);
    dialog.addEventListener("submit", this.onSubmit);
  }

  get dirty(): boolean {
    return this.options.isDirty?.() ?? this.dirtyState;
  }

  get submitting(): boolean {
    return this.submittingState;
  }

  get open(): boolean {
    return this.dialog.open;
  }

  setDirty(value: boolean): void {
    this.dirtyState = value;
  }

  setSubmitting(value: boolean): void {
    this.submittingState = value;
    if (value) this.dialog.setAttribute("aria-busy", "true");
    else this.dialog.removeAttribute("aria-busy");
  }

  show(initialFocus?: HTMLElement | string): void {
    if (this.active) return;
    this.returnTo = this.dialog.ownerDocument.activeElement as HTMLElement | null;
    this.active = true;
    this.dirtyState = false;
    this.setSubmitting(false);
    if (!this.dialog.open) this.dialog.showModal();
    let target: HTMLElement | null = null;
    if (typeof initialFocus === "string") target = this.dialog.querySelector<HTMLElement>(initialFocus);
    else if (initialFocus) target = initialFocus;
    target ??= this.dialog.querySelector<HTMLElement>("[autofocus]");
    target ??= this.focusable()[0] ?? null;
    if (!target) {
      this.dialog.tabIndex = -1;
      target = this.dialog;
    }
    target.focus({ preventScroll: true });
  }

  async requestClose(): Promise<boolean> {
    if (!this.dialog.open) return true;
    if (this.submittingState) return false;
    if (this.closing) return this.closing;
    const attempt = this.confirmAndClose();
    this.closing = attempt;
    try {
      return await attempt;
    } finally {
      if (this.closing === attempt) this.closing = null;
    }
  }

  forceClose(): void {
    if (this.dialog.open) this.dialog.close();
    this.finishClose();
  }

  dispose(): void {
    this.dialog.removeEventListener("cancel", this.onCancel);
    this.dialog.removeEventListener("close", this.onClose);
    this.dialog.removeEventListener("keydown", this.onKeyDown);
    this.dialog.removeEventListener("submit", this.onSubmit);
    this.forceClose();
  }

  private async confirmAndClose(): Promise<boolean> {
    if (this.dirty) {
      if (!this.options.confirmDiscard || !await this.options.confirmDiscard()) return false;
    }
    if (!this.dialog.open) return true;
    if (this.submittingState) return false;
    this.forceClose();
    return true;
  }

  private focusable(): HTMLElement[] {
    return Array.from(this.dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(element =>
      !element.hidden && element.tabIndex >= 0 && !element.hasAttribute("disabled") &&
      element.getAttribute("aria-hidden") !== "true" && !element.closest("[hidden], [inert]"));
  }

  private finishClose(): void {
    if (!this.active) return;
    this.active = false;
    this.closing = null;
    this.dirtyState = false;
    this.setSubmitting(false);
    const fallback = this.options.returnFocus?.() ?? null;
    const target = this.returnTo?.isConnected ? this.returnTo : fallback?.isConnected ? fallback : null;
    this.returnTo = null;
    target?.focus({ preventScroll: true });
    this.options.onClose?.();
  }

  private onCancel = (event: Event): void => {
    event.preventDefault();
    void this.requestClose().catch(() => false);
  };

  private onClose = (): void => {
    this.finishClose();
  };

  private onSubmit = (event: SubmitEvent): void => {
    const form = event.target as HTMLFormElement | null;
    const submitter = event.submitter as HTMLButtonElement | null;
    if (form?.method !== "dialog" && submitter?.formMethod !== "dialog") return;
    event.preventDefault();
    void this.requestClose().catch(() => false);
  };

  private onKeyDown = (event: KeyboardEvent): void => {
    if (event.key !== "Tab" || !this.dialog.open) return;
    const elements = this.focusable();
    if (!elements.length) {
      event.preventDefault();
      this.dialog.focus({ preventScroll: true });
      return;
    }
    const first = elements[0];
    const last = elements[elements.length - 1];
    const current = this.dialog.ownerDocument.activeElement;
    const outside = !this.dialog.contains(current);
    if (event.shiftKey && (current === first || outside)) {
      event.preventDefault();
      last.focus({ preventScroll: true });
    } else if (!event.shiftKey && (current === last || outside)) {
      event.preventDefault();
      first.focus({ preventScroll: true });
    }
  };
}
