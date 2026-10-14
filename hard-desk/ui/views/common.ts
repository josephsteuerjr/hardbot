// Общие куски экранов руководителя: окно периода, чипы офисов, карточка должника.
import { api, hard } from "../kit/api";
import { $, esc, icon, lightbox, n, shortDate } from "../kit/dom";
import { openPanel, go, hasSection, currentContext } from "../kit/shell";

export interface Range { preset?: string; shift?: number; from?: string; to?: string }
export interface RangeInfo { preset: string; from: string; to: string; label: string; days: number; today: string; next: unknown }

export const PRESETS: [string, string][] = [["today", "Сегодня"], ["yesterday", "Вчера"], ["7d", "7 дней"], ["30d", "30 дней"], ["month", "Месяц"], ["all", "За всё время"]];

export function rangeQuery(r: Range): Record<string, unknown> {
  if (r.from || r.to) return { preset: "custom", from: r.from, to: r.to, shift: r.shift || 0 };
  return { preset: r.preset || "today", shift: r.shift || 0 };
}

export function rangeChips(r: Range, info: RangeInfo | null, only?: string[]): string {
  const cur = r.from ? "custom" : (r.preset || "today");
  const list = PRESETS.filter(([k]) => !only || only.includes(k));
  const nav = info && info.preset !== 'all' ? `<span class="chip-sep"></span>
    <button class="chip" data-shift="-1" title="Раньше">‹</button>
    <span class="muted nowrap" style="font-size:13.5px;padding:0 6px">${esc(info.label)}</span>
    <button class="chip" data-shift="1" ${info.next ? "" : "disabled"} title="Позже">›</button>` : "";
  return `<div class="chips">${list.map(([k, t]) =>
    `<button class="chip" data-preset="${k}" aria-pressed="${cur === k && !(r.shift && k === "today")}">${t}</button>`).join("")}${nav}</div>`;
}

/** Чипы офисов: «Все», офисы, «Без офиса». */
export function officeChips(offices: { code: string; label?: string; name?: string; n?: number; employees?: number }[], cur: string): string {
  if (!offices || offices.length < 2 && !offices.some((o) => o.code === "!none")) return "";
  const all = `<button class="chip" data-office="" aria-pressed="${!cur}">Все офисы</button>`;
  return `<div class="chips">${all}${offices.map((o) => `<button class="chip" data-office="${esc(o.code)}" aria-pressed="${cur === o.code}">${esc(o.label || o.name || o.code)}<span class="n">${n(o.n ?? o.employees ?? "")}</span></button>`).join("")}</div>`;
}

export function wireRange(root: HTMLElement, r: Range, redraw: () => void): void {
  root.querySelectorAll<HTMLButtonElement>("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    r.preset = b.dataset.preset; r.shift = 0; r.from = undefined; r.to = undefined; redraw();
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-shift]").forEach((b) => b.addEventListener("click", () => {
    r.shift = (r.shift || 0) + Number(b.dataset.shift); redraw();
  }));
}

export function problem(text: string, retry?: () => void): string {
  return `<div class="notice err"><span class="grow">${esc(text)}</span>${retry ? '<button class="btn small" data-retry>Повторить</button>' : ""}</div>`;
}

export function staleNote(at?: string): string {
  if (!at) return "";
  const d = new Date(at);
  return `<div class="notice"><span class="grow">Нет связи — показываю то, что было получено ${esc(shortDate(at.slice(0, 10)))} в ${esc(d.toTimeString().slice(0, 5))}.</span></div>`;
}

const FLAG_TEXT: Record<string, string> = {
  far: "далеко от адреса", nogeo: "нет GPS-точки", late: "внесено позже", fast: "карточка быстрее 40 с",
  night: "ночью", series: "внесено пачкой", cash: "«наличные» в тексте", paid: "«оплатил» без платежа",
  addr_coarse: "адрес неточный", bymgr: "внесено руководителем", dupphoto: "фото использовано дважды",
};
const STRONG = new Set(["cash", "paid", "far", "dupphoto"]);

export function flagsHtml(flags: string[] | undefined): string {
  if (!flags || !flags.length) return "";
  return `<span class="flags">${flags.map((f) => `<span class="flag${STRONG.has(f) ? " strong" : ""}">${esc(FLAG_TEXT[f] || f)}</span>`).join("")}</span>`;
}

const EVIDENCE: Record<string, [string, string]> = {
  confirmed: ["точка рядом с адресом", "ok"], far: ["точка далеко от адреса", "err"], nofix: ["GPS не поймался", "warn"],
  nogeo: ["нет GPS-точки", "warn"], coarse: ["адрес неточный", "warn"],
};
export function evidenceTag(level: string | undefined): string {
  const e = level ? EVIDENCE[level] : undefined;
  return e ? `<span class="tag ${e[1]}">${esc(e[0])}</span>` : "";
}

export interface CaseReference { employee: string; rep: string; profile?: string }
export interface CaseNavigation {
  cases?: CaseReference[];
  previous?: () => void; next?: () => void; hasPrevious?: boolean; hasNext?: boolean;
}

/** Managers and security open a full workspace; analyst keeps its existing panel. */
export async function openPerson(employee: string, rep: string, date?: string, mode?: string,
  navigation?: CaseNavigation): Promise<void> {
  if (hasSection("crm")) {
    const context = currentContext();
    const origin = context.id === "crm" && context.params.origin ? context.params.origin : context;
    await go("crm", { employee, rep, date, profile: mode || 'hard', cases: navigation?.cases, origin, currentPortfolio: !!navigation?.cases });
    return;
  }
  await openPersonPanel(employee, rep, date, mode, navigation);
}

async function openPersonPanel(employee: string, rep: string, date?: string, mode?: string,
  navigation?: CaseNavigation): Promise<void> {
  const body = openPanel(`<h2>…</h2><div class="sub">${esc(employee)}</div>`);
  const r = await api("/api/manager/person", { employee, rep, date, mode });
  if (!body.isConnected) return;
  if (!r.ok) { body.innerHTML = problem(r.text || "Не открылось"); return; }
  const d = r.data;
  $("panel-title").innerHTML = `<h2>${esc(d.client || rep)}</h2>
    <div class="sub">${esc(employee)} · <span class="mono">${esc(rep)}</span>${d.n > 1 ? ` · ${n(d.n)} договора` : ""}</div>`;
  const ev = d.evidence || {};
  const rows: [string, unknown][] = [
    ["Долг", d.money_text], ["Итог", d.outcome], ["Адрес", d.address],
    ["Телефон", (d.phones || []).join(", ")], ["Родился", d.birth_date], ["Партнёр", d.partner],
    ["Обещал до", d.dates?.promise_date], ["Встреча", d.dates?.meeting_date],
    ["Карточка", d.fill_text ? "заполнена за " + d.fill_text : ""],
  ];
  const kv = rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  const form = ((d.visit || []) as any[]).filter((f) => f.value !== null && f.value !== undefined && f.value !== "")
    .map((f) => `<dt>${esc(f.title)}</dt><dd>${esc(f.value)}</dd>`).join("");
  const amount = (value: unknown, unit = "") => value === null || value === undefined || value === "" ? "—" :
    `${Number(value).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unit}`.trim();
  body.innerHTML = `
    ${navigation ? `<div class="toolbar"><button class="btn small" data-case-previous ${navigation.hasPrevious?'':'disabled'}>← Предыдущий</button><span class="spacer"></span><button class="btn small" data-case-next ${navigation.hasNext?'':'disabled'}>Следующий →</button></div>` : ''}
    ${ev.level ? `<div style="margin:2px 0 6px">${evidenceTag(ev.level)} <span class="muted" style="font-size:13px">${esc(ev.note || "")}</span></div>` : ""}
    <dl class="kv">${kv}</dl>
    ${d.contracts?.length ? `<div class="block"><h3>Договоры (${n(d.contracts.length)})</h3>${d.contracts.map((c:any)=>`<div class="list-row"><div class="grow"><b class="mono">${esc(c.contract_id)}</b> · ${esc(c.partner || '')}<div class="sub">Долг: ${esc(amount(c.total,c.unit))} · результат: ${esc(c.status || 'Не обработан')}<br>Последний платёж из загруженного реестра: ${esc(shortDate(c.last_payment_date || ''))} · ${esc(amount(c.last_payment_sum,c.unit))}<br>Текущие платежи: ${esc(amount(c.current_payment,c.unit))}</div></div></div>`).join('')}</div>` : ''}
    ${ev.rows?.length ? `<details class="block"><summary>Проверка выезда</summary><ul>${ev.rows.map((text:string)=>`<li>${esc(text)}</li>`).join('')}</ul></details>` : ''}
    ${d.payment_checks?.length ? `<details class="block"><summary>Сверка чеков с последним платежом</summary>${d.payment_checks.map((check:any)=>`<div class="list-row"><div class="grow"><b class="mono">${esc(check.contract_id)}</b> · ${esc(shortDate(check.date || ''))}<div class="sub">Реестр: ${esc(amount(check.registry_sum,check.currency))}<br>Чеки на эту дату: ${esc(amount(check.receipt_sum,check.currency))}<br>Разница: ${esc(amount(check.difference,check.currency))}</div></div></div>`).join('')}<p class="muted">Сравнение касается последнего платежа, а не всей истории зачислений.</p></details>` : ''}
    ${d.current_promises?.length ? `<div class="notice"><span>${d.current_promises.map((p: any) => `Просрочка Софта: ${esc(p.promise_date)} · ${n(p.days_overdue)} дн · ${esc(p.money_text)}; по реестру ${esc(p.received)} ${esc(p.unit)}; автор ${esc(p.author)}`).join("<br>")}</span></div>` : ""}
    ${form ? `<div class="block"><h3>${d.profile === "soft" ? "Звонок" : "Выезд"} ${esc(shortDate(d.date || ""))}</h3><dl class="kv">${form}</dl></div>` : ""}
    ${d.calls?.length ? `<div class="block"><h3>История звонков</h3>${d.calls.map((c: any) => `<p>${esc(c.day)} · ${esc(c.payload)}</p>`).join("")}</div>` : ""}
    ${d.reported_payments?.length ? `<div class="block"><h3>Записанные сотрудниками оплаты</h3>${d.reported_payments.map((p:any)=>`<div class="list-row"><div class="grow"><b>${esc(Number(p.amount).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}))} ${esc(p.currency || '')}</b> · ${esc(p.paid_at)}<div class="sub">КД ${esc(p.contract_id)} · ${esc(p.employee)}${p.result_status?' · '+esc(p.result_status):''}</div></div></div>`).join('')}</div>` : ''}
    ${d.office_history?.length ? `<div class="block"><h3>Офисная история</h3>${d.office_history.map((e:any)=>`<div class="list-row"><div class="grow"><b>${esc(e.title)}</b> · ${esc(e.day)}<div class="sub">${esc(e.employee)} · ${esc(e.values.note || e.values.result || '')}${e.values.amount?' · '+esc(e.values.amount)+' '+esc(e.currency || ''):''}</div></div></div>`).join('')}</div>` : ''}
    ${d.comment ? `<div class="block"><h3>Комментарий</h3><div style="white-space:pre-wrap;font-size:14px;line-height:1.55">${esc(d.comment_display ?? d.comment)}</div></div>` : ""}
    ${d.receipts?.length ? `<div class="block"><h3>Квитанции для сверки</h3>${d.receipts.map((receipt: any) => `<div class="list-row"><div class="grow"><b>${esc(receipt.amount ?? '—')} ${esc(receipt.currency || '')}</b> · ${esc(receipt.paid_at || receipt.created_at || '')}<div class="sub">${esc(receipt.employee || '')}${receipt.payer_note ? ' · ' + esc(receipt.payer_note) : ''}</div></div><button class="btn small" data-receipt="${esc(receipt.file)}">Снимок</button></div>`).join('')}<p class="muted">Наличие квитанции не подтверждает зачисление. Сверьте операцию с реестром платежей.</p></div>` : ''}
    <div class="block" id="ph-block" hidden><h3>Фото</h3><div class="photos" id="ph"></div></div>`;
  body.querySelectorAll<HTMLButtonElement>('[data-receipt]').forEach(button => button.addEventListener('click', async () => {
    const src = await hard.image('/api/manager/receipt_photo', { file: button.dataset.receipt });
    if (src) lightbox(src); else button.textContent = 'Не удалось открыть';
  }));
  body.querySelector('[data-case-previous]')?.addEventListener('click',()=>navigation?.previous?.());
  body.querySelector('[data-case-next]')?.addEventListener('click',()=>navigation?.next?.());
  if (d.profile !== "soft") void loadPhotos(rep, date, body);
}

export async function loadPhotos(rep: string, date: string | undefined, host: HTMLElement) {
  const r = await api("/api/manager/photos", { contract_id: rep });
  if (!r.ok || !host.isConnected) return;
  let list = ((r.data?.items || []) as any[]).filter((x) => !x.missing);
  if (date) {
    const same = list.filter((x) => String(x.shot_at || x.uploaded_at || "").startsWith(date));
    if (same.length) list = same;
  }
  list = list.slice(0, 12);
  const box = host.querySelector<HTMLElement>("#ph");
  const block = host.querySelector<HTMLElement>("#ph-block");
  if (!box || !block || !list.length) return;
  block.hidden = false;
  for (const ph of list) {
    const img = document.createElement("img");
    img.alt = "";
    img.title = String(ph.shot_at || ph.uploaded_at || "").replace("T", " ").slice(0, 16);
    box.appendChild(img);
    void hard.image("/api/manager/photo", { file: ph.file }).then((src) => {
      if (src) { img.src = src; img.addEventListener("click", () => lightbox(src)); } else img.remove();
    });
  }
}

export function refreshButton(): string {
  return `<button class="icon-btn" data-refresh title="Обновить (F5)">${icon("refresh")}</button>`;
}
