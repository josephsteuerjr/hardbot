#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Контур · Выезды — десктоп-пульт. Tauri-шелл + вся работа с Excel на Rust:
// чтение реестров calamine (xlsx/xlsm/xlsb/xls — .xlsb от заказчика без
// пересохранения), запись rust_xlsxwriter (типизированные даты/числа, шапки
// блоков, автофильтр). Сервер после этого гоняет только JSON — openpyxl
// остался там лишь как легаси-путь для старых клиентов.

use base64::Engine as _;
use calamine::{Data, Reader};
use rust_xlsxwriter::{Color, ExcelDateTime, Format, FormatAlign, Workbook, XlsxError};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::io::Cursor;

// ---------- чтение: файл → гриды листов (для POST /admin/import.json) ----------

#[derive(serde::Serialize)]
struct Sheet {
    name: String,
    grid: Vec<Vec<Value>>,
}

fn cell_to_json(c: &Data) -> Value {
    match c {
        Data::Empty | Data::Error(_) => Value::Null,
        Data::String(s) => {
            let t = s.trim();
            if t.is_empty() { Value::Null } else { Value::String(t.to_string()) }
        }
        // целые float → int, чтобы сервер не получил "100900.0"
        Data::Float(f) => {
            if f.fract() == 0.0 && f.abs() < 9e15 { json!(*f as i64) } else { json!(f) }
        }
        Data::Int(i) => json!(i),
        Data::Bool(b) => json!(b),
        Data::DateTime(dt) => match dt.as_datetime() {
            // Display NaiveDateTime = "YYYY-MM-DD HH:MM:SS"; полночь режем до даты
            Some(ndt) => {
                let s = ndt.to_string();
                Value::String(s.strip_suffix(" 00:00:00").unwrap_or(&s).to_string())
            }
            None => Value::Null,
        },
        Data::DateTimeIso(s) => json!(s),
        Data::DurationIso(s) => json!(s),
    }
}

#[tauri::command]
fn parse_workbook(b64: String, filename: String) -> Result<Vec<Sheet>, String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| format!("base64: {e}"))?;
    let mut wb = calamine::open_workbook_auto_from_rs(Cursor::new(bytes))
        .map_err(|e| format!("не смог открыть {filename}: {e}"))?;
    let mut out = Vec::new();
    for name in wb.sheet_names().to_vec() {
        if let Ok(range) = wb.worksheet_range(&name) {
            let grid: Vec<Vec<Value>> = range
                .rows()
                .map(|row| row.iter().map(cell_to_json).collect())
                .collect();
            out.push(Sheet { name, grid });
        }
    }
    if out.is_empty() {
        return Err(format!("в {filename} нет читаемых листов"));
    }
    Ok(out)
}

// ---------- запись: JSON с сервера → xlsx через «Сохранить как» ----------

#[derive(Deserialize)]
struct FieldMeta {
    key: String,
    title: String,
    block: String,
    source: String,
    date: bool,
    num: bool,
}

/// "10 000 000" / "12 400,50" / 100900 → f64
fn parse_num(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => {
            let t: String = s
                .chars()
                .filter(|c| !c.is_whitespace())
                .map(|c| if c == ',' { '.' } else { c })
                .collect();
            if t.is_empty() { None } else { t.parse().ok() }
        }
        _ => None,
    }
}

/// "2026-06-12" | "12.06.2026" | "12.06.26" | "2026-06-12 00:00:00" → (г, м, д)
fn parse_date(s: &str) -> Option<(u16, u8, u8)> {
    let core = s.trim().split_whitespace().next()?;
    let (y, m, d): (i32, u32, u32) = if core.contains('-') {
        let p: Vec<_> = core.split('-').collect();
        if p.len() != 3 {
            return None;
        }
        (p[0].parse().ok()?, p[1].parse().ok()?, p[2].parse().ok()?)
    } else if core.contains('.') {
        let p: Vec<_> = core.split('.').collect();
        if p.len() != 3 {
            return None;
        }
        let yy: i32 = p[2].parse().ok()?;
        (if yy < 100 { 2000 + yy } else { yy }, p[1].parse().ok()?, p[0].parse().ok()?)
    } else {
        return None;
    };
    ((1900..=9999).contains(&y) && (1..=12).contains(&m) && (1..=31).contains(&d))
        .then_some((y as u16, m as u8, d as u8))
}

fn col_width(key: &str, title: &str) -> f64 {
    match key {
        "address" | "visit_comment" => 42.0,
        "client" | "address_customer" | "extra_address" | "point_address" => 28.0,
        "responsible" | "point_name" | "call_comment" => 22.0,
        k if k.ends_with("_date") => 12.0,
        _ => (title.chars().count() as f64 * 1.1).clamp(9.0, 20.0),
    }
}

fn build_registry(fields: &[FieldMeta], rows: &[Map<String, Value>]) -> Result<Vec<u8>, XlsxError> {
    let mut wb = Workbook::new();
    let ws = wb.add_worksheet();
    ws.set_name("Портфели")?;

    let bold = Format::new().set_bold();
    let block_hdr = Format::new().set_bold().set_align(FormatAlign::Center);
    let src_fmt = Format::new().set_italic().set_font_color(Color::RGB(0x8A8578));
    let date_fmt = Format::new().set_num_format("DD.MM.YYYY");
    let num_fmt = Format::new().set_num_format("#,##0");

    // строка 0 — шапки блоков (merge по колонкам блока, как в «Хард.xlsb»)
    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut start = 0usize;
    for i in 1..=fields.len() {
        if i == fields.len() || fields[i].block != fields[start].block {
            spans.push((start, i - 1));
            start = i;
        }
    }
    for &(s, e) in &spans {
        if e > s {
            ws.merge_range(0, s as u16, 0, e as u16, &fields[s].block, &block_hdr)?;
        } else {
            ws.write_string_with_format(0, s as u16, &fields[s].block, &block_hdr)?;
        }
    }

    // строка 1 — заголовки, строка 2 — сх/хард, ширины
    for (c, f) in fields.iter().enumerate() {
        let c = c as u16;
        ws.write_string_with_format(1, c, &f.title, &bold)?;
        ws.write_string_with_format(2, c, &f.source, &src_fmt)?;
        ws.set_column_width(c, col_width(&f.key, &f.title))?;
    }

    // данные со строки 3: даты — датами, деньги — числами, остальное — как есть
    for (r, row) in rows.iter().enumerate() {
        let r = (r + 3) as u32;
        for (c, f) in fields.iter().enumerate() {
            let c = c as u16;
            let Some(v) = row.get(&f.key) else { continue };
            if v.is_null() {
                continue;
            }
            if f.date {
                if let Some((y, m, d)) = v.as_str().and_then(parse_date) {
                    let dt = ExcelDateTime::from_ymd(y, m, d)?;
                    ws.write_datetime_with_format(r, c, &dt, &date_fmt)?;
                    continue;
                }
            }
            if f.num {
                if let Some(n) = parse_num(v) {
                    ws.write_number_with_format(r, c, n, &num_fmt)?;
                    continue;
                }
            }
            match v {
                Value::String(s) => ws.write_string(r, c, s)?,
                Value::Number(n) => ws.write_number(r, c, n.as_f64().unwrap_or(0.0))?,
                other => ws.write_string(r, c, &other.to_string())?,
            };
        }
    }

    let last_row = (rows.len() + 2).max(3) as u32;
    ws.autofilter(1, 0, last_row, fields.len().saturating_sub(1) as u16)?;
    ws.set_freeze_panes(3, 0)?;
    wb.save_to_buffer()
}

const MARKER_COLS: [(&str, &str, f64); 9] = [
    ("created_at", "Дата", 19.0),
    ("kind", "Тип", 12.0),
    ("telegram_id", "telegram_id", 14.0),
    ("rep", "Кейс (rep)", 16.0),
    ("client", "Клиент", 28.0),
    ("address", "Адрес", 42.0),
    ("label", "Метка/подпись", 30.0),
    ("lat", "Широта", 12.0),
    ("lon", "Долгота", 12.0),
];

fn build_markers(rows: &[Map<String, Value>]) -> Result<Vec<u8>, XlsxError> {
    let mut wb = Workbook::new();
    let ws = wb.add_worksheet();
    ws.set_name("Метки")?;

    let bold = Format::new().set_bold();
    let dt_fmt = Format::new().set_num_format("DD.MM.YYYY HH:MM");
    let coord_fmt = Format::new().set_num_format("0.000000");

    for (c, (_k, title, w)) in MARKER_COLS.iter().enumerate() {
        ws.write_string_with_format(0, c as u16, *title, &bold)?;
        ws.set_column_width(c as u16, *w)?;
    }
    for (r, row) in rows.iter().enumerate() {
        let r = (r + 1) as u32;
        for (c, (k, _t, _w)) in MARKER_COLS.iter().enumerate() {
            let c = c as u16;
            let Some(v) = row.get(*k) else { continue };
            match (*k, v) {
                (_, Value::Null) => {}
                ("created_at", Value::String(s)) => match ExcelDateTime::parse_from_str(s) {
                    Ok(dt) => {
                        ws.write_datetime_with_format(r, c, &dt, &dt_fmt)?;
                    }
                    Err(_) => {
                        ws.write_string(r, c, s)?;
                    }
                },
                ("lat" | "lon", v) => {
                    if let Some(n) = v.as_f64() {
                        ws.write_number_with_format(r, c, n, &coord_fmt)?;
                    }
                }
                (_, Value::String(s)) => {
                    ws.write_string(r, c, s)?;
                }
                (_, Value::Number(n)) => {
                    ws.write_number(r, c, n.as_f64().unwrap_or(0.0))?;
                }
                (_, other) => {
                    ws.write_string(r, c, &other.to_string())?;
                }
            }
        }
    }
    if !rows.is_empty() {
        ws.autofilter(0, 0, rows.len() as u32, (MARKER_COLS.len() - 1) as u16)?;
    }
    ws.set_freeze_panes(1, 0)?;
    wb.save_to_buffer()
}

fn save_dialog(default_name: &str) -> Option<std::path::PathBuf> {
    rfd::FileDialog::new()
        .set_file_name(default_name)
        .add_filter("Excel", &["xlsx"])
        .save_file()
}

#[tauri::command]
fn save_registry_xlsx(
    fields: Vec<FieldMeta>,
    rows: Vec<Map<String, Value>>,
) -> Result<Option<String>, String> {
    let Some(path) = save_dialog("registry_export.xlsx") else {
        return Ok(None);
    };
    let buf = build_registry(&fields, &rows).map_err(|e| e.to_string())?;
    std::fs::write(&path, buf).map_err(|e| e.to_string())?;
    Ok(Some(path.display().to_string()))
}

#[tauri::command]
fn save_markers_xlsx(rows: Vec<Map<String, Value>>) -> Result<Option<String>, String> {
    let Some(path) = save_dialog("markers.xlsx") else {
        return Ok(None);
    };
    let buf = build_markers(&rows).map_err(|e| e.to_string())?;
    std::fs::write(&path, buf).map_err(|e| e.to_string())?;
    Ok(Some(path.display().to_string()))
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            parse_workbook,
            save_registry_xlsx,
            save_markers_xlsx
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

// ---------- roundtrip-тесты: пишем rust_xlsxwriter-ом, читаем calamine-ом ----------

#[cfg(test)]
mod tests {
    use super::*;

    fn meta(key: &str, title: &str, block: &str, date: bool, num: bool) -> FieldMeta {
        FieldMeta {
            key: key.into(),
            title: title.into(),
            block: block.into(),
            source: "СХ".into(),
            date,
            num,
        }
    }

    #[test]
    fn registry_roundtrip() {
        let fields = vec![
            meta("contract_id", "Номер КД / ID", "Информация по клиенту", false, false),
            meta("debt_total", "долг всего", "Разбивка по долгу", false, true),
            meta("assign_date", "Дата закрепления", "Блок выезда", true, false),
        ];
        let mut row = Map::new();
        row.insert("contract_id".into(), json!("T-1"));
        row.insert("debt_total".into(), json!("10 000 000"));
        row.insert("assign_date".into(), json!("2026-06-12"));

        let buf = build_registry(&fields, &[row]).unwrap();
        let mut wb = calamine::open_workbook_auto_from_rs(Cursor::new(buf)).unwrap();
        let r = wb.worksheet_range("Портфели").unwrap();

        assert_eq!(r.get_value((1, 0)).unwrap().to_string(), "Номер КД / ID");
        // "10 000 000" текстом → настоящее число
        assert!(
            matches!(r.get_value((3, 1)), Some(Data::Float(f)) if (f - 10_000_000.0).abs() < 1e-9)
        );
        // ISO-строка → типизированная дата Excel
        assert!(matches!(r.get_value((3, 2)), Some(Data::DateTime(_))));

        // и обратно через cell_to_json: дата → ISO, целый float → int
        let grid: Vec<Vec<Value>> =
            r.rows().map(|row| row.iter().map(cell_to_json).collect()).collect();
        assert_eq!(grid[3][2], json!("2026-06-12"));
        assert_eq!(grid[3][1], json!(10_000_000i64));
    }

    #[test]
    fn markers_roundtrip() {
        let mut row = Map::new();
        row.insert("created_at".into(), json!("2026-07-02 15:28:04"));
        row.insert("kind".into(), json!("courier"));
        row.insert("telegram_id".into(), json!(809306689i64));
        row.insert("rep".into(), json!(""));
        row.insert("client".into(), json!("Тестов"));
        row.insert("address".into(), json!("г. Москва"));
        row.insert("label".into(), json!("дверь во дворе"));
        row.insert("lat".into(), json!(55.7558));
        row.insert("lon".into(), json!(37.6173));

        let buf = build_markers(&[row]).unwrap();
        let mut wb = calamine::open_workbook_auto_from_rs(Cursor::new(buf)).unwrap();
        let r = wb.worksheet_range("Метки").unwrap();
        assert_eq!(r.get_value((0, 0)).unwrap().to_string(), "Дата");
        assert!(matches!(r.get_value((1, 0)), Some(Data::DateTime(_))));
        assert!(matches!(r.get_value((1, 7)), Some(Data::Float(f)) if (f - 55.7558).abs() < 1e-9));
    }

    #[test]
    fn parse_helpers() {
        assert_eq!(parse_num(&json!("12 400,50")), Some(12400.5));
        assert_eq!(parse_num(&json!("1\u{a0}000")), Some(1000.0));
        assert_eq!(parse_num(&json!("н/д")), None);
        assert_eq!(parse_date("12.06.26"), Some((2026, 6, 12)));
        assert_eq!(parse_date("2026-06-12 00:00:00"), Some((2026, 6, 12)));
        assert_eq!(parse_date("июнь"), None);
    }
}
