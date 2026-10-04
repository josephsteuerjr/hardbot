// «Контроль» — только безопасность (и владелец). Процентов и сводного балла
// НЕТ (решение 03.10.2026): только счётчики по каждому признаку, отдельно.
// Слои: статистика по людям, гео-запись дня по времени (сопоставление фрода
// с перемещением), контрольный обзвон, закрепления и доступ.
import { api, hard } from "../kit/api";
import { dayTitle, esc, icon, n, plural, toast } from "../kit/dom";
import { setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { evidenceTag, flagsHtml, officeChips, openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";

const state = {
  tab: "risk" as "risk" | "calls" | "assign" | "access",
  range: { preset: "30d" } as Range,
  office: "",
  employee: "",
  day: "",
  geoDay: "",
};

/** Как звать сервер: руководитель — своим токеном (/api/control/*), аналитик —
 *  через питон-сторону пульта (/admin/control/*, вся компания). Одна и та же
 *  вёрстка и правила, разный только транспорт. */
export type ControlCall = (path: string, query?: Record<string, unknown>,
                           method?: string, body?: unknown) =>
  Promise<{ ok: boolean; data?: any; text?: string }>;

let call: ControlCall = (p, q, m, b) => api(p, q, m, b);
let canExcel = true;

/** Транспорт пульта аналитика: локальный мост → устройство пульта. */
export async function adminControlCall(path: string, query?: Record<string, unknown>,
                                        method = "GET", body?: unknown) {
  const qs = new URLSearchParams(
    Object.entries(query || {})
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
  const p = path.replace("/api/control", "/admin/control") + (qs ? "?" + qs : "");
  const r = await hard.local("/admin", { method, path: p, ...(body !== undefined ? { body } : {}) });
  return r.ok ? { ok: true, data: r.data } : { ok: false, text: r.error || "Ошибка связи с питон-стороной" };
}

/** Колонки-счётчики: один признак — одна колонка, без процентов и весов. */
const COUNTERS: [string, string][] = [
  ["far", "Далеко от адреса"],
  ["nogeo", "Нет GPS-точки"],
  ["addr_coarse", "Адрес неточный"],
  ["late", "Внесено позже"],
  ["fast", "За полминуты"],
  ["night", "Ночью"],
  ["series", "Внесено пачкой"],
  ["cash", "«Наличные» в тексте"],
  ["paid", "«Оплатил» без платежа"],
  ["bymgr", "Внесено руководителем"],
  ["dupphoto", "Фото дважды"],
  ["callback", "Обзвон: не подтвердилось"],
];

function counterCell(key: string, title: string) {
  return {
    key, title, num: true as const, value: (x: any) => x.counts[key] || 0,
    html: (x: any) => (x.counts[key] ? `<b>${n(x.counts[key])}</b>` : '<span class="muted">—</span>'),
  };
}

function tabs(): string {
  return `<div class="chips"><button class="chip" data-tab="risk" aria-pressed="${state.tab === "risk"}">Статистика по людям</button>
    <button class="chip" data-tab="calls" aria-pressed="${state.tab === "calls"}">Контрольный обзвон</button>
    <button class="chip" data-tab="assign" aria-pressed="${state.tab === "assign"}">Закрепления</button>
    <button class="chip" data-tab="access" aria-pressed="${state.tab === "access"}">Доступ</button>
    </div>`;
}

async function renderRisk(root: HTMLElement) {
  setHead("Контроль", "статистика признаков — только факты, без процентов");
  const r = await call("/api/control/overview", { ...rangeQuery(state.range), office: state.office });
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = tabs() + problem(r.text || "Не загрузилось"); wire(root); return; }
  const d = r.data;
  const rows = d.rows as any[];
  const sum = (k: string) => rows.reduce((s, x) => s + (x.counts[k] || 0), 0);
  const vis = rows.reduce((s, x) => s + (x.verifiable || 0), 0);
  const visAll = rows.reduce((s, x) => s + (x.visits || 0), 0);
  root.innerHTML = `${staleNote((r as { stale?: string }).stale)}
    <div class="toolbar">${tabs()}<span class="spacer"></span>${canExcel ? `<button class="btn small" data-xlsx>${icon("download")}Excel</button>` : ""}</div>
    <div class="toolbar">${rangeChips(state.range, d.range, ["7d", "30d", "month"])}<span class="spacer"></span>${officeChips(d.offices, state.office)}</div>
    <div class="kpis"><div class="kpi"><b>${n(visAll)}</b><span>выездов в окне</span></div>
      <div class="kpi accent"><b>${n(sum("far"))}</b><span>далеко от адреса</span></div>
      <div class="kpi"><b>${n(vis)}</b><span>проверяемых (точный адрес + GPS)</span></div>
      <div class="kpi"><b>${n(sum("addr_coarse"))}</b><span>адрес неточный — не проверить</span></div>
      <div class="kpi"><b>${n(rows.reduce((s, x) => s + x.checks.no_visit + x.checks.cash, 0))}</b><span>не подтвердилось звонком</span></div></div>
    <div class="section" id="risk-tbl"></div>
    <div class="section"><div class="section-head"><h2 class="section-title">Как читать</h2></div>
      <ul style="margin:4px 0 0 18px;line-height:1.6">${(d.how as string[]).map((t) => `<li style="font-size:13.5px">${esc(t)}</li>`).join("")}</ul></div>
    <div class="section"><div class="section-head"><h2 class="section-title">Что значат признаки</h2></div>
      <div class="grid2">${(d.legend as any[]).map((l) => `<div class="list-row"><div class="grow"><div class="nm">${esc(l.label)}</div><div class="sub">${esc(l.hint)}</div></div></div>`).join("")}</div></div>`;
  wire(root);
  new DataTable<any>(root.querySelector("#risk-tbl")!, {
    id: "control-risk", exportName: "Контроль — статистика", key: (x) => x.employee,
    cols: [
      { key: "emp", title: "Сотрудник", value: (x) => x.employee, html: (x) => `<span class="who">${esc(x.employee)}</span><span class="sub">${esc(x.office || "без офиса")}</span>` },
      { key: "office", title: "Офис", value: (x) => x.office || "без офиса" },
      { key: "visits", title: "Выездов", num: true, value: (x) => x.visits },
      { key: "verifiable", title: "Проверяемых", num: true, value: (x) => x.verifiable,
        html: (x) => `${n(x.verifiable)}<span class="sub"> из ${n(x.visits)}</span>` },
      ...COUNTERS.map(([key, title]) => counterCell(key, title)),
    ],
    onRow: (x) => { state.employee = x.employee; state.geoDay = ""; void renderFlags(root); },
  }).setRows(rows);
  root.querySelector("[data-xlsx]")?.addEventListener("click", async () => {
    const res = await hard.download("/api/control/export.xlsx", rangeQuery(state.range), `Контроль ${d.range.from}—${d.range.to}.xlsx`);
    if (res.ok && res.file) { toast("Книга сохранена"); void hard.open(res.file); } else if (res.text) toast(res.text);
  });
}

/** Плеер дня: трек на карте (или схеме, если карту не подняли), ползунок
 *  времени, play и скорость. Перемотка произвольная — тащишь ползунок,
 *  видишь, где человек был в этот момент; хвост подсвечен. */
interface TrackPoint { t: number; lat: number; lon: number; acc: number | null; session?: string }
interface PlayerCtl { stop(): void }

let playerCtl: PlayerCtl | null = null;
let deskMapLibs: Promise<boolean> | null = null;

function ensureDeskMapLibs(server: string): Promise<boolean> {
  if (deskMapLibs) return deskMapLibs;
  const addCss = (href: string) => {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href;
    document.head.appendChild(l);
  };
  const addJs = (src: string) => new Promise<boolean>(res => {
    const s = document.createElement("script"); s.src = src;
    s.onload = () => res(true); s.onerror = () => res(false);
    document.head.appendChild(s);
  });
  deskMapLibs = (async () => {
    const w = window as any;
    if (w.maplibregl && w.pmtiles && w.protomaps_themes_base) return true;
    addCss(`${server}/vendor/maplibre-gl-4.7.1.css`);
    if (!await addJs(`${server}/vendor/maplibre-gl-4.7.1.js`)) return false;
    await addJs(`${server}/vendor/pmtiles-3.2.1.js`);
    await addJs(`${server}/vendor/protomaps-themes-base-4.4.0.js`);
    return !!w.maplibregl;
  })();
  return deskMapLibs;
}

function deskMapStyle(server: string): any {
  const w = window as any;
  if (!w.maplibregl.__pmtiles_proto) {
    w.maplibregl.addProtocol("pmtiles", new w.pmtiles.Protocol().tile);
    w.maplibregl.__pmtiles_proto = true;
  }
  const themed = w.protomaps_themes_base.layers
    ? w.protomaps_themes_base.layers("protomaps", "light", "ru")
    : w.protomaps_themes_base.default("protomaps", "light");
  // Symbol-слои требуют внешних glyph — без них (как в миниаппе).
  return { version: 8,
    sources: { protomaps: { type: "vector", url: `pmtiles://${server}/tiles/basemap.pmtiles`, attribution: "© OpenStreetMap" } },
    layers: themed.filter((l: any) => l.type !== "symbol") };
}

function trackAt(track: TrackPoint[], t: number): TrackPoint | null {
  if (!track.length) return null;
  if (t <= track[0].t) return track[0];
  if (t >= track[track.length - 1].t) return track[track.length - 1];
  let lo = 0, hi = track.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (track[mid].t <= t) lo = mid; else hi = mid;
  }
  const a = track[lo], b = track[hi];
  if (t === a.t) return a;
  if (b.t - a.t > 10 * 60000 || a.session !== b.session) return null;
  const k = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
  return { t, lat: a.lat + (b.lat - a.lat) * k, lon: a.lon + (b.lon - a.lon) * k, acc: b.acc };
}

/** Время трека — метки эпохи; показываем местное время офисов (+5), каким
 *  его видят в ленте дня, — а не пояс компьютера руководителя. */
const FIELD_TZ_MIN = 5 * 60;
function hhmmField(t: number): string {
  const d = new Date(t + FIELD_TZ_MIN * 60000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

async function mountDayPlayer(box: HTMLElement, track: TrackPoint[], visits: any[]) {
  if (playerCtl) { playerCtl.stop(); playerCtl = null; }
  if (!track.length) return;                      // трека нет — только лента
  const st = await hard.state().catch(() => null);
  const server = (st?.server || "").replace(/\/$/, "");
  const t0 = track[0].t, t1 = track[track.length - 1].t;

  box.innerHTML = `
    <div class="daymap" id="daymap"><div class="daymap-fb" id="daysvg"></div></div>
    <div class="dayplayer">
      <button class="btn small" id="dp-play" title="Проиграть день">▶</button>
      <select class="input" id="dp-speed" style="width:auto">
        <option value="60">×60</option><option value="120" selected>×120</option>
        <option value="300">×300</option><option value="600">×600</option>
      </select>
      <input type="range" id="dp-range" min="${t0}" max="${t1}" step="30000" value="${t0}">
      <span class="mono nowrap" id="dp-time">${hhmmField(t0)}</span>
    </div>
    <p class="muted" style="font-size:12.5px;margin:4px 0 0">Ползунок показывает оценку положения между замерами телефона. В разрывах записи положение неизвестно. Грубые точки исключены; путь приблизительный. Красным — последние 15 минут записи. Время офисов.</p>`;

  const mapEl = box.querySelector<HTMLElement>("#daymap")!;
  const svgEl = box.querySelector<HTMLElement>("#daysvg")!;
  const range = box.querySelector<HTMLInputElement>("#dp-range")!;
  const timeEl = box.querySelector<HTMLElement>("#dp-time")!;
  const playBtn = box.querySelector<HTMLButtonElement>("#dp-play")!;
  const speedSel = box.querySelector<HTMLSelectElement>("#dp-speed")!;

  let map: any = null;
  let tailSrc: any = null;
  let head: any = null;
  const visitPts = visits.filter(v => v.lat != null && v.lon != null);

  // Схема (fallback) строится всегда — дёшево и без сети; карту рисуем сверху.
  const lats = track.map(p => p.lat), lons = track.map(p => p.lon);
  const la0 = Math.min(...lats), la1 = Math.max(...lats);
  const lo0 = Math.min(...lons), lo1 = Math.max(...lons);
  const W = 900, H = 460;
  const project = (p: { lat: number; lon: number }): [number, number] => {
    const spanA = Math.max(1e-6, la1 - la0 + 0.06), spanO = Math.max(1e-6, lo1 - lo0 + 0.06);
    const k = Math.min(W / spanO, H / spanA);
    return [W / 2 + (p.lon - (lo0 + lo1) / 2) * k, H / 2 - (p.lat - (la0 + la1) / 2) * k];
  };
  const pathOf = (pts: TrackPoint[]) => pts.map(p => project(p).map(v => v.toFixed(1)).join(",")).join(" ");
  // Пауза записи >10 мин — разрыв линии: видно, где телефон молчал.
  const chunks: TrackPoint[][] = [[track[0]]];
  for (let i = 1; i < track.length; i++) {
    if (track[i].t - track[i - 1].t > 10 * 60000 || track[i].session !== track[i - 1].session) chunks.push([]);
    chunks[chunks.length - 1].push(track[i]);
  }
  svgEl.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%">
      <path fill="none" stroke="#93a3b3" stroke-width="2.5" stroke-linejoin="round" d="${chunks.map(c => `M${pathOf(c)}`).join(" ")}"></path>
      <path id="dsvg-tail" fill="none" stroke="#B0472C" stroke-width="3.5" stroke-linejoin="round" d=""></path>
      ${visitPts.map(v => { const [x, y] = project(v); return `<circle cx="${x}" cy="${y}" r="5" fill="${v.flags && v.flags.length ? "#B0472C" : "#2c6e46"}"><title>${esc(v.debtor || v.rep)}${v.flags && v.flags.length ? " · " + esc(v.flags.join(", ")) : ""}</title></circle>`; }).join("")}
      <circle id="dsvg-dot" r="7" fill="#B0472C" stroke="#fff" stroke-width="2"></circle>
    </svg>`;
  const svgLine = { tail: svgEl.querySelector("#dsvg-tail")!, dot: svgEl.querySelector("#dsvg-dot")! };

  if (server && await ensureDeskMapLibs(server)) {
    try {
      const w = window as any;
      const ll = track.map(p => [p.lon, p.lat]);
      const bounds = ll.reduce((b, c) => b.extend(c), new w.maplibregl.LngLatBounds(ll[0], ll[0]));
      map = new w.maplibregl.Map({ container: mapEl, style: deskMapStyle(server),
        bounds: [[bounds.getWest() - 0.01, bounds.getSouth() - 0.01], [bounds.getEast() + 0.01, bounds.getNorth() + 0.01]],
        fitBoundsOptions: { padding: 30 }, attributionControl: false, dragRotate: false });
      map.on("load", () => {
        map.addSource("track", { type: "geojson", data: { type: "MultiLineString", coordinates: chunks.filter(c=>c.length>1).map(c=>c.map(p=>[p.lon,p.lat])) } });
        map.addLayer({ id: "track", type: "line", source: "track",
          paint: { "line-color": "#93a3b3", "line-width": 3 } });
        map.addSource("tail", { type: "geojson", data: { type: "MultiLineString", coordinates: [] } });
        tailSrc = map.getSource("tail");
        map.addLayer({ id: "tail", type: "line", source: "tail",
          paint: { "line-color": "#B0472C", "line-width": 4 } });
        for (const v of visitPts) {
          const elx = document.createElement("div");
          elx.className = "dmap-visit" + (v.flags && v.flags.length ? " bad" : "");
          elx.title = `${v.debtor || v.rep}${v.flags && v.flags.length ? " · " + v.flags.join(", ") : ""}`;
          new w.maplibregl.Marker({ element: elx }).setLngLat([v.lon, v.lat]).addTo(map);
        }
        const hd = document.createElement("div");
        hd.className = "dmap-head";
        head = new w.maplibregl.Marker({ element: hd }).setLngLat(ll[0]).addTo(map);
      });
      map.on("error", () => { /* подложка не поднялась — схема уже нарисована */ });
    } catch { map = null; }
  }

  const TAIL_MS = 15 * 60000;
  function update(t: number) {
    const p = trackAt(track, t);
    const tailChunks = chunks.map(c=>c.filter(q => q.t >= t - TAIL_MS && q.t <= t)).filter(c=>c.length>1);
    timeEl.textContent = hhmmField(t)+(p ? "" : " · нет записи");
    svgLine.dot.setAttribute("visibility",p ? "visible" : "hidden");
    if(head) head.getElement().style.display=p ? "" : "none";
    svgLine.tail.setAttribute("d",tailChunks.map(c=>`M${pathOf(c)}`).join(" "));
    if (map && tailSrc) tailSrc.setData({type:"MultiLineString",coordinates:tailChunks.map(c=>c.map(q=>[q.lon,q.lat]))});
    if(!p) return;
    const [x, y] = project(p);
    svgLine.dot.setAttribute("cx", String(x)); svgLine.dot.setAttribute("cy", String(y));
    if (map && head) head.setLngLat([p.lon, p.lat]);
  }

  let timer: number | undefined;
  const stop = () => { if (timer) { clearInterval(timer); timer = undefined; } playBtn.textContent = "▶"; };
  playerCtl = { stop };
  range.addEventListener("input", () => { stop(); update(Number(range.value)); });
  playBtn.addEventListener("click", () => {
    if (timer) { stop(); return; }
    let t = Number(range.value) >= t1 - 30000 ? t0 : Number(range.value);
    playBtn.textContent = "⏸";
    timer = window.setInterval(() => {
      t += Number(speedSel.value) * 100;
      if (t >= t1) { t = t1; stop(); }
      range.value = String(t);
      update(t);
    }, 100);
  });
  update(t0);
}

/** Гео-запись одного дня по времени: плеер + лента (выезды, отрезки, тревога). */
async function renderDay(root: HTMLElement, day: string) {
  const body = root.querySelector("#day-body") as HTMLElement | null;
  if (!body) return;
  if (playerCtl) { playerCtl.stop(); playerCtl = null; }
  body.innerHTML = '<div class="skeleton"></div>';
  const r = await call("/api/control/day", { ...rangeQuery(state.range), employee: state.employee, date: day });
  if (!root.isConnected || !body.isConnected) return;
  if (!r.ok) { body.innerHTML = problem(r.text || "Не загрузилось"); return; }
  const d = r.data;
  const evs = d.events as any[];
  const gaps = d.gaps as any[];
  const visits = evs.filter(e => e.kind === "visit").map(e => e.event);
  const track = (d.track || []) as TrackPoint[];
  body.innerHTML = `<div class="dayplayer-box" id="player-box"></div><div id="day-rest"></div>`;
  const rest = body.querySelector<HTMLElement>("#day-rest")!;
  rest.innerHTML = `
    <div class="kpis"><div class="kpi"><b>${n(d.points)}</b><span>точек для расчёта</span></div>
      <div class="kpi"><b>${d.km ? String(d.km).replace(".", ",") : "0"}</b><span>км по записи</span></div>
      <div class="kpi"><b>${esc(d.first || "—")}</b><span>первая точка</span></div>
      <div class="kpi"><b>${esc(d.last || "—")}</b><span>последняя точка</span></div></div>
    ${d.excluded_points ? `<p class="muted">Из ${n(d.raw_points)} исходных точек исключено ${n(d.excluded_points)} неточных или повторных замеров.</p>` : ""}
    ${gaps.length ? `<div class="notice"><span class="grow">Паузы записи: ${gaps.map((g) => `${esc(g.from)}–${esc(g.to)} (${n(g.min)} мин)`).join(", ")}. В эти интервалы достоверных точек нет; причина по записи неизвестна.</span></div>` : ""}
    <div class="feed">${evs.map((e) => {
      if (e.kind === "move") {
        const s = e.event;
        return `<div class="ev"><time>${esc(s.from)}</time><span class="mark"></span>
          <div><div class="line1">${s.km > 0 ? "Движение по записи" : "Точки в пределах точности"} ${esc(s.from)}–${esc(s.to)} · ${n(s.min)} мин</div>
          <div class="line2">${String(s.km).replace(".", ",")} км · точек ${n(s.n)}</div></div></div>`;
      }
      if (e.kind === "sos") {
        const s = e.event;
        return `<div class="ev"><time>${esc(String(s.ts || "").slice(11, 16))}</time><span class="mark" style="background:#B0472C"></span>
          <div><div class="line1"><b style="color:#B0472C">СИГНАЛ ТРЕВОГИ</b>${s.battery != null ? ` · заряд ${n(s.battery)}%` : ""}</div>
          ${s.maps ? `<div class="line2"><a href="#" data-map="${esc(s.maps)}">точка на карте</a></div>` : ""}</div></div>`;
      }
      const v = e.event;
      if(e.kind === "action") return `<div class="ev pick" ${v.rep ? `data-rep="${esc(v.rep)}" data-day="${esc(v.day)}"` : ""}><time>${esc(v.time)}</time><span class="mark"></span><div><div class="line1">${esc(v.title)}${v.debtor ? " · "+esc(v.debtor) : ""}${v.visit_op ? ' · связан с выездом' : ''}</div></div></div>`;
      return `<div class="ev visit pick" data-rep="${esc(v.rep)}" data-day="${esc(v.day)}"><time>${esc(v.time)}</time><span class="mark"></span>
        <div><div class="line1"><b>${esc(v.debtor || v.rep)}</b> — ${esc(v.title)}${v.agreed ? ` · ${esc(v.agreed)}` : ""}</div>
        ${v.detail ? `<div class="line2">${esc(v.detail)}</div>` : ""}${flagsHtml(v.flags)}</div>
        <div class="side">${evidenceTag(v.evidence)}</div></div>`;
    }).join("") || '<div class="empty"><b>Пусто</b>Ни выездов, ни гео-записи в этот день.</div>'}</div>`;
  rest.querySelectorAll<HTMLElement>(".ev[data-rep]").forEach((row) => row.addEventListener("click", () => {
    void openPerson(state.employee, row.dataset.rep!, row.dataset.day);
  }));
  rest.querySelectorAll<HTMLAnchorElement>("[data-map]").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault(); window.open(a.dataset.map!, "_blank");
  }));
  const pbox = body.querySelector<HTMLElement>("#player-box")!;
  if (track.length) {
    void mountDayPlayer(pbox, track, visits);
  } else {
    pbox.innerHTML = '<p class="muted" style="font-size:13.5px">В этот день гео-записи нет — только выезды. Трек появится, когда сотрудник включает «На линии».</p>';
  }
}

async function renderGeoSection(root: HTMLElement) {
  const box = root.querySelector("#geo-box") as HTMLElement | null;
  if (!box) return;
  const r = await call("/api/control/geo_days", { ...rangeQuery(state.range), employee: state.employee });
  if (!root.isConnected || !box.isConnected) return;
  if (!r.ok) { box.innerHTML = problem(r.text || "Не загрузилось"); return; }
  const days = (r.data.days as any[]) || [];
  if (!days.length) {
    box.innerHTML = '<p class="muted" style="font-size:13.5px">За это окно гео-записи и выездов нет.</p>';
    return;
  }
  const first = state.geoDay && days.some((x) => x.day === state.geoDay) ? state.geoDay : days[0].day;
  state.geoDay = first;
  box.innerHTML = `<div class="chips" style="margin-bottom:10px">${days.slice(0, 14).map((x) =>
    `<button class="chip" data-gday="${esc(x.day)}" aria-pressed="${x.day === first}">${esc(dayTitle(x.day))}<span class="n">${n(x.points || x.visits || 0)}</span></button>`).join("")}</div>
    <div id="day-body"></div>`;
  box.querySelectorAll<HTMLButtonElement>("[data-gday]").forEach((b) => b.addEventListener("click", () => {
    state.geoDay = b.dataset.gday!;
    box.querySelectorAll("[data-gday]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    void renderDay(root, state.geoDay);
  }));
  void renderDay(root, state.geoDay);
}

async function renderFlags(root: HTMLElement) {
  const emp = state.employee;
  setHead(emp, "выезды с признаками");
  root.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div>';
  const r = await call("/api/control/flags", { ...rangeQuery(state.range), employee: emp });
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
  const d = r.data;
  const all = d.visits as any[];
  const flagged = all.filter((v) => v.flags.length);
  const verifiable = all.filter((v) => v.evidence === "confirmed" || v.evidence === "far").length;
  const coarse = all.filter((v) => v.evidence === "coarse").length;
  let day = "";
  const rows = flagged.map((v) => {
    const head = v.day !== day ? `<div class="feed-day">${esc(dayTitle(v.day))}</div>` : "";
    day = v.day;
    return `${head}<div class="ev visit pick" data-rep="${esc(v.rep)}" data-day="${esc(v.day)}"><time>${esc(v.time)}</time><span class="mark"></span>
      <div><div class="line1"><b>${esc(v.debtor || v.rep)}</b> — ${esc(v.title)}${v.agreed ? ` · ${esc(v.agreed)}` : ""}</div>
      ${v.detail ? `<div class="line2">${esc(v.detail)}</div>` : ""}${flagsHtml(v.flags)}</div>
      <div class="side">${evidenceTag(v.evidence)}${v.late_h ? `<br>доехал через ${n(v.late_h)} ч` : ""}</div></div>`;
  }).join("");
  root.innerHTML = `<div class="toolbar"><button class="btn small" data-back>← Все люди</button>${rangeChips(state.range, d.range, ["7d", "30d", "month"])}</div>
    <div class="kpis"><div class="kpi"><b>${n(all.length)}</b><span>выездов за окно</span></div>
      <div class="kpi accent"><b>${n(flagged.length)}</b><span>с признаками</span></div>
      <div class="kpi"><b>${n(all.filter((v) => v.flags.includes("far")).length)}</b><span>далеко (из ${n(verifiable)} проверяемых)</span></div>
      <div class="kpi"><b>${n(coarse)}</b><span>адрес неточный</span></div>
      <div class="kpi"><b>${n(all.filter((v) => v.flags.includes("nogeo")).length)}</b><span>нет GPS-точки</span></div></div>
    <div class="section feed">${rows || '<div class="empty"><b>Чисто</b>Выездов с признаками за это окно нет.</div>'}</div>
    <div class="section"><div class="section-head"><h2 class="section-title">Гео по времени</h2></div>
      <p class="muted" style="font-size:13.5px;margin:0 0 10px">Запись смены «На линии» и поездок: движение отрезками, выезды и тревога одной лентой по времени.</p>
      <div id="geo-box"></div></div>`;
  root.querySelector("[data-back]")?.addEventListener("click", () => { state.employee = ""; void renderRisk(root); });
  wireRange(root, state.range, () => void renderFlags(root));
  root.querySelectorAll<HTMLElement>(".ev[data-rep]").forEach((row) => row.addEventListener("click", () => {
    root.querySelectorAll('.ev[aria-selected="true"]').forEach((x) => x.removeAttribute("aria-selected"));
    row.setAttribute("aria-selected", "true");
    void openPerson(emp, row.dataset.rep!, row.dataset.day);
  }));
  void renderGeoSection(root);
}

async function renderAssign(root: HTMLElement) {
  setHead("Закрепления", "кому приехали договоры — и чьи они были");
  const r = await call("/api/control/assignments", rangeQuery(state.range));
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = tabs() + problem(r.text || "Не загрузилось"); wire(root); return; }
  const d = r.data;
  const rows = d.rows as any[];
  const evs = d.events as any[];
  const KIND: Record<string, string> = { transfer: "передача", assign: "новое", removed: "снято" };
  root.innerHTML = `${staleNote((r as { stale?: string }).stale)}
    <div class="toolbar">${tabs()}</div>
    <div class="toolbar">${rangeChips(state.range, d.range, ["7d", "30d", "month"])}</div>
    <div class="kpis"><div class="kpi"><b>${n(rows.reduce((s, x) => s + x.received, 0))}</b><span>договоров передано</span></div>
      <div class="kpi accent"><b>${n(rows.reduce((s, x) => s + x.backdated, 0))}</b><span>закреплено задним числом</span></div>
      <div class="kpi"><b>${n(evs.filter((e) => e.kind === "transfer").length)}</b><span>передач между людьми</span></div></div>
    <div class="section" id="assign-tbl"></div>
    <div class="section"><div class="section-head"><h2 class="section-title">События</h2></div>
      <div class="feed">${evs.slice(0, 100).map((e) => `<div class="ev"><time>${esc(e.window_to)}</time><span class="mark"></span>
        <div><div class="line1"><b>${esc(e.employee || "(снят)")}</b> ← ${esc(e.prev_employee || "без ответственного")} · ${KIND[e.kind] || e.kind}${e.claimed_at ? ` <span class="muted">· «дата закрепления» ${esc(e.claimed_at)}</span>` : ""}</div>
        <div class="line2">${e.profile === 'soft' ? 'Софт' : 'Хард'} · <span class="mono">${esc(e.contract_id)}</span>${e.debt ? ` · долг ${n(e.debt)}` : ""}</div></div></div>`).join("") || '<div class="empty"><b>Тихо</b>Передач за это окно не было.</div>'}</div>
      <p class="muted" style="font-size:13.5px;max-width:760px;margin-top:12px">События снимает импорт общего реестра (окно факта); «дата закрепления» пишется руками и может отличаться — расхождение больше ${n(d.claimed_lag_days || 7)} дней помечено как задним числом. Медиана долга выше офисной — вопрос, а не приговор: смотрите, у кого это стабильно.</p></div>`;
  wire(root);
  new DataTable<any>(root.querySelector("#assign-tbl")!, {
    id: "control-assign", exportName: "Закрепления", key: (x) => `${x.profile}:${x.employee}`,
    cols: [
      { key: "emp", title: "Сотрудник", value: (x) => x.employee,
        html: (x) => `<span class="who">${esc(x.employee)}</span><span class="sub">${esc(x.office || "без офиса")}</span>` },
      { key: "received", title: "Получил", num: true, value: (x) => x.received },
      { key: "profile", title: "Профиль", value: (x) => x.profile === 'soft' ? 'Софт' : 'Хард' },
      { key: "transfers", title: "Передач", num: true, value: (x) => x.transfers },
      { key: "paying", title: "Платящих", num: true, value: (x) => x.paying },
      { key: "median", title: "Медиана долга", num: true, value: (x) => x.median_debt,
        html: (x) => `${n(x.median_debt)}${x.debt_ratio ? ` <span class="muted">· ×${x.debt_ratio} от офиса (${n(x.office_median)})</span>` : ""}` },
      { key: "backdated", title: "Задним числом", num: true, value: (x) => x.backdated,
        html: (x) => (x.backdated ? `<span class="tag err">${n(x.backdated)}</span>` : '<span class="muted">—</span>') },
      { key: "peer", title: "Чаще всех от", value: (x) => x.top_peer || "",
        html: (x) => (x.top_peer ? `${esc(x.top_peer)} <span class="muted">· ${n(x.top_peer_n)}</span>` : '<span class="muted">—</span>') },
    ],
  }).setRows(rows);
}


async function renderAccess(root: HTMLElement) {
  setHead("Доступ", "кто кому выдал и кто что отозвал");
  const r = await call("/api/control/access", rangeQuery(state.range));
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = tabs() + problem(r.text || "Не загрузилось"); wire(root); return; }
  const d = r.data;
  const evs = d.events as any[];
  root.innerHTML = `${staleNote((r as { stale?: string }).stale)}
    <div class="toolbar">${tabs()}</div>
    <div class="toolbar">${rangeChips(state.range, d.range, ["7d", "30d", "month"])}</div>
    <div class="kpis"><div class="kpi"><b>${n(evs.length)}</b><span>событий доступа</span></div>
      <div class="kpi accent"><b>${n(evs.filter((e) => /revoke|unbind|сброс/i.test(e.action)).length)}</b><span>отзывов и сбросов</span></div>
      <div class="kpi"><b>${n(evs.filter((e) => e.action === "mgr_app_link").length)}</b><span>выдано входов</span></div></div>
    <div class="section feed">${evs.map((e) => `<div class="ev"><time>${esc(String(e.ts || "").replace("T", " ").slice(5, 16))}</time><span class="mark"></span>
      <div><div class="line1"><b>${esc(e.actor_name || ("id " + e.actor))}</b> — ${esc(e.label)}${e.target ? ` · <b>${esc(e.target)}</b>` : ""}</div>
      ${e.detail ? `<div class="line2">${esc(e.detail)}</div>` : ""}</div></div>`).join("") || '<div class="empty"><b>Тихо</b>Событий доступа за это окно нет.</div>'}</div>
    <p class="muted" style="font-size:13.5px;max-width:760px;margin-top:12px">Выдача входа — действие руководителя или аналитика; сама по себе не нарушение, но «любимчику второй вход в 23:40» — вопрос. Просмотры карточек должников лежат в журнале на сервере.</p>`;
  wire(root);
}

async function renderCalls(root: HTMLElement) {
  setHead("Контрольный обзвон", "позвонить должнику и сверить со словами выездника");
  const r = await call("/api/control/callbacks", { date: state.day || undefined, office: state.office });
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = tabs() + problem(r.text || "Не загрузилось"); wire(root); return; }
  const d = r.data;
  state.day = d.date;
  const items = d.items as any[];
  const todo = items.filter((x) => x.state === "todo").length;
  const outcomes = d.outcomes as { key: string; label: string }[];
  root.innerHTML = `${staleNote((r as { stale?: string }).stale)}
    <div class="toolbar">${tabs()}<span class="spacer"></span>
      <div class="chips"><button class="chip" data-day="${esc(d.prev_date)}">‹</button>
      <span class="muted nowrap" style="font-size:13.5px;padding:0 6px">${esc(dayTitle(d.date, d.today))}</span>
      <button class="chip" data-day="${esc(d.next_date || "")}" ${d.next_date ? "" : "disabled"}>›</button></div></div>
    <div class="toolbar">${officeChips(d.offices, state.office)}</div>
    <div class="kpis"><div class="kpi accent"><b>${n(todo)}</b><span>осталось позвонить</span></div>
      <div class="kpi"><b>${n(items.length - todo)}</b><span>проверено</span></div>
      <div class="kpi"><b>${n(items.filter((x) => x.outcome === "no_visit" || x.outcome === "cash_to_employee").length)}</b><span>не подтвердилось</span></div></div>
    <div class="section"><details><summary class="link" style="cursor:pointer">Что спрашивать</summary>
      <ol class="script" style="margin-top:8px">${(d.script as string[]).map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details></div>
    <div class="section">${items.map((x) => `<div class="call${x.state === "done" ? " done" : ""}" data-vid="${x.visit_id}">
      <div><h4>${esc(x.debtor || x.rep)}</h4>
        <div class="muted" style="font-size:13px">${esc(x.employee)}${x.office ? ` · ${esc(x.office)}` : ""} · выезд ${esc(x.time)} · <span class="mono">${esc(x.rep)}</span></div>
        <div class="phones">${(x.phones as string[]).map((p) => `<button class="phone" data-phone="${esc(p)}" title="Скопировать">${esc(p)}</button>`).join("") || '<span class="muted">телефона в реестре нет</span>'}</div>
        ${x.address ? `<div class="muted" style="font-size:13px;margin-top:6px">${esc(x.address)}</div>` : ""}
        <div class="said"><b>${esc(x.status || "—")}</b>${x.agreed ? ` · ${esc(x.agreed)}` : ""}${x.promise ? ` · обещание ${esc(x.promise.date || "")} ${x.promise.sum ? esc(x.promise.sum) : ""}` : ""}${x.reported ? `<br>${esc(x.reported)}` : ""}</div>
        <div class="why">${esc(x.why === "по следам" ? "выбран по признакам:" : x.why)} ${x.why === "по следам" ? flagsHtml(x.flags) : ""}</div></div>
      <div><div class="outcomes">${outcomes.map((o) => `<button class="chip" data-out="${o.key}" aria-pressed="${x.outcome === o.key}">${esc(o.label)}</button>`).join("")}</div>
        <textarea class="input" data-note placeholder="Заметка: что сказал должник" style="width:100%;margin-top:8px;min-height:60px">${esc(x.note)}</textarea>
        ${x.checked_at ? `<div class="muted" style="font-size:12px;margin-top:4px">отмечено ${esc(x.checked_at.replace("T", " ").slice(0, 16))}</div>` : ""}</div>
    </div>`).join("") || '<div class="empty"><b>Звонить некому</b>В этот день выездов не было.</div>'}</div>`;
  wire(root);
  root.querySelectorAll<HTMLButtonElement>("[data-day]").forEach((b) => b.addEventListener("click", () => {
    if (!b.dataset.day) return;
    state.day = b.dataset.day; void renderCalls(root);
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-phone]").forEach((b) => b.addEventListener("click", async () => {
    await hard.copy(b.dataset.phone!); toast("Номер скопирован");
  }));
  root.querySelectorAll<HTMLElement>(".call").forEach((card) => {
    const vid = Number(card.dataset.vid);
    card.querySelectorAll<HTMLButtonElement>("[data-out]").forEach((b) => b.addEventListener("click", async () => {
      const note = card.querySelector<HTMLTextAreaElement>("[data-note]")?.value || "";
      const res = await call("/api/control/callback", undefined, "POST", { visit_id: vid, outcome: b.dataset.out, note });
      if (!res.ok) { toast(res.text || "Не сохранилось"); return; }
      card.querySelectorAll("[data-out]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      card.classList.add("done");
      toast("Записано");
    }));
  });
}

function wire(root: HTMLElement) {
  root.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    state.tab = b.dataset.tab as "risk" | "calls"; state.employee = ""; void draw(root);
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-office]").forEach((b) => b.addEventListener("click", () => {
    state.office = b.dataset.office || ""; void draw(root);
  }));
  wireRange(root, state.range, () => void draw(root));
}

function draw(root: HTMLElement): Promise<void> {
  if (state.tab === "calls") return renderCalls(root);
  if (state.tab === "assign") return renderAssign(root);
  if (state.tab === "access") return renderAccess(root);
  if (state.employee) return renderFlags(root);
  return renderRisk(root);
}

/** Раздел «Контроль». transport — чей токен, excel — есть ли админская выгрузка. */
export function controlSection(transport: ControlCall, excel: boolean): Section {
  call = transport;
  canExcel = excel;
  return {
    id: "control",
    title: "Контроль",
    icon: "control",
    kicker: "статистика признаков — только факты, без процентов",
    async render(root) {
      state.employee = "";
      root.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div>';
      await draw(root);
    },
  };
}

export const control: Section = controlSection((p, q, m, b) => api(p, q, m, b), true);

export { plural };
