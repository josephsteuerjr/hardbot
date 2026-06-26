#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Контур · Выезды — десктоп-пульт. Тонкий Tauri-шелл: фронт (dist/) — статика,
// работает с admin-API сервера по https (CORS включён на сервере). Tauri-плагины
// не нужны: всё через webview fetch.
fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
