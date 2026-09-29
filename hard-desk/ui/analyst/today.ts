// «Сегодня» аналитика: день, а не склад — выезды сегодня/вчера/неделя, столбики,
// кто молчит, что с реестром и обогащением, республики.
import { esc, n } from "../kit/dom";
import type { Section } from "../kit/shell";
import { issueLink } from "./links";
import { DataTable, tupleCols } from "../kit/table";
import { adm, fn, fnMap, guard, note } from "./lib";

export const today: Section = {
  id: "dash",
  title: "Сегодня",
  icon: "today",
  kicker: "день команды целиком",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:60%"></div>';
    const d = await adm("GET", "/admin/today", undefined, 120).catch((e) => { root.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return null; });
    if (!d || !root.isConnected) return;
    const [tiles, TILES, bars, silentNote, regPairs, regNote, cols] = await Promise.all([
      fn("dashboard.tile_values", d), fn("dashboard.TILES"), fn("dashboard.bars", d), fn("dashboard.silent_note", d),
      fn("dashboard.registry_pairs", d), fn("dashboard.registry_note", d), fn("dashboard.SILENT_COLUMNS"),
    ]);
    const silentRows = await fnMap<unknown[]>("dashboard.silent_row", ((d.silent || []) as any[]).map((r) => [r]));
    const countries = await Promise.all(((d.countries || []) as any[]).map(async (c) => ({
      c, pairs: await fn("dashboard.country_today_pairs", c), chips: await fn("dashboard.country_outcome_chips", c) })));
    const max = Math.max(1, ...(bars as any[]).map((b) => b.value));
    const draw = () => {
      root.innerHTML = `
        <div class="kpis">${(TILES as [string, string][]).map(([k, t]) => `<div class="kpi"><b>${esc(tiles[k])}</b><span>${esc(t)}</span></div>`).join("")}</div>
        <div class="days" style="height:84px">${(bars as any[]).map((b, i) => `<div class="day-bar" title="${esc(b.label)}: ${n(b.value)}">
          <i style="height:${Math.max(2, Math.round((b.value / max) * 62))}px;${b.weekend ? "opacity:.45" : ""}${i === bars.length - 1 ? ";background:var(--terra)" : ""}"></i><span>${esc(b.label)}</span></div>`).join("")}</div>
        <div class="section"><div class="section-head"><h2 class="section-title">Сегодня не выехали</h2><span class="section-note">${esc(silentNote)}</span></div>
          <div id="silent"></div>
          <p class="muted" style="font-size:13px">Щелчок по человеку — выдать ему ссылку входа.</p></div>
        <div class="section"><div class="section-head"><h2 class="section-title">Реестр и обогащение</h2></div>
          <div class="kpis">${(regPairs as [string, unknown][]).map(([k, v]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("")}</div>${note(regNote)}</div>
        <div class="section"><div class="section-head"><h2 class="section-title">По республикам</h2></div>
          <div class="grid2">${countries.map(({ c, pairs, chips }) => `<div>
            <h3 class="hand" style="font-size:22px;margin:4px 0 8px;font-weight:400">${esc(c.flag || "")} ${esc(c.name)}</h3>
            <div class="kpis">${(pairs as [string, unknown][]).map(([k, v]) => `<div class="kpi"><b style="font-size:22px">${esc(v)}</b><span>${esc(k)}</span></div>`).join("")}</div>
            <div class="flags" style="margin-top:10px">${(chips as string[]).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div></div>`).join("")}</div></div>`;
      new DataTable<unknown[]>(root.querySelector("#silent")!, {
        id: "analyst-silent", cols: tupleCols(cols, new Set(["portfolio", "idle"])), key: (r) => String(r[0]),
        exportName: "Не выехали сегодня", onRow: guard(async (r: unknown[]) => { await issueLink({ employee: String(r[0]) }); }),
        empty: "Сегодня выехали все, у кого есть портфель.",
      }).setRows(silentRows);
    };
    draw();
  },
};
