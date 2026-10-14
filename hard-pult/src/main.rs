//! Хард — пульт руководителя одним бинарником.
//!
//! Решение владельца 22.09.2026: аналитику — питон, руководителю — один exe,
//! который можно просто положить на машину: без питона, без Qt. Внутри то же,
//! что у питон-версии `pult_ruk`: местный сервер на 127.0.0.1, страница в
//! браузере, вход ссылкой, телефон через cloudflared с кодом-пропуском.
//!
//! Сборка чистым Rust (TLS через schannel): машине не нужен компилятор C —
//! на нём падала прошлая попытка сборки из-за ring.

#![cfg_attr(all(target_os = "windows", not(debug_assertions)), windows_subsystem = "windows")]

mod hard;
mod server;
mod session;
mod settings;
mod token_protection;
mod tunnel;

use std::io::Write;
use std::sync::{Arc, Mutex};

use session::Gate;

fn main() {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("не удалось поднять рантайм");
    runtime.block_on(run());
}

async fn run() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let no_browser = args.iter().any(|a| a == "--no-browser");
    let port = args
        .iter()
        .position(|a| a == "--port")
        .and_then(|i| args.get(i + 1))
        .and_then(|v| v.parse::<u16>().ok())
        .unwrap_or(0);

    let settings = Arc::new(Mutex::new(settings::Settings::load()));
    let hard = Arc::new(tokio::sync::Mutex::new(hard::Hard::new(settings.clone())));
    let tunnel = tunnel::Tunnel::new();
    let gate = Arc::new(Mutex::new(Gate::new()));
    let file_log = state_log();

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port))
        .await
        .expect("не удалось занять порт на 127.0.0.1");
    let port = listener
        .local_addr()
        .expect("адрес слушателя")
        .port();

    let state = server::AppState {
        settings,
        hard,
        tunnel: tunnel.clone(),
        gate,
        port,
        log: Arc::new(move |line: &str| {
            eprintln!("{}", line);
            file_log(line);
        }),
    };
    let app = server::router(state);
    let url = format!("http://127.0.0.1:{}/", port);
    eprintln!("пульт руководителя открыт: {}", url);
    state_log()(&format!("пульт руководителя открыт: {}", url));

    if !no_browser {
        open_browser(&url);
    }

    // Пульт закрывают крестиком окна у питон-версии; у бинарника консоли нет
    // (windows_subsystem), поэтому живём, пока не снимут процесс, и аккуратно
    // гасим тоннель на ctrl_c — для запусков из терминала.
    let serve = axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    );
    if let Err(err) = serve.await {
        eprintln!("сервер остановился: {}", err);
    }
    // Уборка: осиротевший cloudflared — открытая наружу дверь.
    tunnel.stop().await;
    eprintln!("пульт закрывается, тоннель гаснет.");
}

// Лог-файл нужен и до сборки AppState (строка «пульт открыт»), и внутри него;
// держим один файл через ленивую глобальную точку.
static LOG_FILE: Mutex<Option<std::fs::File>> = Mutex::new(None);

fn state_log() -> impl Fn(&str) {
    move |line: &str| {
        let stamp = hard::now_hhmm();
        let path = settings::settings_path()
            .parent()
            .map(|d| d.join("пульт руководителя.log"))
            .unwrap_or_else(|| std::path::PathBuf::from("пульт руководителя.log"));
        let mut keeper = LOG_FILE.lock().unwrap_or_else(|p| p.into_inner());
        if keeper.is_none() {
            *keeper = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .ok();
        }
        if let Some(file) = keeper.as_mut() {
            let _ = writeln!(file, "{} {}", stamp, line);
        }
    }
}

fn open_browser(url: &str) {
    let mut command;
    #[cfg(windows)]
    {
        command = std::process::Command::new("cmd");
        command.args(["/c", "start", "", url]);
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    #[cfg(not(windows))]
    {
        command = std::process::Command::new("xdg-open");
        command.arg(url);
    }
    let _ = command.spawn();
}
