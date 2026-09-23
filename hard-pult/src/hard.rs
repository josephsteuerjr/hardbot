//! Клиент сервера «Хард». Руководитель не инженер: любая беда превращается
//! в русскую строку, которую можно прочитать вслух аналитику по телефону.
//! Три случая лечатся по-разному: нет связи / токен протух / сервер ошибся.
//!
//! Последний удачный ответ каждой ручки лежит в памяти: пропала связь — экран
//! показывает вчерашнюю правду и подписывает, когда связь потерялась. Пустой
//! экран в такой момент хуже устаревшего.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::settings::Settings;

pub const PATHS: &[(&str, &str)] = &[
    ("today", "/api/manager/summary"),
    ("employee", "/api/manager/employee"),
    ("promises", "/api/manager/promises"),
    ("stats", "/api/manager/stats"),
    ("report", "/api/manager/stats.xlsx"),
];

pub const NO_LINK: &str = "Пульт ещё не знает, кто вы. Вставьте ссылку входа — ту, \
     что выдал аналитик.";
pub const OFFLINE: &str = "Нет связи с сервером «Хард». Проверьте интернет — как только \
     он появится, пульт догонит сам.";
pub const CERT_BAD: &str = "Сертификат сервера «Хард» не прошёл проверку — эта сеть \
     подменяет соединение (гостиничный или чужой Wi-Fi). Пульт ничего не отправил. \
     Подключитесь к другой сети.";

/// Сетевая беда словами. Сертификат не прошёл проверку — повтора «без проверки»
/// нет и не будет: такой повтор отдал бы токен руководителя тому, кто подменил
/// связь. Сервер живёт под настоящим сертификатом (Let's Encrypt).
fn net_trouble(err: &reqwest::Error) -> Trouble {
    let mut text = err.to_string();
    let mut source = std::error::Error::source(err);
    while let Some(inner) = source {
        text.push(' ');
        text.push_str(&inner.to_string());
        source = inner.source();
    }
    let text = text.to_lowercase();
    if text.contains("cert") || text.contains("сертификат") {
        Trouble::new("offline", CERT_BAD)
    } else {
        Trouble::new("offline", OFFLINE)
    }
}

pub const DEAD_TOKEN: &str = "Ссылка входа больше не действует. Попросите у аналитика \
     новую и вставьте её здесь.";

/// Беда, уже переведённая на русский. `kind` — что делать пульту.
#[derive(Debug, Clone)]
pub struct Trouble {
    pub kind: &'static str, // no_link | offline | auth | server
    pub text: String,
    pub status: u16,
}

impl Trouble {
    fn new(kind: &'static str, text: impl Into<String>) -> Trouble {
        Trouble { kind, text: text.into(), status: 0 }
    }
    fn status(kind: &'static str, text: impl Into<String>, status: u16) -> Trouble {
        Trouble { kind, text: text.into(), status }
    }
}


pub fn now_hhmm() -> String {
    // Местное время без зависимости от chrono: подпись «связь потеряна в 14:32»
    // нужна, чтобы отличать свежую беду от старой, а не сверять часы.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let hh = (secs / 3600) % 24;
    let mm = (secs / 60) % 60;
    format!("{:02}:{:02}", hh, mm)
}

/// Ссылка входа → (адрес сервера, код). Человек вставляет что придётся:
/// полную ссылку, ссылку с решёткой из старой рассылки или один код.
pub fn split_link(raw: &str) -> Result<(String, String), Trouble> {
    let text = raw.trim().trim_matches(|c| c == '<' || c == '>')
        .trim_matches(|c| c == '"' || c == '\'')
        .trim();
    if text.is_empty() {
        return Err(Trouble::new("no_link", "Ссылка пустая — вставьте её целиком."));
    }
    let lower = text.to_ascii_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") {
        let after_scheme = text
            .splitn(2, "://")
            .nth(1)
            .unwrap_or("");
        let netloc = after_scheme
            .split(|c: char| c == '/' || c == '?' || c == '#')
            .next()
            .unwrap_or("");
        if netloc.is_empty() {
            return Err(Trouble::new(
                "no_link",
                "В этой ссылке нет адреса сервера — вставьте её целиком.",
            ));
        }
        let sep = text.find("://").unwrap_or(0);
        let base = format!("{}://{}", &text[..sep], netloc);
        let mut code = String::new();
        for sep in ["/i/", "/#link=", "/#t="] {
            if let Some(idx) = text.find(sep) {
                if idx > 0 {
                    code = text[idx + sep.len()..].to_string();
                    break;
                }
            }
        }
        if code.is_empty() {
            if let Some(frag_pos) = text.find('#') {
                let frag = &text[frag_pos + 1..];
                for prefix in ["link=", "t="] {
                    if let Some(rest) = frag.strip_prefix(prefix) {
                        code = rest.to_string();
                        break;
                    }
                }
            }
        }
        for cut in ['&', '#', '?'] {
            if let Some(idx) = code.find(cut) {
                code.truncate(idx);
            }
        }
        while code.starts_with('/') {
            code.remove(0);
        }
        if valid_code(&code) {
            return Ok((base, code));
        }
        return Err(Trouble::new(
            "no_link",
            "В этой ссылке нет кода входа. Нужна та, что заканчивается на «/i/…» — \
             попросите её у аналитика.",
        ));
    }
    if valid_code(text) {
        return Ok((String::new(), text.to_string()));
    }
    Err(Trouble::new(
        "no_link",
        "Это не похоже на ссылку входа. Вставьте её целиком, вместе с «https://».",
    ))
}

fn valid_code(code: &str) -> bool {
    (4..=64).contains(&code.len())
        && code
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

pub struct Hard {
    pub settings: Arc<Mutex<Settings>>,
    client: reqwest::Client,
    cache: Mutex<HashMap<String, (Value, String)>>,
    lost_at: Mutex<Option<String>>,
    dead: Mutex<bool>,
    manager: Mutex<(String, String)>,
}

impl Hard {
    pub fn new(settings: Arc<Mutex<Settings>>) -> Hard {
        let (name, scope) = settings.lock().expect("настройки").manager();
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(25))
            // Без системных прокси — как у питон-версии: корпоративный
            // прокси не знает про наши адреса и рвёт связь с сервером.
            .no_proxy()
            .build()
            .expect("http-клиент");
        Hard {
            settings,
            client,
            cache: Mutex::new(HashMap::new()),
            lost_at: Mutex::new(None),
            dead: Mutex::new(false),
            manager: Mutex::new((name, scope)),
        }
    }

    pub fn base(&self) -> String {
        self.settings.lock().expect("настройки").url()
    }

    fn ready(&self) -> bool {
        !self.base().is_empty()
            && !self
                .settings
                .lock()
                .expect("настройки")
                .token()
                .is_empty()
            && !*self.dead.lock().expect("dead")
    }

    pub fn manager(&self) -> (String, String) {
        self.manager.lock().expect("manager").clone()
    }

    pub fn lost_at(&self) -> String {
        self.lost_at.lock().expect("lost_at").clone().unwrap_or_default()
    }

    pub fn is_ready(&self) -> bool {
        self.ready()
    }

    pub async fn forget(&mut self) {
        self.settings.lock().expect("настройки").forget();
        self.settings.lock().expect("настройки").save();
        self.cache.lock().expect("кэш").clear();
        *self.lost_at.lock().expect("lost_at") = None;
        *self.dead.lock().expect("dead") = false;
        *self.manager.lock().expect("manager") = (String::new(), String::new());
    }

    /// Переключиться на известный сервер; новый заводит только ссылка.
    pub fn switch(&mut self, url: &str) -> Result<(), Trouble> {
        let mut st = self.settings.lock().expect("настройки");
        if !st.knows(url) {
            return Err(Trouble::new(
                "no_link",
                "Этот сервер пульту не знаком — войдите в него ссылкой входа.",
            ));
        }
        st.set_url(url);
        st.save();
        drop(st);
        self.cache.lock().expect("кэш").clear();
        *self.lost_at.lock().expect("lost_at") = None;
        *self.dead.lock().expect("dead") = false;
        let (name, scope) = self
            .settings
            .lock()
            .expect("настройки")
            .manager();
        *self.manager.lock().expect("manager") = (name, scope);
        Ok(())
    }

    async fn request(&self, method: reqwest::Method, url: &str, body: Option<Value>,
                     auth: bool, timeout: Option<Duration>) -> Result<Value, Trouble> {
        let token = self.settings.lock().expect("настройки").token();
        if auth && token.is_empty() {
            return Err(Trouble::new("no_link", NO_LINK));
        }
        let send = |client: &reqwest::Client| {
            let mut req = client.request(method.clone(), url.to_string());
            req = req.timeout(timeout.unwrap_or(Duration::from_secs(25)));
            if auth {
                req = req.header("X-App-Token", &token);
            }
            if let Some(payload) = &body {
                req = req.json(payload);
            }
            req
        };
        let resp = send(&self.client)
            .send()
            .await
            .map_err(|err| net_trouble(&err))?;
        let status = resp.status().as_u16();
        if status >= 400 {
            let text = resp.text().await.unwrap_or_default();
            return Err(trouble_of(status, &text));
        }
        resp.json::<Value>()
            .await
            .map_err(|_| {
                Trouble::new(
                    "server",
                    "Сервер «Хард» ответил не по-нашему — возможно, вместо него \
                     отвечает чужая страница входа. Проверьте адрес.",
                )
            })
    }

    pub async fn redeem(&mut self, raw: &str) -> Result<Value, Trouble> {
        let (link_base, code) = split_link(raw)?;
        let base = if link_base.is_empty() { self.base() } else { link_base };
        if base.is_empty() {
            return Err(Trouble::new(
                "no_link",
                "Пульт не знает адреса сервера. Вставьте ссылку целиком, \
                 вместе с «https://».",
            ));
        }
        let token = self.settings.lock().expect("настройки").token();
        let mut body = Map::new();
        body.insert("code".into(), Value::String(code));
        body.insert("standalone".into(), Value::Bool(true));
        if !token.is_empty() && base == self.base() {
            // Свой живой токен возвращаем серверу: повторная вставка ссылки
            // не сожжёт последнее открытие.
            body.insert("token".into(), Value::String(token));
        }
        let data = self
            .request(reqwest::Method::POST, &format!("{}/api/redeem", base),
                     Some(Value::Object(body)), false, Some(Duration::from_secs(20)))
            .await?;
        let new_token = data.get("token").and_then(Value::as_str).unwrap_or("");
        if new_token.is_empty() {
            return Err(Trouble::new("auth", "Сервер не дал доступа по этой ссылке."));
        }
        let days = data.get("days").cloned().unwrap_or(Value::Null);
        {
            let mut st = self.settings.lock().expect("настройки");
            st.set_login(&base, new_token, days);
            st.save();
        }
        *self.dead.lock().expect("dead") = false;
        self.cache.lock().expect("кэш").clear();
        Ok(json!({"ok": true, "days": data.get("days").cloned().unwrap_or(Value::Null)}))
    }

    fn remember_manager(&self, data: &Value) {
        let mgr = data.get("manager");
        let name = mgr
            .and_then(|m| m.get("name"))
            .and_then(Value::as_str)
            .or_else(|| data.get("manager_name").and_then(Value::as_str))
            .unwrap_or("");
        let scope = mgr
            .and_then(|m| m.get("scope"))
            .and_then(Value::as_str)
            .or_else(|| data.get("scope").and_then(Value::as_str))
            .unwrap_or("");
        if name.is_empty() && scope.is_empty() {
            return;
        }
        let mut keeper = self.manager.lock().expect("manager");
        if *keeper != (name.to_string(), scope.to_string()) {
            *keeper = (name.to_string(), scope.to_string());
            drop(keeper);
            self.settings
                .lock()
                .expect("настройки")
                .remember_manager(name, scope);
            self.settings.lock().expect("настройки").save();
        }
    }

    /// Данные экрана: свежие, устаревшие с подписью или ошибка — всегда словарь.
    pub async fn call(&self, screen: &str, params: &[(&str, String)]) -> Result<Value, Trouble> {
        let path = PATHS
            .iter()
            .find(|(name, _)| *name == screen)
            .map(|(_, path)| *path)
            .unwrap_or("/api/manager/summary");
        let mut query: Vec<(String, String)> = params
            .iter()
            .filter(|(_, v)| !v.is_empty())
            .map(|(k, v)| (k.to_string(), v.clone()))
            .collect();
        query.sort();
        let key = format!(
            "{}?{}",
            screen,
            query
                .iter()
                .map(|(k, v)| format!("{}={}", k, v))
                .collect::<Vec<_>>()
                .join("&")
        );
        let base = self.base();
        let url = reqwest::Url::parse_with_params(&format!("{}{}", base, path), &query)
            .map_err(|_| Trouble::new("server", "Не получилось собрать адрес запроса."))?;
        let attempt = self
            .request(reqwest::Method::GET, url.as_str(), None, true, None)
            .await;
        match attempt {
            Ok(data) => {
                *self.lost_at.lock().expect("lost_at") = None;
                *self.dead.lock().expect("dead") = false;
                self.remember_manager(&data);
                let fresh = normalize(data);
                self.cache
                    .lock()
                    .expect("кэш")
                    .insert(key, (fresh.clone(), now_hhmm()));
                let at = now_hhmm();
                Ok(merged(fresh, false, "", &at, self.ready()))
            }
            Err(trouble) => {
                if trouble.kind == "auth" {
                    *self.dead.lock().expect("dead") = true;
                } else if trouble.kind == "offline" {
                    let mut lost = self.lost_at.lock().expect("lost_at");
                    if lost.is_none() {
                        *lost = Some(now_hhmm());
                    }
                }
                let lost = self.lost_at().clone();
                let cache = self.cache.lock().expect("кэш");
                if let Some((data, at)) = cache.get(&key) {
                    let when = if lost.is_empty() { at.clone() } else { lost.clone() };
                    return Ok(merged_stale(
                        data.clone(), &trouble.text, trouble.kind,
                        &when, at, self.ready(),
                    ));
                }
                Ok(json!({
                    "error": trouble.text, "kind": trouble.kind,
                    "stale": false, "ready": self.ready(),
                }))
            }
        }
    }

    // --- рассылки и входы команды --------------------------------------------
    pub async fn pushes(&self) -> Result<Value, Trouble> {
        self.request(reqwest::Method::GET,
                     &format!("{}/api/manager/pushes", self.base()),
                     None, true, None).await
    }

    pub async fn set_push(&self, key: &str, value: bool) -> Result<Value, Trouble> {
        let mut body = Map::new();
        body.insert(key.to_string(), Value::Bool(value));
        self.request(reqwest::Method::POST,
                     &format!("{}/api/manager/pushes", self.base()),
                     Some(Value::Object(body)), true, None).await
    }

    pub async fn team_links(&self) -> Result<Value, Trouble> {
        self.request(reqwest::Method::GET,
                     &format!("{}/api/manager/app_links", self.base()),
                     None, true, None).await
    }

    pub async fn team_link(&self, employee: &str, revoke: bool) -> Result<Value, Trouble> {
        let path = if revoke { "/api/manager/app_revoke" } else { "/api/manager/app_link" };
        let mut body = Map::new();
        let emp: String = employee.chars().take(200).collect();
        body.insert("employee".into(), Value::String(emp));
        self.request(reqwest::Method::POST,
                     &format!("{}{}", self.base(), path),
                     Some(Value::Object(body)), true, None).await
    }

    /// Книга Excel: открытым потоком, не собирая её в память целиком.
    pub async fn book(
        &self,
        params: &[(&str, String)],
    ) -> Result<reqwest::Response, Trouble> {
        if !self.ready() {
            return Err(Trouble::new("no_link", NO_LINK));
        }
        let query: Vec<(String, String)> = params
            .iter()
            .filter(|(_, v)| !v.is_empty())
            .map(|(k, v)| (k.to_string(), v.clone()))
            .collect();
        let url = reqwest::Url::parse_with_params(
            &format!("{}{}", self.base(), "/api/manager/stats.xlsx"),
            &query,
        )
        .map_err(|_| Trouble::new("server", "Не получилось собрать адрес книги."))?;
        let token = self.settings.lock().expect("настройки").token();
        let resp = self
            .client
            .get(url)
            .header("X-App-Token", &token)
            .timeout(Duration::from_secs(300))
            .send()
            .await
            .map_err(|err| {
                let trouble = net_trouble(&err);
                if trouble.text == CERT_BAD {
                    trouble
                } else {
                    Trouble::new("offline", "Сервер «Хард» не успел собрать книгу.")
                }
            })?;
        let status = resp.status().as_u16();
        if status >= 400 {
            let text = resp.text().await.unwrap_or_default();
            let trouble = trouble_of(status, &text);
            if trouble.kind == "auth" {
                *self.dead.lock().expect("dead") = true;
            }
            return Err(trouble);
        }
        Ok(resp)
    }
}

fn trouble_of(status: u16, body: &str) -> Trouble {
    match status {
        403 => {
            // 403 бывает разный: «не руководитель» и «не ваш сотрудник».
            // Сервер называет причину по-русски — показываем её, а не общую.
            if let Ok(parsed) = serde_json::from_str::<Value>(body) {
                if let Some(named) = parsed.get("error").and_then(Value::as_str) {
                    if !named.is_empty() {
                        return Trouble::status("auth", named, status);
                    }
                }
            }
            Trouble::status(
                "auth",
                "Сервер не считает вас руководителем направления. Это чинит аналитик — \
                 попросите выдать роль.",
                status,
            )
        }
        401 => Trouble::status("auth", DEAD_TOKEN, status),
        410 => Trouble::status(
            "auth",
            "Ссылка уже использована или просрочена. Попросите у аналитика новую.",
            status,
        ),
        404 => Trouble::status(
            "server",
            "Сервер не нашёл, что показать: сотрудника нет в вашей команде или такой \
             ручки у сервера нет.",
            status,
        ),
        400 => Trouble::status("server", "Сервер не понял запрос пульта — сообщите аналитику.", status),
        429 => Trouble::status(
            "server",
            "Сервер просит подождать: слишком много запросов подряд. Попробуйте через минуту.",
            status,
        ),
        s if s >= 500 => Trouble::status(
            "server",
            format!("Сервер «Хард» сломался на этом запросе (код {}). Сообщите аналитику — \
                     сам он об этом не узнает.", s),
            status,
        ),
        s => {
            let detail: String = body.chars().take(120).collect();
            Trouble::status(
                "server",
                format!("Сервер ответил отказом (код {}). {}", s, detail.trim()),
                status,
            )
        }
    }
}

/// Ответ «Харда» → форма, обещанная странице: `date` одним предметом.
fn normalize(mut payload: Value) -> Value {
    let obj = match payload.as_object_mut() {
        Some(obj) => obj,
        None => return json!({"error": "«Хард» ответил не тем, чего пульт ждал."}),
    };
    let iso = obj.get("date").and_then(Value::as_str).map(str::to_string);
    if !obj.get("date").map_or(true, |d| d.is_object()) {
        let today = obj.get("today").and_then(Value::as_str).unwrap_or("").to_string();
        let prev = obj.get("prev_date").and_then(Value::as_str).unwrap_or("").to_string();
        let next = obj.get("next_date").and_then(Value::as_str).unwrap_or("").to_string();
        obj.insert("date_iso".into(), Value::String(iso.clone().unwrap_or_default()));
        obj.insert(
            "date".into(),
            json!({"iso": iso.unwrap_or_default(), "today": today,
                   "prev": prev, "next": next}),
        );
    }
    let mgr_name = obj
        .get("manager")
        .and_then(|m| m.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if !obj.get("manager_name").map_or(false, |v| !v.is_null()) {
        obj.insert("manager_name".into(), Value::String(mgr_name));
    }
    let scope = obj
        .get("manager")
        .and_then(|m| m.get("scope"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if !obj.get("scope").map_or(false, |v| !v.is_null()) {
        obj.insert("scope".into(), Value::String(scope));
    }
    Value::Object(obj.clone())
}

fn merged(data: Value, stale: bool, lost: &str, at: &str, ready: bool) -> Value {
    let mut obj = data;
    if let Some(map) = obj.as_object_mut() {
        map.insert("stale".into(), Value::Bool(stale));
        map.insert("lost_at".into(), Value::String(lost.to_string()));
        map.insert("fresh_at".into(), Value::String(at.to_string()));
        map.insert("ready".into(), Value::Bool(ready));
    }
    obj
}

fn merged_stale(data: Value, error: &str, kind: &str, lost: &str, at: &str, ready: bool) -> Value {
    let mut obj = data;
    if let Some(map) = obj.as_object_mut() {
        map.insert("error".into(), Value::String(error.to_string()));
        map.insert("kind".into(), Value::String(kind.to_string()));
        map.insert("stale".into(), Value::Bool(true));
        map.insert("lost_at".into(), Value::String(lost.to_string()));
        map.insert("fresh_at".into(), Value::String(at.to_string()));
        map.insert("ready".into(), Value::Bool(ready));
    }
    obj
}
