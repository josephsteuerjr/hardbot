// Итог выдачи ссылок входа — общий лист для обоих пультов: список «кто — ссылка»,
// скопировать всё разом или скачать книгой Excel.
import { hard } from "./api";
import { esc, toast } from "./dom";

/** Итог выдачи списком: скопировать «ФИО — ссылка» или скачать книгой. */
export async function showIssued(title: string, rows: any[]): Promise<void> {
  const ok = rows.filter((r) => r.url);
  const bad = rows.filter((r) => !r.url);
  const text = ok.map((r) => `${r.employee} — ${r.url}`).join("\n");
  const box = document.createElement("div");
  box.id = "ask";
  box.innerHTML = `<div class="sheet" style="width:min(720px,94vw);max-height:84vh;display:flex;flex-direction:column">
    <h3>${esc(title)}</h3>
    <p>Выдано ${ok.length} из ${rows.length}. Каждая ссылка персональная — пересылайте лично человеку, не в общий чат.</p>
    <div style="overflow:auto;border-top:var(--lw) solid var(--line);border-bottom:var(--lw) solid var(--line);margin-bottom:14px">
      ${rows.map((r) => `<div class="list-row"><div class="grow"><div class="nm">${esc(r.employee)} <span class="sub">${esc(r.who || "")}</span></div>
        ${r.url ? `<div class="url">${esc(r.url)}</div>` : `<div class="sub" style="color:var(--err)">${esc(r.error || "не выдано")}</div>`}</div></div>`).join("")}
    </div>
    ${bad.length ? `<p style="color:var(--err);margin-top:-6px">Не выдано: ${bad.length} — причина в строке.</p>` : ""}
    <div class="row"><button class="btn" data-close>Закрыть</button><button class="btn" data-xlsx>Скачать Excel</button>
      <button class="btn primary" data-copy ${ok.length ? "" : "disabled"}>Скопировать всё</button></div></div>`;
  box.querySelector("[data-close]")!.addEventListener("click", () => box.remove());
  box.querySelector("[data-copy]")!.addEventListener("click", async () => { await hard.copy(text); toast(`Скопировано ссылок: ${ok.length}`); });
  box.querySelector("[data-xlsx]")!.addEventListener("click", async () => {
    const res = await hard.xlsx("Ссылки входа", ["Кто", "Роль", "Ссылка", "Открытий", "Действует до", "Ошибка"],
      rows.map((r) => [r.employee, r.who || "", r.url || "", r.uses || null, r.expires || "", r.error || ""]));
    if (res.ok && res.file) { toast("Книга сохранена"); void hard.open(res.file); }
  });
  document.body.appendChild(box);
}

