//! Кто стучится в пульт: свой браузер на этом ПК или телефон из тоннеля.
//!
//! ГЛАВНАЯ ЛОВУШКА та же, что у питон-версии: cloudflared стоит на той же
//! машине и приходит с 127.0.0.1, поэтому телефон отличаем по заголовку Host
//! и следам посредника (Cf-Ray и компания). Ошибиться в сторону «спросить
//! код» не страшно; ошибиться в другую — отдать портфель тому, кто угадал
//! поддомен.

use rand::Rng;

pub const COOKIE: &str = "pult_ruk";
pub const MAX_FAILS: u32 = 5;

const LOOPBACK: &[&str] = &["127.0.0.1", "localhost", "::1", "[::1]"];
const FORWARDED: &[&str] = &[
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    "x-real-ip", "forwarded", "cf-connecting-ip", "cf-ray",
    "cf-ipcountry", "cf-visitor",
];

pub fn make_pin() -> String {
    format!("{:04}", rand::thread_rng().gen_range(0..10000u32))
}

pub fn host_name(host: &str) -> String {
    let h = host.trim().to_lowercase();
    if let Some(rest) = h.strip_prefix('[') {
        if let Some(end) = rest.find(']') {
            return format!("{}]", &rest[..=end]);
        }
    }
    h.split(':').next().unwrap_or("").to_string()
}

/// Сравнение постоянного времени: подбор куки по времени ответа — стандартный
/// приём, а сессий у нас единицы.
fn ct_eq(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.bytes().zip(b.bytes()).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

#[derive(Default)]
pub struct Gate {
    pub pin: String,
    pub sessions: Vec<String>,
    pub fails: u32,
    pub alarm: String,
    pub locked: bool,
    pub tunnel_host: String,
}

impl Gate {
    pub fn new() -> Gate {
        Gate { pin: make_pin(), ..Gate::default() }
    }

    /// Свой браузер на этом же ПК. Любое сомнение — «нет».
    pub fn is_console(&self, host: &str, remote: &str, headers: &axum::http::HeaderMap) -> bool {
        if FORWARDED
            .iter()
            .any(|name| headers.get(*name).map_or(false, |v| !v.is_empty()))
        {
            return false;
        }
        if !LOOPBACK.contains(&host_name(host).as_str()) {
            return false;
        }
        matches!(remote.trim().to_lowercase().as_str(), "127.0.0.1" | "::1" | "")
    }

    /// Имя, под которым нас открыли, — наше собственное: свой адрес или живой
    /// тоннель. Чужое имя, ведущее на 127.0.0.1, — отказ.
    pub fn known_host(&self, host: &str) -> bool {
        let host = host_name(host);
        LOOPBACK.contains(&host.as_str())
            || (!self.tunnel_host.is_empty() && host == host_name(&self.tunnel_host))
    }

    pub fn has_session(&self, cookie: &str) -> bool {
        !cookie.is_empty() && self.sessions.iter().any(|live| ct_eq(cookie, live))
    }

    pub fn allowed(&self, host: &str, remote: &str, headers: &axum::http::HeaderMap,
                   cookie: &str) -> bool {
        self.is_console(host, remote, headers) || self.has_session(cookie)
    }

    /// (пустили?, текст для человека, ключ сессии). Пять промахов — тоннеля
    /// больше нет; текст пишет сам гейт, телефону нужен счётчик, а не «ошибка».
    pub fn check(&mut self, value: &str) -> (bool, String, String) {
        if self.locked {
            return (false, "Слишком много неверных попыток — телефон отключён. \
                Включите его заново на компьютере.".into(), String::new());
        }
        let given: String = value.chars().filter(|c| c.is_ascii_digit()).collect();
        if !given.is_empty() && ct_eq(&given, &self.pin) {
            self.fails = 0;
            let key = token();
            self.sessions.push(key.clone());
            return (true, String::new(), key);
        }
        self.fails += 1;
        let left = MAX_FAILS.saturating_sub(self.fails);
        if left == 0 {
            self.lock("ввели неверный код пять раз");
            return (false, "Код не подошёл пять раз. Телефон отключён — включите \
                его заново на компьютере.".into(), String::new());
        }
        let tail = match left {
            1 => "попытка",
            2..=4 => "попытки",
            _ => "попыток",
        };
        (false, format!("Код не подошёл. Осталось {} {}.", left, tail), String::new())
    }

    pub fn lock(&mut self, why: &str) {
        self.locked = true;
        self.sessions.clear();
        self.alarm = format!("{}: в пульт ломились с телефона — {}. Тоннель погашен.",
                             hard::now_hhmm(), why);
    }

    pub fn reopen(&mut self) {
        self.pin = make_pin();
        self.fails = 0;
        self.locked = false;
        self.sessions.clear();
    }

    pub fn close(&mut self) {
        self.sessions.clear();
        self.tunnel_host = String::new();
    }
}

mod hard {
    pub(crate) fn now_hhmm() -> String {
        let secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        format!("{:02}:{:02}", (secs / 3600) % 24, (secs / 60) % 60)
    }
}

/// Ключ сессии: 32 байта случайного в url-safe base64.
fn token() -> String {
    use base64::Engine;
    let bytes: Vec<u8> = (0..32).map(|_| rand::thread_rng().gen()).collect();
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

/// Значение куки из заголовка Cookie (только нужное нам имя).
pub fn cookie_value(headers: &axum::http::HeaderMap, name: &str) -> String {
    headers
        .get_all(axum::http::header::COOKIE)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|line| line.split(';'))
        .filter_map(|part| part.split_once('='))
        .find(|(k, _)| k.trim() == name)
        .map(|(_, v)| v.trim().to_string())
        .unwrap_or_default()
}
