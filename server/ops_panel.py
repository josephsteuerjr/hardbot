"""Панель обслуживания для ИТ-инженера.

Отдельный путь ``/ops/*`` и отдельная роль устройства. Панель НЕ ВИДИТ должников
ни при каких обстоятельствах: здесь нет ни одной ручки, которая читает clients,
visits или фотографии. Всё, что она умеет, — закрытый список операций над
сервером, и каждая исполняется помощником на хосте, а не контейнером.

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
    return f"{device.get('name') or 'инженер'} (#{device.get('id')})"


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

    Это мост между двумя целями владельца: раньше код мог выдать только тот, у
    кого SSH. Теперь то же самое делает инженер из панели — и «не лазить по SSH»
    перестаёт противоречить «управляющим становится тот, кто у сервера».
    """
    return web.json_response({"devices": db.list_admin_devices(),
                              "codes": db.list_enroll_codes()})


async def issue_code(request: web.Request, device: dict) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    name = str((body or {}).get("name") or "").strip()
    role = str((body or {}).get("role") or "operator").strip()
    if role != "operator":
        # Инженер заводит ПУЛЬТЫ. Новую панель заводит тот, кто у сервера —
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
    <div class="row">
      <input id="devName" placeholder="Имя пульта, например «Ноут аналитика»" size="34">
      <button id="issue">Выдать код для пульта</button>
    </div>
    <p class="hint">Код одноразовый и показывается один раз. Код для новой
       панели инженера здесь не выдаётся — только командой на сервере.</p>
    <div id="devices" style="margin-top:12px"></div>
  </div>
</main>
<script id="app" data-buttons="__BUTTONS__">
const BUTTONS = JSON.parse(document.getElementById("app").dataset.buttons || "[]");
const $ = s => document.querySelector(s);
let polling = null;

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
  $("#agent").innerHTML = a.alive
    ? '<span class="ok">Помощник на хосте отвечает</span> <span class="pill">на связи ' + a.seen + '</span>'
    : '<span class="bad">Помощник на хосте не отвечает: ' + a.reason + '</span>';
  const inv = s.inventory || {};
  $("#disk").textContent = inv.disk_free ? ("Свободно на диске: " + inv.disk_free) : "";
  const units = (inv.units || []);
  const sel = $("#unit");
  const keep = sel.value;
  sel.innerHTML = units.map(u => '<option value="' + u.unit + '">' + u.unit + '</option>').join("");
  if(keep) sel.value = keep;
  window.__units = units;
  fillBackups();
}

function fillBackups(){
  const unit = $("#unit").value;
  const u = (window.__units || []).find(x => x.unit === unit);
  const list = (u && u.backups) || [];
  $("#backup").innerHTML = list.map(b => '<option>' + b + '</option>').join("");
  $("#backupWrap").hidden = list.length === 0;
}

function renderButtons(){
  $("#buttons").innerHTML = "";
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
  const rows = d.devices.map(x =>
    "<tr><td>" + (x.name || "(без имени)") + "</td><td><span class='pill'>" + x.role +
    "</span></td><td>" + (x.last_seen || "ни разу") + "</td><td>" +
    "<button data-id='" + x.id + "'>Отозвать</button></td></tr>").join("");
  const waiting = d.codes.length
    ? "<p class='hint'>Ждут обмена: " + d.codes.length + "</p>" : "";
  $("#devices").innerHTML =
    "<table><tr><th>Устройство</th><th>Роль</th><th>На связи</th><th></th></tr>" +
    rows + "</table>" + waiting;
  $("#devices").querySelectorAll("button").forEach(b => {
    b.onclick = async () => {
      if(!confirm("Отозвать доступ «" + b.closest("tr").children[0].textContent +
                  "»?\\n\\nЕго токен перестанет работать немедленно.")) return;
      try{ await api("/ops/device/revoke.json", "POST", {id: +b.dataset.id}); renderDevices(); }
      catch(e){ alert(e.message); }
    };
  });
}

$("#issue").onclick = async () => {
  const name = $("#devName").value.trim();
  if(!name){ alert("Назовите пульт, чтобы через месяц понять, что отзываете."); return; }
  try{
    const r = await api("/ops/code.json", "POST", {name, role: "operator"});
    $("#out").textContent =
      "Код привязки для «" + name + "» (показывается один раз):\\n\\n    " + r.code +
      "\\n\\nПередайте его аналитику. В пульте: вкладка «Сервер» → «Привязать по коду».";
    $("#devName").value = "";
    renderDevices();
  }catch(e){ alert(e.message); }
};

$("#unit").onchange = fillBackups;

async function refresh(){
  try{
    const s = await api("/ops/state.json");
    $("#who").textContent = s.you.name ? ("вы: " + s.you.name) : "";
    renderAgent(s);
  }catch(e){ $("#agent").innerHTML = '<span class="bad">' + e.message + '</span>'; }
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
