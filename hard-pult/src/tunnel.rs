//! Быстрый тоннель cloudflared: пульт с ПК становится виден телефону.
//! Без аккаунта и настройки: подпроцесс печатает адрес
//! `https://<случайное>.trycloudflare.com` — этого достаточно.
//!
//! Тоннель живёт ровно столько, сколько живёт пульт: осиротевший cloudflared —
//! это открытая наружу дверь в портфель направления, поэтому гасим его и в
//! штатном выходе, и при падении. Отсутствие бинарника — состояние, а не
//! ошибка: кнопка остаётся, человеку пишется, что поставить.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::process::{Child, Command};

pub const WINGET: &str = "winget install --id Cloudflare.cloudflared";
pub const NO_BINARY: &str = "Для показа на телефоне нужна программа cloudflared, а её на \
    этом компьютере нет. Установите одной командой в терминале: \
    winget install --id Cloudflare.cloudflared — и нажмите «Включить телефон» снова.";

const WAIT_SECS: u64 = 45;

#[derive(Debug)]
pub struct TunnelTrouble(pub String);

#[derive(Clone, serde::Serialize)]
pub struct TunnelState {
    pub on: bool,
    pub url: String,
    pub ready: bool,
    pub error: String,
}

struct Running {
    child: Child,
}

#[derive(Clone)]
pub struct Tunnel {
    inner: Arc<Mutex<Option<Running>>>,
    url: Arc<Mutex<String>>,
}

impl Tunnel {
    pub fn new() -> Tunnel {
        Tunnel {
            inner: Arc::new(Mutex::new(None)),
            url: Arc::new(Mutex::new(String::new())),
        }
    }

    /// Адрес быстрого тоннеля из строки вывода cloudflared.
    fn extract_url(text: &str) -> String {
        let lower = text.to_ascii_lowercase();
        let mut from = 0usize;
        while let Some(idx) = lower[from..].find("https://") {
            let start = from + idx;
            let host: String = text[start + 8..]
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '.')
                .collect();
            if host.to_ascii_lowercase().ends_with(".trycloudflare.com") {
                return format!("https://{}", host);
            }
            from = start + 8;
            if from >= text.len() {
                break;
            }
        }
        String::new()
    }

    pub fn available(&self) -> bool {
        find_binary().is_some()
    }

    pub fn state(&self) -> TunnelState {
        let alive = {
            let mut guard = self.inner.lock().expect("тоннель");
            match guard.as_mut() {
                Some(run) => run
                    .child
                    .try_wait()
                    .map(|w| w.is_none())
                    .unwrap_or(false),
                None => false,
            }
        };
        let url = self.url.lock().expect("url").clone();
        let on = alive && !url.is_empty();
        TunnelState {
            on,
            url: if on { url } else { String::new() },
            ready: self.available(),
            error: String::new(),
        }
    }

    pub async fn start(&self, port: u16) -> Result<String, TunnelTrouble> {
        self.stop().await;
        let binary = find_binary().ok_or_else(|| TunnelTrouble(NO_BINARY.to_string()))?;
        let mut command = Command::new(&binary);
        command
            .args(["tunnel", "--url", &format!("http://127.0.0.1:{}", port),
                   "--no-autoupdate"])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null());
        #[cfg(windows)]
        {
            // Пульт живёт без консоли; cloudflared без этого флага открыл бы
            // своё окно — и его закрыли бы первым движением, вместе с тоннелем.
            // (у tokio::process::Command этот метод свой, трейта не нужно)
            command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let mut child = command
            .spawn()
            .map_err(|e| TunnelTrouble(format!(
                "Не удалось запустить cloudflared: {}. Переустановите его: {}", e, WINGET)))?;
        let stdout = child.stdout.take();
        let url_slot = self.url.clone();
        // Читаем вывод в стороне: cloudflared пишет адрес и продолжает сыпать
        // логи всю жизнь; не читать — значит однажды упереться в буфер трубы.
        let reader = tokio::spawn(async move {
            use tokio::io::{AsyncBufReadExt, BufReader};
            if let Some(out) = stdout {
                let mut lines = BufReader::new(out).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    let url = Tunnel::extract_url(&line);
                    if !url.is_empty() {
                        *url_slot.lock().expect("url") = url;
                        break;
                    }
                }
            }
        });
        {
            self.inner.lock().expect("тоннель").replace(Running { child });
        }
        let deadline = tokio::time::Instant::now() + Duration::from_secs(WAIT_SECS);
        loop {
            let url = self.url.lock().expect("url").clone();
            if !url.is_empty() {
                let _ = reader.await;
                return Ok(url);
            }
            if tokio::time::Instant::now() >= deadline {
                break;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        self.stop().await;
        Err(TunnelTrouble(format!(
            "cloudflared не выдал адрес за {} секунд — похоже, у компьютера нет \
             интернета. Попробуйте ещё раз.",
            WAIT_SECS
        )))
    }

    pub async fn stop(&self) {
        let taken = self.inner.lock().expect("тоннель").take();
        if let Some(mut run) = taken {
            let _ = run.child.start_kill();
            let _ = tokio::time::timeout(Duration::from_secs(5), run.child.wait()).await;
        }
        self.url.lock().expect("url").clear();
    }
}

/// Путь к cloudflared: сначала PATH, потом привычные места winget.
fn find_binary() -> Option<String> {
    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            for name in ["cloudflared.exe", "cloudflared"] {
                let full = dir.join(name);
                if full.is_file() {
                    return full.to_str().map(str::to_string);
                }
            }
        }
    }
    let local = std::env::var("LOCALAPPDATA").unwrap_or_default();
    let guesses = [
        format!("{}\\Microsoft\\WinGet\\Links\\cloudflared.exe", local),
        "C:\\Program Files\\cloudflared\\cloudflared.exe".into(),
        "C:\\Program Files (x86)\\cloudflared\\cloudflared.exe".into(),
    ];
    guesses
        .into_iter()
        .find(|path| std::path::Path::new(path).is_file())
}

#[cfg(test)]
mod tests {
    use super::Tunnel;

    #[test]
    fn extracts_quick_tunnel_url_from_noisy_output() {
        let line = "2026-09-22 INF |  Your quick Tunnel has been created! \
                    Visit it at: https://bold-cat-77.trycloudflare.com |";
        assert_eq!(
            Tunnel::extract_url(line),
            "https://bold-cat-77.trycloudflare.com"
        );
        assert_eq!(Tunnel::extract_url("ничего похожего"), "");
    }
}
