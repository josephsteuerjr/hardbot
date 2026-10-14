//! Настройки пульта: файл JSON рядом с бинарником, тот же формат, что у
//! питон-версии (`pult_ruk`). Человек, знающий один пульт, узнает и второй —
//! включая файл настроек, который можно перенести руками.
//!
//! Структура: `{url, servers: {адрес: {token, days, manager}}}`. Серверов
//! может быть несколько (прод и контур компании); ссылка входа сама называет
//! сервер, здесь — лишь выбор активного. Токен живёт ТОЛЬКО в этом файле:
//! ни в адресе страницы, ни в JSON, который уходит в браузер.

use std::path::PathBuf;

use serde_json::{Map, Value};

pub fn settings_path() -> PathBuf {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("hard-pult.exe"));
    exe.parent()
        .map(|d| d.to_path_buf())
        .unwrap_or_default()
        .join("настройки пульта руководителя.json")
}

#[derive(Clone)]
pub struct Settings {
    path: PathBuf,
    data: Map<String, Value>,
    /// Серверы, чей токен не открылся DPAPI (другая учётка Windows). Значение
    /// в `data` остаётся ЗАПЕЧАТАННЫМ: следующий save() перепишет его как есть,
    /// а не затрёт пустой строкой — перенос файла на другую учётку не должен
    /// уничтожать единственную копию токена.
    locked: std::collections::BTreeSet<String>,
}

impl Settings {
    pub fn load() -> Settings {
        let path = settings_path();
        let data = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .and_then(|v| match v {
                Value::Object(map) => Some(map),
                _ => None,
            })
            .unwrap_or_default();
        let mut s = Settings { path, data, locked: Default::default() };
        s.ensure_servers();
        let mut locked = std::collections::BTreeSet::new();
        if let Some(servers) = s.data.get_mut("servers").and_then(Value::as_object_mut) {
            for (url, entry) in servers.iter_mut() {
                if let Some(token) = entry.get("token").and_then(Value::as_str) {
                    match crate::token_protection::open(token) {
                        Ok(clear) => { entry["token"] = Value::String(clear); },
                        Err(err) => {
                            // Недоступен ЭТОЙ учётной записи: остаётся запечатанным
                            // на месте, пользоваться нельзя, уничтожать нельзя.
                            locked.insert(url.clone());
                            eprintln!("Токен настроек недоступен для этой учётной записи Windows ({url}): {err}");
                        }
                    }
                }
            }
        }
        s.locked = locked;
        s
    }

    /// Гарантировать `servers` и осмысленный `url`. Плоский старый файл
    /// (один сервер) переезжает в карту серверов при первой загрузке.
    fn ensure_servers(&mut self) {
        let flat_url = self
            .data
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim_end_matches('/')
            .to_string();
        let had_servers = self.data.contains_key("servers");
        let mut servers: Map<String, Value> = match self.data.get("servers") {
            Some(Value::Object(map)) => map.clone(),
            _ => Map::new(),
        };
        if !had_servers && !flat_url.is_empty() {
            let mut flat = Map::new();
            for key in ["token", "days", "manager"] {
                if let Some(v) = self.data.get(key).cloned() {
                    if !v.is_null() {
                        flat.insert(key.to_string(), v);
                    }
                }
            }
            if !flat.is_empty() {
                servers.insert(flat_url.clone(), Value::Object(flat));
            }
            for key in ["token", "days", "manager"] {
                self.data.remove(key);
            }
        }
        if !flat_url.is_empty() {
            servers
                .entry(flat_url.clone())
                .or_insert_with(|| Value::Object(Map::new()));
        } else if !servers.is_empty() {
            let with_token = servers
                .iter()
                .find(|(_, v)| v.get("token").and_then(Value::as_str).map_or(false, |t| !t.is_empty()))
                .map(|(k, _)| k.clone());
            let pick = with_token.unwrap_or_else(|| servers.keys().next().cloned().unwrap());
            self.data
                .insert("url".into(), Value::String(pick));
        }
        self.data
            .insert("servers".into(), Value::Object(servers));
    }

    pub fn url(&self) -> String {
        self.data
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim_end_matches('/')
            .to_string()
    }

    pub fn set_url(&mut self, url: &str) {
        let url = url.trim_end_matches('/').to_string();
        if let Some(servers) = self.data.get_mut("servers").and_then(Value::as_object_mut) {
            servers
                .entry(url.clone())
                .or_insert_with(|| Value::Object(Map::new()));
        }
        self.data.insert("url".into(), Value::String(url));
    }

    /// Известен ли сервер (переключаться можно только между знакомыми).
    pub fn knows(&self, url: &str) -> bool {
        self.data
            .get("servers")
            .and_then(Value::as_object)
            .map_or(false, |m| m.contains_key(url.trim_end_matches('/')))
    }

    /// Список серверов для экрана «Я и телефон»: (адрес, есть ли токен).
    pub fn servers(&self) -> Vec<(String, bool)> {
        self.data
            .get("servers")
            .and_then(Value::as_object)
            .map(|m| {
                m.iter()
                    .map(|(url, entry)| {
                        let ready = !self.locked.contains(url)
                            && entry
                                .get("token")
                                .and_then(Value::as_str)
                                .map_or(false, |t| !t.is_empty());
                        (url.clone(), ready)
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Запись активного сервера (токен, days, manager).
    fn entry(&mut self) -> &mut Map<String, Value> {
        let url = self.url();
        self.data
            .get_mut("servers")
            .and_then(Value::as_object_mut)
            .expect("ensure_servers уже создал карту")
            .entry(url)
            .or_insert_with(|| Value::Object(Map::new()))
            .as_object_mut()
            .expect("записи серверов — всегда объекты")
    }

    pub fn token(&mut self) -> String {
        let url = self.url();
        if self.locked.contains(&url) {
            return String::new(); // запечатанный и недоступный — как «не входил»
        }
        self.entry()
            .get("token")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    }

    pub fn manager(&mut self) -> (String, String) {
        let mgr = self.entry().get("manager").cloned().unwrap_or(Value::Null);
        let name = mgr.get("name").and_then(Value::as_str).unwrap_or("");
        let scope = mgr.get("scope").and_then(Value::as_str).unwrap_or("");
        (name.to_string(), scope.to_string())
    }

    pub fn set_login(&mut self, url: &str, token: &str, days: Value) {
        self.set_url(url);
        self.locked.remove(url.trim_end_matches('/'));
        let entry = self.entry();
        entry.insert("token".into(), Value::String(token.to_string()));
        entry.insert("days".into(), days);
    }

    pub fn remember_manager(&mut self, name: &str, scope: &str) {
        if name.is_empty() && scope.is_empty() {
            return;
        }
        let mut mgr = Map::new();
        mgr.insert("name".into(), Value::String(name.to_string()));
        mgr.insert("scope".into(), Value::String(scope.to_string()));
        self.entry().insert("manager".into(), Value::Object(mgr));
    }

    /// «Выйти из пульта»: забыть токен АКТИВНОГО сервера, не трогая другие.
    pub fn forget(&mut self) {
        let entry = self.entry();
        entry.remove("token");
        entry.remove("days");
        entry.remove("manager");
    }

    pub fn save(&self) -> bool {
        let mut protected = self.data.clone();
        if let Some(servers) = protected.get_mut("servers").and_then(Value::as_object_mut) {
            for entry in servers.values_mut() {
                if let Some(token) = entry.get("token").and_then(Value::as_str) {
                    if token.starts_with(crate::token_protection::SEALED_PREFIX) {
                        // Уже запечатан (недоступен этой учётке) — переписать как есть.
                        continue;
                    }
                    if !token.is_empty() {
                        match crate::token_protection::seal(token) {
                            Ok(sealed) => { entry["token"] = Value::String(sealed); },
                            Err(_) => return false,
                        }
                    }
                }
            }
        }
        let text = serde_json::to_string_pretty(&Value::Object(protected))
            .unwrap_or_default();
        // Атомарно: краш посреди записи не оставляет битый JSON (молчаливая
        // потеря всех серверов при следующем load — исправлено 14.10).
        let tmp = self.path.with_extension("json.tmp");
        if std::fs::write(&tmp, &text).is_err() {
            return false;
        }
        if std::fs::rename(&tmp, &self.path).is_err() {
            let _ = std::fs::remove_file(&tmp);
            return false;
        }
        true
    }
}
