"""Панель обслуживания сервера.

Отдельный путь ``/ops/*`` и отдельные роли устройства. Панель НЕ ВИДИТ должников
ни при каких обстоятельствах: здесь нет ни одной ручки, которая читает clients,
visits или фотографии. Всё, что она умеет, — закрытый список операций над
сервером, и каждая исполняется помощником на хосте, а не контейнером.

Входят двумя дорогами: ИТ-инженер — веб-страницей ``/ops/`` по коду роли
engineer, аналитик — пультом (роль operator), куда перенесено всё, что
задумывалось «гипотетическому инженеру». Контракт ключей один: код доступа
рождается там, где есть логин к серверу, — командой на сервере или в пульте
аналитика, и только код пульта; код инженера панель не выдаёт никогда.
Инженер кодов не выдаёт вовсе: код пульта открывает должников, и инженер,
выдав его себе, обошёл бы ровно ту границу, ради которой роль отдельная.

Что сюда сознательно НЕ вошло и почему: произвольный SQL (обошёл бы аудит
доступа к карточке), произвольные команды и сборка образов (потребовали бы
докер-сокет, то есть root на всём сервере), просмотр карточек должников (для
этого есть пульт, и там доступ пишется в журнал).
"""

from __future__ import annotations

import html
import json

from aiohttp import web

import db
import ops_requests as ops

# Кнопки панели. Порядок — как в работе: сначала посмотреть, потом трогать.
BUTTONS = (
    {"action": "doctor", "title": "Диагностика", "kind": "safe",
     "hint": "Состояние единицы: контейнеры, здоровье, диск, свежие ошибки."},
    {"action": "logs", "title": "Журнал", "kind": "safe",
     "hint": "Последние строки журнала. Адресов, координат и голоса в нём нет."},
    {"action": "backup", "title": "Снять копию", "kind": "safe",
     "hint": "Копия базы и фотографий. Делайте её перед всем, что меняет данные."},
    {"action": "restart", "title": "Перезапустить", "kind": "care",
     "hint": "Контейнеры единицы поднимутся заново. Данные не трогаются."},
    {"action": "update", "title": "Обновить", "kind": "care",
     "hint": "Копия базы, пересборка, перезапуск. В закрытом контуре сборка пропускается."},
    {"action": "restore", "title": "Восстановить из копии", "kind": "danger",
     "hint": "Текущая база откладывается рядом, на её место встаёт выбранная копия."},
)


def _who(device: dict) -> str:
    role = {"engineer": "инженер", "operator": "аналитик"}.get(
        device.get("role") or "", "оператор")
    return f"{device.get('name') or role} (#{device.get('id')})"


async def state(request: web.Request, device: dict) -> web.Response:
    """Всё, что панель показывает на первом экране, одним запросом."""
    return web.json_response({
        "agent": ops.agent_alive(),
        "inventory": ops.inventory(),
        "pending": [{"id": r.get("id"), "action": r.get("action"),
                     "unit": (r.get("params") or {}).get("unit", ""),
                     "who": r.get("who", ""), "created_at": r.get("created_at", "")}
                    for r in ops.pending()],
        "buttons": list(BUTTONS),
        "you": {"name": device.get("name") or "", "id": device.get("id")},
    })


async def run(request: web.Request, device: dict) -> web.Response:
    """Поставить операцию в очередь помощнику."""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "тело не JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "тело не объект"}, status=400)

    alive = ops.agent_alive()
    if not alive["alive"]:
        # Отказываем СРАЗУ, а не кладём заявку, которую никто не заберёт: иначе
        # инженер смотрит на «выполняется» и ждёт службу, которая не запущена.
        return web.json_response(
            {"error": f"помощник на хосте не отвечает: {alive['reason']}"}, status=409)
    try:
        request_id = ops.submit(str(body.get("action") or ""),
                                body.get("params") or {}, who=_who(device))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    db.audit_access(0, "ops_request", request_id,
                    f"{body.get('action')} · {_who(device)}")
    return web.json_response({"ok": True, "id": request_id})


async def result(request: web.Request, device: dict) -> web.Response:
    got = ops.result(request.rel_url.query.get("id", ""))
    if got is None:
        return web.json_response({"done": False})
    return web.json_response({"done": True, **got})


async def devices(request: web.Request, device: dict) -> web.Response:
    """Список устройств и выдача кода привязки для пульта.

    Это мост между двумя целями владельца: раньше код мог выдать только тот,
    у кого SSH. Теперь то же самое делает аналитик из пульта — и «не лазить по
    SSH» перестаёт противоречить «управляющим становится тот, кто у сервера».
    """
    return web.json_response({"devices": db.list_admin_devices(),
                              "codes": db.list_enroll_codes()})


async def issue_code(request: web.Request, device: dict) -> web.Response:
    if device.get("role") != "operator":
        # Код пульта — это доступ к должникам. Инженер, которому они закрыты,
        # выдал бы его себе и в два запроса стал бы оператором (ревью 25.09).
        return web.json_response(
            {"error": "код выдаёт оператор или команда на сервере"}, status=403)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    name = str(body.get("name") or "").strip()
    role = str(body.get("role") or "operator").strip()
    if role != "operator":
        # Заводятся ПУЛЬТЫ. Новую панель инженера заводит тот, кто у сервера, —
        # иначе один инженер расплодил бы себе панелей, и отзыв потерял бы смысл.
        return web.json_response(
            {"error": "из панели выдаётся код только для пульта; код инженера — "
                      "командой на сервере"}, status=400)
    try:
        made = db.issue_enroll_code(name, "operator")
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    db.audit_access(0, "ops_enroll_code", str(made["id"]), _who(device))
    return web.json_response({"ok": True, **made})


async def revoke(request: web.Request, device: dict) -> web.Response:
    try:
        body = await request.json()
        row_id = int(body.get("id"))
    except (ValueError, TypeError, AttributeError):
        return web.json_response({"error": "нужен id устройства"}, status=400)
    if row_id == device.get("id"):
        # Инженер не отзывает сам себя: это выглядит как «панель сломалась» и
        # лечится только доступом к серверу, которого мы его и лишаем.
        return web.json_response(
            {"error": "нельзя отозвать устройство, из которого вы работаете"}, status=400)
    target = next((d for d in db.list_admin_devices(include_revoked=True)
                   if d["id"] == row_id), None)
    if target is not None and target["role"] != (device.get("role") or "operator"):
        # Оператор и инженер не гасят друг друга: иначе укравший один токен
        # отзывал бы законные устройства другой роли, а своё оставлял. Чужую
        # роль отзывает мастер-ключ (/admin/device/revoke.json) или сервер.
        return web.json_response(
            {"error": "устройство другой роли отзывается мастер-ключом "
                      "или командой на сервере"}, status=403)
    ok = db.revoke_admin_device(row_id)
    if ok:
        db.audit_access(0, "ops_device_revoke", str(row_id), _who(device))
    return web.json_response({"ok": ok})


def page() -> str:
    """Страница панели. Без внешних библиотек: в закрытом контуре их неоткуда взять."""
    buttons = json.dumps(list(BUTTONS), ensure_ascii=False)
    return _PAGE.replace("__BUTTONS__", html.escape(buttons, quote=True))


_PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Хард — обслуживание</title>
<style>
:root{--ink:#1a1c1e;--dim:#5f6368;--line:#e3e5e8;--bg:#f7f8fa;--card:#fff;
      --safe:#1f7a4d;--care:#8a6d1f;--danger:#a03226;--accent:#2f5bd7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:14px 20px;
       display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
h1{font-size:17px;margin:0;font-weight:600}
.who{color:var(--dim);font-size:13px}
main{max-width:1040px;margin:0 auto;padding:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:16px 18px;margin-bottom:16px}
.card h2{font-size:14px;margin:0 0 10px;font-weight:600;color:var(--dim);
         text-transform:uppercase;letter-spacing:.04em}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);
       background:#fff;cursor:pointer}
button:hover{border-color:var(--accent)}
button[disabled]{opacity:.45;cursor:default}
button.safe{border-left:3px solid var(--safe)}
button.care{border-left:3px solid var(--care)}
button.danger{border-left:3px solid var(--danger);color:var(--danger)}
select,input{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px}
pre{background:#0f1115;color:#e6e6e6;padding:14px;border-radius:8px;overflow:auto;
    max-height:460px;font:13px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap}
.bad{color:var(--danger)}
.ok{color:var(--safe)}
.hint{color:var(--dim);font-size:13px;margin:6px 0 0}
table{border-collapse:collapse;width:100%;font-size:14px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:13px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--bg);
      border:1px solid var(--line);font-size:12px;color:var(--dim)}
</style>
<header><h1>Хард — обслуживание сервера</h1><span class="who" id="who"></span>
  <button id="forget" style="margin-left:auto" hidden>Забыть этот браузер</button></header>

<main id="login" hidden>
  <div class="card">
    <h2>Вход</h2>
    <p class="hint" style="margin-top:0">Панель привязывается к браузеру одноразовым кодом.
       Код выдают на сервере командой:</p>
    <pre style="max-height:none">docker compose exec -T app python /app/admin_access.py issue --name "Инженер" --role engineer</pre>
    <div class="row">
      <input id="code" placeholder="Вставьте код" size="40" autocomplete="off">
      <input id="myname" placeholder="Как назвать это место, например «ноут Петра»" size="34">
      <button id="enter" class="safe">Войти</button>
    </div>
    <p class="hint" id="loginErr"></p>
  </div>
</main>

<main id="app-body" hidden>
  <div class="card">
    <h2>Состояние</h2>
    <div id="agent"></div>
    <p class="hint" id="disk"></p>
  </div>

  <div class="card">
    <h2>Операции</h2>
    <div class="row">
      <label>Единица <select id="unit"></select></label>
      <label id="backupWrap" hidden>Копия <select id="backup"></select></label>
    </div>
    <div class="row" id="buttons" style="margin-top:12px"></div>
    <p class="hint" id="hint"></p>
  </div>

  <div class="card">
    <h2>Вывод</h2>
    <pre id="out">Выберите операцию.</pre>
  </div>

  <div class="card">
    <h2>Устройства</h2>
    <p class="hint">Коды привязки здесь не выдаются: код пульта выдаёт аналитик
       из своего пульта, код панели — команда на сервере. Отозвать отсюда можно
       панель инженера; пульт отзывается мастер-ключом или командой на сервере.</p>
    <div id="devices" style="margin-top:12px"></div>
  </div>
</main>
<script id="app" data-buttons="__BUTTONS__">
const BUTTONS = JSON.parse(document.getElementById("app").dataset.buttons || "[]");
const $ = s => document.querySelector(s);
let polling = null;

// Всё, что приходит с сервера, ставится ТОЛЬКО текстом (textContent). Имя
// устройства задаёт тот, кто обменял код, — вставленное разметкой, оно
// исполнилось бы в панели инженера и унесло бы токен из localStorage.
function node(tag, cls, text){
  const e = document.createElement(tag);
  if(cls) e.className = cls;
  if(text != null) e.textContent = String(text);
  return e;
}

// Устройство — это ИМЕННО ЭТОТ браузер. Отпечаток генерируется один раз и живёт
// рядом с токеном: сервер сверяет его на каждом запросе, поэтому увезённый
// токен без него не работает. Чистка данных сайта = «забыли» — нужен новый код,
// и это правильно: чужой браузер доступа не получает.
function deviceId(){
  let id = localStorage.getItem("hard.ops.device");
  if(!id){
    id = "browser-" + (crypto.randomUUID ? crypto.randomUUID()
                       : String(Date.now()) + Math.random().toString(36).slice(2));
    localStorage.setItem("hard.ops.device", id);
  }
  return id;
}
const token = () => localStorage.getItem("hard.ops.token") || "";

async function api(path, method, body){
  const headers = {"X-Device-Token": token(), "X-Device-Id": deviceId()};
  if(body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {
    method: method || "GET", headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if(r.status === 401){ showLogin("Доступ не принят — привяжите этот браузер заново."); 
    throw new Error("нужен код привязки"); }
  const data = await r.json().catch(() => ({}));
  if(!r.ok) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}

function showLogin(message){
  $("#login").hidden = false;
  $("#app-body").hidden = true;
  $("#forget").hidden = true;
  $("#loginErr").textContent = message || "";
}
function showPanel(){
  $("#login").hidden = true;
  $("#app-body").hidden = false;
  $("#forget").hidden = false;
}

$("#enter").onclick = async () => {
  const code = $("#code").value.trim();
  if(!code){ $("#loginErr").textContent = "Вставьте код."; return; }
  try{
    const r = await fetch("/admin/device/redeem.json", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({code, device_id: deviceId(),
                            name: $("#myname").value.trim() || "панель инженера"}),
    });
    const d = await r.json().catch(() => ({}));
    if(!r.ok){ $("#loginErr").textContent = d.error || ("HTTP " + r.status); return; }
    if(d.role !== "engineer"){
      $("#loginErr").textContent = "Это код для пульта, а не для панели. Нужен код с ролью engineer.";
      return;
    }
    localStorage.setItem("hard.ops.token", d.token);
    $("#code").value = "";
    start();
  }catch(e){ $("#loginErr").textContent = e.message; }
};

$("#forget").onclick = () => {
  if(!confirm("Забыть доступ в этом браузере?\\n\\nНа сервере устройство останется " +
              "действующим — чтобы погасить его совсем, отзовите в списке ниже.")) return;
  localStorage.removeItem("hard.ops.token");
  showLogin("Доступ забыт. Вставьте новый код, когда понадобится.");
};

function renderAgent(s){
  const a = s.agent;
  const box = $("#agent");
  box.textContent = "";
  if(a.alive){
    box.appendChild(node("span", "ok", "Помощник на хосте отвечает"));
    box.appendChild(document.createTextNode(" "));
    box.appendChild(node("span", "pill", "на связи " + a.seen));
  }else{
    box.appendChild(node("span", "bad", "Помощник на хосте не отвечает: " + a.reason));
  }
  const inv = s.inventory || {};
  $("#disk").textContent = inv.disk_free ? ("Свободно на диске: " + inv.disk_free) : "";
  const units = (inv.units || []);
  const sel = $("#unit");
  const keep = sel.value;
  sel.textContent = "";
  units.forEach(u => {
    const o = node("option", "", u.unit);
    o.value = u.unit;
    sel.appendChild(o);
  });
  if(keep) sel.value = keep;
  window.__units = units;
  fillBackups();
}

function fillBackups(){
  const unit = $("#unit").value;
  const u = (window.__units || []).find(x => x.unit === unit);
  const list = (u && u.backups) || [];
  const sel = $("#backup");
  sel.textContent = "";
  list.forEach(b => sel.appendChild(node("option", "", b)));
  $("#backupWrap").hidden = list.length === 0;
}

function renderButtons(){
  $("#buttons").textContent = "";
  BUTTONS.forEach(b => {
    const el = document.createElement("button");
    el.textContent = b.title;
    el.className = b.kind;
    el.onmouseenter = () => { $("#hint").textContent = b.hint; };
    el.onclick = () => run(b);
    $("#buttons").appendChild(el);
  });
}

async function run(b){
  const unit = $("#unit").value || "core";
  const params = {unit};
  if(b.action === "restore"){
    const backup = $("#backup").value;
    if(!backup){ $("#out").textContent = "Копий нет — сначала снимите копию."; return; }
    if(!confirm("Восстановить базу единицы «" + unit + "» из копии " + backup +
                "?\\n\\nТекущая база будет отложена рядом, приложение перезапустится."))
      return;
    params.backup = backup;
  }
  if(b.action === "update" && !confirm("Обновить единицу «" + unit + "»?\\n\\nСначала снимется копия базы, потом пересборка и перезапуск."))
    return;
  if(b.action === "logs") params.lines = 300;
  $("#out").textContent = "Отправляю: " + b.title + "…";
  try{
    const {id} = await api("/ops/run.json", "POST", {action: b.action, params});
    poll(id, b.title);
  }catch(e){ $("#out").textContent = "Не вышло: " + e.message; }
}

function poll(id, title){
  clearInterval(polling);
  let waited = 0;
  $("#out").textContent = title + ": выполняется…";
  polling = setInterval(async () => {
    waited += 2;
    try{
      const r = await api("/ops/result.json?id=" + encodeURIComponent(id));
      if(!r.done){
        $("#out").textContent = title + ": выполняется… (" + waited + " с)";
        return;
      }
      clearInterval(polling);
      const head = r.exit === 0 ? "готово" : ("код возврата " + r.exit);
      $("#out").textContent = title + " — " + head + "\\n\\n" + (r.output || "(без вывода)");
      refresh();
    }catch(e){ clearInterval(polling); $("#out").textContent = "Не вышло: " + e.message; }
  }, 2000);
}

async function renderDevices(){
  const d = await api("/ops/devices.json");
  const table = node("table");
  const head = node("tr");
  ["Устройство", "Роль", "На связи", ""].forEach(t => head.appendChild(node("th", "", t)));
  table.appendChild(head);
  d.devices.forEach(x => {
    const name = x.name || "(без имени)";
    const tr = node("tr");
    tr.appendChild(node("td", "", name));
    const role = node("td");
    role.appendChild(node("span", "pill", x.role));
    tr.appendChild(role);
    tr.appendChild(node("td", "", x.last_seen || "ни разу"));
    const cell = node("td");
    // Панель отзывает только панели: пульт аналитика гасится мастер-ключом
    // (сервер откажет и сам, кнопка просто не обещает невозможного).
    if(x.role === "engineer"){
      const b = node("button", "", "Отозвать");
      b.onclick = async () => {
        if(!confirm("Отозвать доступ «" + name +
                    "»?\\n\\nЕго токен перестанет работать немедленно.")) return;
        try{ await api("/ops/device/revoke.json", "POST", {id: x.id}); renderDevices(); }
        catch(e){ alert(e.message); }
      };
      cell.appendChild(b);
    }
    tr.appendChild(cell);
    table.appendChild(tr);
  });
  const box = $("#devices");
  box.textContent = "";
  box.appendChild(table);
  if(d.codes.length) box.appendChild(node("p", "hint", "Ждут обмена: " + d.codes.length));
}

$("#unit").onchange = fillBackups;

async function refresh(){
  try{
    const s = await api("/ops/state.json");
    $("#who").textContent = s.you.name ? ("вы: " + s.you.name) : "";
    renderAgent(s);
  }catch(e){
    const box = $("#agent");
    box.textContent = "";
    box.appendChild(node("span", "bad", e.message));
  }
}

async function start(){
  if(!token()){ showLogin(""); return; }
  try{
    await api("/ops/state.json");   // проверка доступа до показа панели
  }catch(e){ return; }              // 401 уже показал экран входа
  showPanel();
  renderButtons();
  refresh();
  renderDevices();
}

start();
setInterval(() => { if(!$("#app-body").hidden) refresh(); }, 15000);
</script>
</html>"""


# This is deliberately separate from the engineer-only maintenance page above.
# Its HTML carries no account data; the authenticated operator API supplies rows.
def approval_page() -> str:
    """Mobile operator approval form; GET /ops/approve serves only static markup."""
    return _APPROVAL_PAGE


_APPROVAL_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Хард — подтверждение телефона</title>
<style>
:root{color-scheme:light;--ink:#172334;--muted:#526174;--line:#d6dfe9;
  --paper:#fff;--bg:#f2f5f9;--blue:#174cad;--red:#a12332;--amber:#785300}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
 font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{background:#18345d;color:#fff;padding:18px max(18px,env(safe-area-inset-right)) 16px;
 padding-left:max(18px,env(safe-area-inset-left))}
h1{font-size:21px;line-height:1.25;margin:0}header p{margin:5px 0 0;color:#d9e5fa;font-size:14px}
main{max-width:720px;margin:auto;padding:16px max(14px,env(safe-area-inset-right)) 36px;
 padding-left:max(14px,env(safe-area-inset-left))}
.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;
 padding:17px;margin:0 0 14px;box-shadow:0 2px 8px #1a375008}
h2{font-size:18px;margin:0 0 9px}p{margin:8px 0}small,.hint{color:var(--muted);font-size:14px}
label{display:block;font-weight:600;margin:12px 0 5px}
input:not([type=checkbox]),textarea{font:inherit;border:1px solid #a8b5c5;border-radius:9px;
 width:100%;padding:11px;min-height:45px;background:#fff;color:var(--ink)}
textarea{min-height:80px;resize:vertical}input[type=checkbox]{width:22px;height:22px;flex:none}
.check{display:flex;gap:10px;align-items:flex-start;font-weight:500;line-height:1.4}
.check input{margin-top:2px}button{font:inherit;font-weight:600;border:1px solid var(--blue);
 border-radius:10px;background:var(--blue);color:#fff;min-height:46px;padding:9px 15px;cursor:pointer}
button.secondary{background:#fff;color:var(--blue)}button:disabled{opacity:.48;cursor:not-allowed}
button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #82b2f5;outline-offset:2px}
.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px}.actions button{flex:1 1 150px}
.notice{padding:10px 12px;border-radius:9px;background:#e8effa;color:#153766;margin:10px 0}
.warn{background:#fff1d2;color:var(--amber)}.error{background:#ffebef;color:var(--red)}
.good{background:#e5f5ea;color:#205c35}.meta{display:grid;grid-template-columns:minmax(0,1fr);
 gap:5px;overflow-wrap:anywhere}.meta b{font-weight:600}.request{border-top:1px solid var(--line);
 padding-top:12px;margin-top:14px}.request:first-child{border-top:0;margin-top:0;padding-top:0}
code{overflow-wrap:anywhere} [hidden]{display:none!important}
@media(min-width:580px){.meta{grid-template-columns:1fr 1fr}}
</style></head><body>
<header><h1>Подтверждение телефона</h1><p>Оператор · отдельный экран без данных должников</p></header>
<main>
<section class="card" id="login">
 <h2>Привязать этот браузер</h2>
 <p class="hint">Получите одноразовый код устройства оператора у администратора или в действующем пульте.
   Код телефона сотрудника сюда не вводите.</p>
 <label for="enrollCode">Код устройства оператора</label>
 <input id="enrollCode" autocomplete="off" autocapitalize="off" spellcheck="false">
 <label for="deviceName">Как назвать этот браузер</label>
 <input id="deviceName" autocomplete="off" placeholder="Например, телефон администратора">
 <div class="actions"><button id="redeem">Привязать</button></div>
 <p id="loginStatus" class="notice" role="status" aria-live="polite"></p>
</section>
<section id="workspace" hidden>
 <div class="card"><h2>Ваше право подтверждения</h2>
  <p id="principal" class="notice" role="status"></p>
  <p class="hint">Одного названия роли «оператор» недостаточно: сервер проверяет
    зарегистрированного подтверждающего и действующий запрос руководителя.
    Код запроса получите от руководителя <b>по другому каналу</b> и сверьте с ним человека и телефон.</p>
  <div class="actions"><button class="secondary" id="reload">Обновить заявки</button>
    <button class="secondary" id="forget">Забыть токен браузера</button></div>
 </div>
 <div class="card"><h2>Моя геолокация при подтверждении</h2>
  <p class="hint">Сервер запишет мою точку и точность. GPS телефона сотрудника не доказывает,
    что он находится в офисе; регистрация с телефона разрешена вне офиса.</p>
  <p id="myGps" class="notice" role="status" aria-live="polite">Точка ещё не получена.</p>
  <div class="actions"><button id="gpsRetry" class="secondary">Разрешить / повторить GPS</button></div>
  <p class="hint" id="gpsHelp">Если разрешение отклонено: откройте настройки сайта/браузера,
    разрешите геолокацию для этого адреса, вернитесь и нажмите «повторить».
    На iPhone: Настройки → Конфиденциальность и безопасность → Службы геолокации,
    затем доступ Safari к геопозиции и разрешение этого сайта (в меню адресной строки).
    На Android: настройки сайта в Chrome и геолокация в настройках телефона.
    Доступен только в безопасном контексте HTTPS (кроме localhost).</p>
 </div>
 <div class="card"><h2>Заявки на телефоны</h2>
  <p id="listStatus" class="notice" role="status" aria-live="polite">Загрузка…</p>
  <div id="requests"></div>
 </div>
 <section class="card" id="decision" hidden>
  <h2>Решение по одному телефону</h2>
  <p id="selection" class="notice"></p>
  <label for="requestCode">Одноразовый код запроса от руководителя</label>
  <input id="requestCode" autocomplete="off" autocapitalize="off" spellcheck="false">
  <p class="hint">Код вводится только после самостоятельной связи с руководителем по другому каналу.
    Он не отображается в списке заявок и не является паролем сотрудника.</p>
  <label for="reason">Что и как вы проверили (12–500 символов)</label>
  <textarea id="reason" maxlength="500" placeholder="Например, позвонил руководителю по известному номеру и сверил ФИО и устройство"></textarea>
  <div id="exceptionWrap" hidden>
   <p class="notice warn">GPS при регистрации или входе сотрудника отсутствует.
     Исключение будет отмечено на сервере на 24 часа; проверьте причину с руководителем.</p>
   <label for="exceptionReason">Причина исключения без GPS (12–500 символов)</label>
   <textarea id="exceptionReason" maxlength="500"></textarea>
  </div>
  <label class="check"><input type="checkbox" id="attest"><span>Я лично проверил запрос руководителя
    по другому каналу, сверил человека и именно этот телефон. Я понимаю,
    что GPS не доказывает присутствие в офисе.</span></label>
  <div class="actions"><button id="approve" disabled>Подтвердить этот телефон</button></div>
  <p id="decisionStatus" class="notice" role="status" aria-live="polite"></p>
 </section>
</section>
</main>
<script>
(() => {
"use strict";
const $ = id => document.getElementById(id);
const storedToken = () => localStorage.getItem("hard.approval.token") || "";
let principal = "", selected = null, ownGps = null, busy = false;
function status(id, text, type){$(id).textContent = text;$(id).className = "notice " + (type || "");}
function deviceId(){
  let id = localStorage.getItem("hard.ops.device");
  if(!id){id = "browser-" + crypto.randomUUID();localStorage.setItem("hard.ops.device", id);}
  return id;
}
function make(tag, text, className){
  const el = document.createElement(tag);
  if(text != null) el.textContent = String(text);
  if(className) el.className = className;
  return el;
}
function unauthenticated(message){
  principal = "";selected = null;ownGps = null;
  $("workspace").hidden = true;$("login").hidden = false;
  $("decision").hidden = true;
  status("loginStatus", message || "Введите одноразовый код привязки.", message ? "error" : "");
}
async function api(path, method="GET", body){
  const headers = {"X-Device-Token":storedToken(),"X-Device-Id":deviceId()};
  if(body) headers["Content-Type"] = "application/json";
  const response = await fetch(path,{method,headers,body:body ? JSON.stringify(body) : undefined,
    cache:"no-store"});
  const data = await response.json().catch(() => ({}));
  if(response.status === 401){localStorage.removeItem("hard.approval.token");
    unauthenticated("Доступ истёк или устройство отозвано. Получите новый код оператора.");}
  if(!response.ok || data.ok === false) throw new Error(data.error || "HTTP " + response.status);
  return data;
}
function locationText(value, at){
  let p = value;
  if(typeof p === "string") {try{p = JSON.parse(p);}catch{p = null;}}
  if(!p || typeof p !== "object" || p.lat == null || p.lon == null ||
      !Number.isFinite(Number(p.lat)) || !Number.isFinite(Number(p.lon))) return "Нет GPS";
  return `${Number(p.lat).toFixed(5)}, ${Number(p.lon).toFixed(5)} · точность ${
    Number(p.accuracy || 0).toFixed(0)} м · запись ${at || "время не указано"}`;
}
function latestRequest(account, device){
  return (account.phone_requests || []).find(r => Number(r.device_id) === Number(device.id)) || null;
}
function activeRequest(account, device){
  const r = latestRequest(account,device);
  return r && !r.consumed_at && Date.parse(r.expires_at || "") > Date.now() ? r : null;
}
function requestStatus(r){
  if(!r) return "Запрос руководителя ещё не поступил. Попросите руководителя отправить запрос и связаться отдельно.";
  const header = `Запрос руководителя #${r.manager_uid} · ${r.issued_at || "время не указано"} · до ${r.expires_at || "не указано"}. `;
  if(r.consumed_at) return header + `Уже использован ${r.consumed_at}; запросите новый.`;
  if(Date.parse(r.expires_at || "") <= Date.now()) return header + "Истёк; запросите новый.";
  return header + "Действует. Код приходит отдельно, в списке его нет.";
}
function checks(){
  const missing = selected && (!selected.account.registration_gps || !selected.device.gps);
  const exceptionOK = !missing || $("exceptionReason").value.trim().length >= 12;
  $("approve").disabled = busy || !principal || !selected || !selected.request ||
    !ownGps || !$("attest").checked || !$("requestCode").value.trim() ||
    $("reason").value.trim().length < 12 || !exceptionOK;
}
function resetSelection(){
  selected = null;$("decision").hidden = true;$("requestCode").value = "";
  $("reason").value = "";$("exceptionReason").value = "";$("attest").checked = false;
  status("decisionStatus", "");checks();
}
function select(account, device, request){
  resetSelection();
  if(!principal || !request) return;
  selected = {account,device,request};
  $("selection").textContent = `Аккаунт ${account.username} · ${account.employee} · телефон #${device.id} (${device.label || "без названия"}).`;
  $("exceptionWrap").hidden = !!(account.registration_gps && device.gps);
  $("decision").hidden = false;$("decision").scrollIntoView({block:"start",behavior:"smooth"});checks();
}
function line(box, title, text){const p = make("p");p.append(make("b",title+": "),document.createTextNode(String(text)));box.append(p);}
function render(rows){
  resetSelection();const box = $("requests");box.textContent = "";
  let count = 0;
  for(const account of rows){
    for(const device of (account.devices || [])){
      // An already active employee can request a replacement phone. Its new
      // device remains pending until the operator decides on THAT request.
      if(device.kind !== "phone" || device.status !== "pending" ||
         !["awaiting","active"].includes(account.status)) continue;
      count++;
      const request = activeRequest(account,device);
      const receipt = latestRequest(account,device);
      const card = make("article",null,"request");const meta = make("div",null,"meta");
      line(meta,"Сотрудник",account.employee || "не указано");
      line(meta,"Аккаунт",account.username || "не указан");
      line(meta,"Телефон",`#${device.id} · ${device.label || "без названия"}`);
      line(meta,"Регистрация",account.created_at || "время не указано");
      line(meta,"GPS регистрации",locationText(account.registration_gps,account.created_at));
      line(meta,"GPS телефона",locationText(device.gps,device.first_seen));
      card.append(meta);
      const note = make("p",requestStatus(receipt),"notice " + (request ? "" : "warn"));
      card.append(note);
      if(!account.registration_gps || !device.gps)
        card.append(make("p","Внимание: GPS сотрудника отсутствует; потребуется причина исключения. Местонахождение не доказывает офис.","notice warn"));
      if(request && principal){const btn = make("button","Разобрать телефон #"+device.id);
        btn.type = "button";btn.onclick = () => select(account,device,request);card.append(btn);}
      box.append(card);
    }
  }
  status("listStatus",count ? `Ожидают рассмотрения: ${count}. Выберите один телефон.` :
    "Телефонов с ожидающей регистрацией нет.");
}
async function refresh(){
  if(!storedToken()){unauthenticated();return;}
  try{
    const data = await api("/admin/accounts.json");
    if(!$("login").hidden && !storedToken()) return;
    $("login").hidden = true;$("workspace").hidden = false;
    principal = typeof data.approver === "string" ? data.approver.trim() : "";
    status("principal",principal ? `Зарегистрированный подтверждающий: ${principal}` :
      "У этого устройства нет зарегистрированного подтверждающего. Администратор должен назначить его на сервере; подтверждение недоступно.",principal ? "good" : "error");
    render(Array.isArray(data.accounts) ? data.accounts : []);
  }catch(e){
    if(!$("workspace").hidden) status("listStatus","Не удалось обновить: "+e.message,"error");
    else if($("login").hidden) unauthenticated("Не удалось проверить доступ: "+e.message);
    else status("loginStatus","Не удалось проверить доступ: "+e.message,"error");
  }
}
$("redeem").onclick = async () => {
  const code = $("enrollCode").value.trim();
  if(!code){status("loginStatus","Введите код устройства оператора.","error");return;}
  $("redeem").disabled = true;
  try{
    const response = await fetch("/admin/device/redeem.json",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({code,device_id:deviceId(),name:$("deviceName").value.trim() || "телефон администратора"})});
    const data = await response.json().catch(() => ({}));
    if(!response.ok || !data.ok) throw new Error(data.error || "HTTP "+response.status);
    if(data.role !== "operator" || typeof data.token !== "string" || !data.token)
      throw new Error("Нужен код устройства с ролью operator; этот код не подходит.");
    localStorage.setItem("hard.approval.token",data.token);$("enrollCode").value = "";
    await refresh();
  }catch(e){status("loginStatus","Привязка не удалась: "+e.message,"error");}
  finally{$("redeem").disabled = false;}
};
$("forget").onclick = () => {localStorage.removeItem("hard.approval.token");unauthenticated("Токен удалён из этого браузера. На сервере устройство ещё действует; отзовите его отдельно при необходимости.");};
$("reload").onclick = refresh;
$("gpsRetry").onclick = () => {
  ownGps = null;checks();
  if(!navigator.geolocation){status("myGps","Браузер не поддерживает геолокацию. Подтверждение здесь невозможно.","error");return;}
  status("myGps","Запрашиваю местоположение этого телефона…");
  navigator.geolocation.getCurrentPosition(position => {
    ownGps = {lat:position.coords.latitude,lon:position.coords.longitude,
      accuracy:position.coords.accuracy};
    status("myGps",`Точка получена: ${locationText(ownGps,new Date(position.timestamp).toLocaleString("ru-RU"))}. Она будет записана при подтверждении.`,"good");
    checks();
  },error => {
    ownGps = null;
    status("myGps","GPS не получен ("+(error && error.message || "отказ/таймаут")+"). Разрешите геолокацию в настройках сайта и телефона, затем нажмите «повторить». Без моей точки сервер не примет подтверждение.","error");checks();
  },{enableHighAccuracy:true,timeout:15000,maximumAge:0});
};
for(const id of ["requestCode","reason","exceptionReason","attest"]){
  $(id).addEventListener(id === "attest" ? "change" : "input",checks);
}
$("approve").onclick = async () => {
  checks();if($("approve").disabled || !selected) return;
  const target = selected;
  // Never substitute the current selection after a slow network response.
  const body = {device_id:Number(target.device.id),request_code:$("requestCode").value.trim(),
    verified_out_of_band:true,reason:$("reason").value.trim(),gps:ownGps};
  if(!$("exceptionWrap").hidden) body.gps_exception_reason = $("exceptionReason").value.trim();
  busy = true;checks();status("decisionStatus","Передаю решение серверу…");
  try{
    const result = await api("/admin/account_approve.json","POST",body);
    if(result.account_active !== true || result.device_active !== true)
      status("decisionStatus","Сервер принял шаг, но аккаунт или телефон ещё не активен. Проверьте журнал и повторно обновите список; не считайте это успешным входом.","warn");
    else status("decisionStatus",result.gps_exception_pending ?
      "Подтверждено сервером с временным исключением без GPS (24 часа)." :
      "Сервер подтвердил аккаунт и именно этот телефон.","good");
    // Do not retain the manager's one-use code in a filled field.
    $("requestCode").value = "";$("attest").checked = false;
    selected = null;
    // Preserve the result until the next deliberate refresh.
  }catch(e){status("decisionStatus","Сервер отказал: "+e.message+". Ничего не подтверждено на этом экране.","error");}
  finally{busy = false;checks();}
};
refresh();
})();
</script></body></html>"""
