import {local} from './lib';

type ImportJob = {
  id: string;
  kind: string;
  started: number;
  state: 'running' | 'done' | 'failed' | 'uncertain';
  log?: string[];
  result?: {toast?: string; error?: string; toast_err?: boolean};
  error?: string;
};

export function mountImportStatus(host: HTMLElement, onChange: () => void) {
  const panel = document.createElement('div');
  panel.className = 'analyst-import-status';
  panel.innerHTML = '<span role="status"></span><small hidden aria-hidden="true"></small>';
  host.append(panel);
  const label = panel.querySelector('span')!;
  const elapsed = panel.querySelector('small')!;
  let receipt: ImportJob | undefined;
  let localWork: {started: number; text: string} | undefined;
  let readFailed = false;
  let revision = 0;
  let retry: ReturnType<typeof setTimeout> | undefined;

  function paint() {
    const started = localWork?.started ?? (receipt?.state === 'running' ? receipt.started * 1000 : undefined);
    const result = receipt?.result;
    const problem = result?.error || receipt?.error?.split('\n')[0];
    const text = localWork?.text
      || (readFailed ? 'Проверяю состояние импорта. Журнал временно недоступен…' : '')
      || (receipt?.state === 'running' ? receipt.log?.at(-1) || 'Импорт выполняется…' : '')
      || (receipt?.state === 'uncertain' ? problem || 'Запись ещё не подтверждена квитанцией.' : '')
      || (receipt?.state === 'failed' || problem ? problem || 'Импорт завершился ошибкой.' : '')
      || (receipt?.state === 'done' ? result?.toast || 'Импорт завершён.' : '');
    panel.hidden = !text;
    panel.classList.toggle('err', !localWork && !readFailed && (!!problem || !!result?.toast_err));
    if (label.textContent !== text) label.textContent = text;
    elapsed.hidden = started === undefined;
    if (started !== undefined) {
      const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
      elapsed.textContent = `Прошло ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }
  }

  async function refresh(id?: string) {
    if (!panel.isConnected) return;
    clearTimeout(retry);
    const turn = ++revision;
    try {
      const latest = id === undefined
        ? (await local<ImportJob[]>('/jobs')).find(job => job.kind === 'apply')
        : receipt;
      const current = latest && await local<ImportJob>('/job', {id: latest.id});
      if (turn !== revision || !panel.isConnected) return;
      receipt = latest && current && {...current, started: latest.started};
      readFailed = false;
    } catch {
      if (turn !== revision || !panel.isConnected) return;
      readFailed = true;
    }
    paint();
    onChange();
    if (!localWork && (readFailed || receipt?.state === 'running'))
      retry = setTimeout(() => void refresh(readFailed ? undefined : receipt?.id), 2000);
  }

  const ticker = setInterval(paint, 1000);
  panel.hidden = true;
  void refresh();
  return {
    working: () => receipt?.state === 'running',
    show(work?: {started: number; text: string}) { localWork = work; paint(); },
    refresh,
    dispose() { revision++; clearTimeout(retry); clearInterval(ticker); panel.remove(); },
  };
}
