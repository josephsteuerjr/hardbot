//! Местный сервер пульта: страница и весь локальный API. Браузер ходит
//! ТОЛЬКО сюда, на 127.0.0.1; наружу — в «Хард» — ходит этот процесс, и
//! только он знает токен. Страница та же, что у питон-версии (`pult_ruk/web`),
//! поэтому контракт совпадает буква в букву.
//!
//! Форма ответов: всё — JSON, беда — `{"error": "по-русски"}` с кодом 200
//! (текст беды и есть содержимое экрана); кодами остаются только «сюда нельзя»
//! (401/403) и «такого нет» (404).

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use axum::body::Body;
use axum::extract::{ConnectInfo, Request, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine;
use serde_json::{json, Value};

use crate::hard::Hard;
use crate::session::{cookie_value, Gate, COOKIE};
use crate::settings::Settings;
use crate::tunnel::{Tunnel, TunnelTrouble};

/// Страница — та же, что у питон-пульта: один источник разметки на оба пульта.
#[derive(rust_embed::RustEmbed)]
#[folder = "../pult_ruk/web/"]
struct WebAssets;

const SERVABLE: &[(&str, &str)] = &[
    ("html", "text/html; charset=utf-8"),
    ("css", "text/css; charset=utf-8"),
    ("js", "text/javascript; charset=utf-8"),
    ("mjs", "text/javascript; charset=utf-8"),
    ("svg", "image/svg+xml"),
    ("png", "image/png"),
    ("webmanifest", "application/manifest+json"),
    ("woff2", "font/woff2"),
];

const NEED_PIN: &str = "Сначала введите код-пропуск — его показывает пульт на компьютере.";
const ONLY_PC: &str = "Это делается на компьютере, где открыт пульт: с телефона доступ \
     и ссылку входа не меняют.";

#[derive(Clone)]
pub struct AppState {
    pub settings: Arc<Mutex<Settings>>,
    pub hard: Arc<tokio::sync::Mutex<Hard>>,
    pub tunnel: Tunnel,
    pub gate: Arc<Mutex<Gate>>,
    pub port: u16,
    pub log: Arc<dyn Fn(&str) + Send + Sync>,
}

/// Кто пришёл: решает middleware, обработчику остаётся дело.
#[derive(Clone, Default)]
pub struct Who {
    pub console: bool,
    pub has_session: bool,
}

fn ok_json(payload: Value) -> Response {
    let mut resp = Json(payload).into_response();
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    resp
}

fn err_json(payload: Value, status: StatusCode) -> Response {
    let mut resp = (status, Json(payload)).into_response();
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    resp
}

/// Один вход для всех проверок: чужое имя, чужой сайт, чужой телефон.
/// Проверка доступа, размазанная по десяти обработчикам, однажды забывается
/// в одиннадцатом — и именно он отдаёт наружу портфель направления.
async fn guard(State(app): State<AppState>, mut req: Request, next: Next) -> Response {
    let path = req.uri().path().to_string();
    let method = req.method().clone();
    let headers = req.headers().clone();
    let remote = req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| c.0.ip().to_string())
        .unwrap_or_default();
    let host = headers
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let cookie = cookie_value(&headers, COOKIE);
    let (console, known_host, has_session) = {
        let gate = app.gate.lock().expect("gate");
        (
            gate.is_console(&host, &remote, &headers),
            gate.known_host(&host),
            gate.has_session(&cookie),
        )
    };
    if !known_host {
        return err_json(
            json!({"error": "Пульт открыт не по своему адресу."}),
            StatusCode::FORBIDDEN,
        );
    }
    if method == axum::http::Method::POST {
        let origin = headers
            .get(header::ORIGIN)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        let mine = [format!("http://{}", host), format!("https://{}", host)];
        if !origin.is_empty() && !mine.contains(&origin.to_string()) {
            return err_json(
                json!({"error": "Запрос пришёл с чужой страницы."}),
                StatusCode::FORBIDDEN,
            );
        }
    }
    let open = path == "/api/state" || path == "/api/pin" || !path.starts_with("/api/");
    if !open && !(console || has_session) {
        return err_json(
            json!({"error": NEED_PIN, "need_pin": true}),
            StatusCode::UNAUTHORIZED,
        );
    }
    let console_only =
        matches!(path.as_str(), "/api/link" | "/api/out" | "/api/phone" | "/api/server");
    if console_only && !console {
        return err_json(json!({"error": ONLY_PC}), StatusCode::FORBIDDEN);
    }
    req.extensions_mut().insert(Who { console, has_session });
    next.run(req).await
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(page))
        .route("/api/state", get(api_state))
        .route("/api/link", post(api_link))
        .route("/api/pin", post(api_pin))
        .route("/api/server", post(api_server))
        .route("/api/out", post(api_out))
        .route("/api/today", get(api_today))
        .route("/api/employee", get(api_employee))
        .route("/api/promises", get(api_promises))
        .route("/api/stats", get(api_stats))
        .route("/api/report.xlsx", get(api_report))
        .route("/api/pushes", get(api_pushes).post(api_pushes))
        .route("/api/team_links", get(api_team_links))
        .route("/api/team_link", post(api_team_link))
        .route("/api/phone", post(api_phone))
        .route("/{name}", get(static_file))
        .with_state(state.clone())
        .layer(middleware::from_fn_with_state(state, guard))
}

// --- страница ----------------------------------------------------------------
async fn page() -> Response {
    serve_asset("index.html")
}

async fn static_file(axum::extract::Path(name): axum::extract::Path<String>) -> Response {
    serve_asset(&name)
}

fn serve_asset(name: &str) -> Response {
    // Имя берём только последней частью: «../» — попытка читать чужие файлы.
    let clean = name.rsplit(['/', '\\']).next().unwrap_or("");
    let ext = clean.rsplit('.').next().unwrap_or("");
    let kind = SERVABLE
        .iter()
        .find(|(e, _)| *e == ext)
        .map(|(_, kind)| kind.to_string());
    match (WebAssets::get(clean), kind) {
        (Some(asset), Some(kind)) => {
            let mut resp = Response::new(Body::from(asset.data.into_owned()));
            resp.headers_mut()
                .insert(header::CONTENT_TYPE, kind.parse().unwrap());
            // Страница меняется вместе с пультом; телефон, закэшировавший
            // старую, выглядит как сломанный пульт.
            resp.headers_mut()
                .insert(header::CACHE_CONTROL, "no-cache".parse().unwrap());
            resp
        }
        _ => err_json(
            json!({"error": format!(
                "Файл «{}» пульту не найден. Похоже, сборка неполна.", clean)}),
            StatusCode::NOT_FOUND,
        ),
    }
}

// --- состояние ---------------------------------------------------------------
fn qr_data_url(url: &str) -> Option<String> {
    if url.is_empty() {
        return None;
    }
    let code = qrcode::QrCode::new(url.as_bytes()).ok()?;
    let svg = code
        .render::<qrcode::render::svg::Color>()
        .dark_color(qrcode::render::svg::Color("#101418"))
        .light_color(qrcode::render::svg::Color("#ffffff"))
        .min_dimensions(200, 200)
        .build();
    let b64 = base64::engine::general_purpose::STANDARD.encode(svg.as_bytes());
    Some(format!("data:image/svg+xml;base64,{}", b64))
}

async fn api_state(State(app): State<AppState>, headers: HeaderMap,
                   axum::Extension(w): axum::Extension<Who>) -> Response {
    let hard = app.hard.lock().await;
    ok_json(state_payload(&app, &hard, &headers, &w))
}

fn state_payload(app: &AppState, hard: &Hard, headers: &HeaderMap, w: &Who) -> Value {
    let gate = app.gate.lock().expect("gate");
    let cookie = cookie_value(headers, COOKIE);
    if !w.console && !gate.has_session(&cookie) {
        // Телефону до кода-пропуска не показываем ничего: ни имени, ни адреса.
        return json!({
            "ready": hard.is_ready(), "need_pin": true,
            "manager": {"name": "", "scope": ""}, "hard_url": "", "servers": [],
            "phone": {"on": true, "url": "", "pin": "", "qr": null},
        });
    }
    let phone = app.tunnel.state();
    let (name, scope) = hard.manager();
    let mut servers: Vec<Value> = Vec::new();
    let base = hard.base();
    if w.console {
        let settings = app.settings.lock().expect("настройки");
        for (url, ready) in settings.servers() {
            let host = url
                .splitn(2, "://")
                .nth(1)
                .map(|rest| rest.split('/').next().unwrap_or(rest).to_string())
                .unwrap_or_else(|| url.clone());
            servers.push(json!({"url": url, "host": host, "ready": ready}));
        }
        servers.sort_by_key(|s| (
            s["url"].as_str() != Some(base.as_str()),
            s["host"].as_str().unwrap_or("").to_string(),
        ));
    }
    json!({
        "ready": hard.is_ready(),
        "need_pin": false,
        "manager": {"name": name, "scope": scope},
        "hard_url": hard.base(),
        "lost_at": hard.lost_at(),
        "servers": servers,
        "phone": {
            "on": phone.on,
            "url": phone.url,
            "pin": if w.console { gate.pin.clone() } else { String::new() },
            "qr": if w.console { qr_data_url(&phone.url) } else { None },
            "ready": phone.ready,
            "error": phone.error,
            "alarm": gate.alarm,
        },
    })
}

// --- вход, переключение, выход ------------------------------------------------
async fn read_json(req: Request) -> Value {
    let bytes = axum::body::to_bytes(req.into_body(), 1 << 20)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&bytes).unwrap_or(Value::Null)
}

async fn api_link(State(app): State<AppState>, req: Request) -> Response {
    let payload = read_json(req).await;
    let raw = payload
        .get("link")
        .or_else(|| payload.get("code"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let mut hard = app.hard.lock().await;
    match hard.redeem(&raw).await {
        Ok(out) => {
            (app.log)(&format!(
                "вход по ссылке принят, доступ на {} суток",
                out.get("days").map(|d| d.to_string()).unwrap_or_default()));
            ok_json(out)
        }
        Err(trouble) => ok_json(json!({
            "error": trouble.text, "kind": trouble.kind, "stale": false})),
    }
}

async fn api_server(State(app): State<AppState>, req: Request) -> Response {
    let url = read_json(req)
        .await
        .get("url")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim_end_matches('/')
        .to_string();
    let mut hard = app.hard.lock().await;
    match hard.switch(&url) {
        Ok(()) => {
            (app.log)(&format!("переключение на сервер {}", url));
            ok_json(json!({"ok": true, "hard_url": hard.base()}))
        }
        Err(trouble) => ok_json(json!({
            "error": trouble.text, "kind": trouble.kind, "stale": false})),
    }
}

async fn api_pin(State(app): State<AppState>, _headers: HeaderMap,
                 axum::Extension(w): axum::Extension<Who>, req: Request) -> Response {
    if w.console {
        return ok_json(json!({"ok": true}));
    }
    let given = read_json(req)
        .await
        .get("pin")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let (ok, text, key, locked) = {
        let mut gate = app.gate.lock().expect("gate");
        let (ok, text, key) = gate.check(&given);
        (ok, text, key, gate.locked)
    };
    if !ok {
        if locked {
            // Пять промахов — тоннель гасится по-настоящему, как и обещает
            // тревога «Тоннель погашен»: публичный адрес перестаёт отвечать.
            app.tunnel.stop().await;
        }
        return ok_json(json!({"ok": false, "error": text}));
    }
    let mut resp = ok_json(json!({"ok": true}));
    // HttpOnly — ключ сессии не достаётся скрипту на странице; Secure — снаружи
    // нас открывают только по https через тоннель; Lax — чужая страница не
    // ходит сюда от имени телефона. Срок не ставим: сессия живёт, пока жив пульт.
    resp.headers_mut().append(
        header::SET_COOKIE,
        format!("{}={}; HttpOnly; Secure; SameSite=Lax; Path=/", COOKIE, key)
            .parse()
            .unwrap(),
    );
    resp
}

async fn api_out(State(app): State<AppState>) -> Response {
    {
        let mut hard = app.hard.lock().await;
        hard.forget().await;
    }
    app.tunnel.stop().await;
    app.gate.lock().expect("gate").close();
    let mut resp = ok_json(json!({"ok": true}));
    resp.headers_mut().append(
        header::SET_COOKIE,
        format!("{}=; Max-Age=0; Path=/", COOKIE).parse().unwrap(),
    );
    resp
}

// --- экраны ------------------------------------------------------------------
fn date_param(query: &HashMap<String, String>, name: &str) -> Result<String, Response> {
    let raw = query.get(name).map(|s| s.trim()).unwrap_or("");
    if raw.is_empty() {
        return Ok(String::new());
    }
    let bytes = raw.as_bytes();
    let ok = bytes.len() == 10
        && bytes[4] == b'-'
        && bytes[7] == b'-'
        && raw[..4].chars().all(|c| c.is_ascii_digit())
        && raw[5..7].chars().all(|c| c.is_ascii_digit())
        && raw[8..].chars().all(|c| c.is_ascii_digit());
    if ok {
        Ok(raw.to_string())
    } else {
        Err(err_json(
            json!({"error": "Дата должна быть вида 2026-09-21."}),
            StatusCode::BAD_REQUEST,
        ))
    }
}

fn range_of(query: &HashMap<String, String>) -> Result<Vec<(&'static str, String)>, Response> {
    let mut out: Vec<(&'static str, String)> =
        vec![("from", date_param(query, "from")?), ("to", date_param(query, "to")?)];
    if let Some(preset) = query.get("preset").map(|s| s.trim()) {
        if ["today", "yesterday", "7d", "30d", "month", "custom"].contains(&preset) {
            out.push(("preset", preset.to_string()));
        }
    }
    if let Some(shift) = query.get("shift").map(|s| s.trim()) {
        let digits = shift.strip_prefix('-').unwrap_or(shift);
        if !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) {
            out.push(("shift", shift.to_string()));
        }
    }
    Ok(out)
}

async fn api_today(State(app): State<AppState>,
                   axum::extract::Query(query): axum::extract::Query<HashMap<String, String>>) -> Response {
    let params = vec![("date", date_param(&query, "date"))];
    let params = match params_into(params) {
        Ok(p) => p,
        Err(resp) => return resp,
    };
    let hard = app.hard.lock().await;
    screen(hard, "today", &params).await
}

async fn api_promises(State(app): State<AppState>,
                      axum::extract::Query(query): axum::extract::Query<HashMap<String, String>>) -> Response {
    let params = vec![("date", date_param(&query, "date"))];
    let params = match params_into(params) {
        Ok(p) => p,
        Err(resp) => return resp,
    };
    let hard = app.hard.lock().await;
    screen(hard, "promises", &params).await
}

async fn api_stats(State(app): State<AppState>,
                   axum::extract::Query(query): axum::extract::Query<HashMap<String, String>>) -> Response {
    let mut params: Vec<(&'static str, Result<String, Response>)> =
        vec![("date", date_param(&query, "date"))];
    let extra = match range_of(&query) {
        Ok(extra) => extra,
        Err(resp) => return resp,
    };
    for (key, value) in extra {
        params.push((key, Ok(value)));
    }
    let params = match params_into(params) {
        Ok(p) => p,
        Err(resp) => return resp,
    };
    let hard = app.hard.lock().await;
    screen(hard, "stats", &params).await
}

async fn api_employee(State(app): State<AppState>,
                      axum::extract::Query(query): axum::extract::Query<HashMap<String, String>>) -> Response {
    let emp = query
        .get("emp")
        .or_else(|| query.get("employee"))
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if emp.is_empty() {
        return ok_json(json!({"error": "Не сказано, чью карточку открыть."}));
    }
    let date = match date_param(&query, "date") {
        Ok(d) => d,
        Err(resp) => return resp,
    };
    let emp_cut: String = emp.chars().take(200).collect();
    let params = vec![("employee", Ok(emp_cut)), ("date", Ok(date))];
    let params = match params_into(params) {
        Ok(p) => p,
        Err(resp) => return resp,
    };
    let hard = app.hard.lock().await;
    screen(hard, "employee", &params).await
}

fn params_into(pairs: Vec<(&'static str, Result<String, Response>)>) -> Result<Vec<(&'static str, String)>, Response> {
    let mut out = Vec::new();
    for (key, value) in pairs {
        out.push((key, value?));
    }
    Ok(out)
}

async fn screen(hard: tokio::sync::MutexGuard<'_, Hard>, name: &str,
                params: &[(&'static str, String)]) -> Response {
    match hard.call(name, params).await {
        Ok(data) => ok_json(data),
        Err(trouble) => ok_json(json!({
            "error": trouble.text, "kind": trouble.kind, "stale": false})),
    }
}

async fn api_report(State(app): State<AppState>,
                    axum::extract::Query(query): axum::extract::Query<HashMap<String, String>>) -> Response {
    let params = match range_of(&query) {
        Ok(p) => p,
        Err(resp) => return resp,
    };
    let hard = app.hard.lock().await;
    match hard.book(&params).await {
        Ok(upstream) => {
            let name = "отчёт.xlsx";
            let mut resp = Response::new(Body::from_stream(upstream.bytes_stream()));
            let headers = resp.headers_mut();
            headers.insert(
                header::CONTENT_TYPE,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    .parse()
                    .unwrap(),
            );
            headers.insert(
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"{}\"", name).parse().unwrap(),
            );
            headers.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
            resp
        }
        Err(trouble) => ok_json(json!({
            "error": trouble.text, "kind": trouble.kind})),
    }
}

// --- рубильники и входы команды ----------------------------------------------
async fn api_pushes(State(app): State<AppState>, req: Request) -> Response {
    let hard = app.hard.lock().await;
    let out = if req.method() == axum::http::Method::POST {
        let body = read_json(req).await;
        let key = body.get("key").and_then(Value::as_str).unwrap_or("");
        let value = body.get("value").and_then(Value::as_bool);
        let who = {
            let name = hard.manager().0;
            if name.is_empty() { "?".to_string() } else { name }
        };
        match value {
            Some(v)
                if ["push_visits", "push_digest", "push_promises"].contains(&key) =>
            {
                (app.log)(&format!("рубильник {} → {} (кем: {})", key, v, who));
                hard.set_push(key, v).await
            }
            _ => {
                return err_json(json!({"error": "Неизвестный рубильник."}),
                                StatusCode::BAD_REQUEST)
            }
        }
    } else {
        hard.pushes().await
    };
    match out {
        Ok(data) => ok_json(data),
        Err(trouble) => ok_json(json!({
            "error": trouble.text, "kind": trouble.kind, "stale": false})),
    }
}

async fn api_team_links(State(app): State<AppState>) -> Response {
    let hard = app.hard.lock().await;
    match hard.team_links().await {
        Ok(data) => ok_json(data),
        Err(trouble) => ok_json(json!({
            "error": trouble.text, "kind": trouble.kind, "stale": false})),
    }
}

async fn api_team_link(State(app): State<AppState>, req: Request) -> Response {
    let body = read_json(req).await;
    let employee = body
        .get("employee")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let revoke = body.get("action").and_then(Value::as_str) == Some("revoke");
    if employee.is_empty() {
        return err_json(json!({"error": "Не сказано, кому выдать вход."}),
                        StatusCode::BAD_REQUEST);
    }
    let hard = app.hard.lock().await;
    let who = hard.manager().0;
    let who = if who.is_empty() { "?".to_string() } else { who };
    let verb = if revoke { "отозван" } else { "выдан" };
    (app.log)(&format!("{} вход: {} (кем: {})", verb, employee, who));
    match hard.team_link(&employee, revoke).await {
        Ok(mut data) => {
            // Выдача возвращается с QR картинкой: руководитель пересылает
            // ссылку человеку сам — одним движением.
            if !revoke {
                if let Some(url) = data.get("url").and_then(Value::as_str) {
                    if let Some(qr) = qr_data_url(url) {
                        if let Some(map) = data.as_object_mut() {
                            map.insert("qr".into(), Value::String(qr));
                        }
                    }
                }
            }
            ok_json(data)
        }
        Err(trouble) => ok_json(json!({
            "error": trouble.text, "kind": trouble.kind, "stale": false})),
    }
}

// --- телефон -----------------------------------------------------------------
async fn api_phone(State(app): State<AppState>, req: Request) -> Response {
    let want = read_json(req)
        .await
        .get("on")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    if !want {
        app.tunnel.stop().await;
        app.gate.lock().expect("gate").close();
        return ok_json(json!({
            "on": false, "url": "", "pin": "", "qr": null,
            "ready": app.tunnel.available(),
        }));
    }
    app.gate.lock().expect("gate").reopen();
    match app.tunnel.start(app.port).await {
        Ok(url) => {
            let host = url.split("//").nth(1).unwrap_or("").to_string();
            app.gate.lock().expect("gate").tunnel_host = host;
            let pin = app.gate.lock().expect("gate").pin.clone();
            (app.log)(&format!("телефон включён: {}", url));
            ok_json(json!({
                "on": true, "url": url, "pin": pin,
                "qr": qr_data_url(&url), "ready": true, "error": "",
            }))
        }
        Err(TunnelTrouble(text)) => ok_json(json!({
            "on": false, "url": "", "pin": "", "qr": null,
            "ready": app.tunnel.available(), "error": text,
        })),
    }
}
