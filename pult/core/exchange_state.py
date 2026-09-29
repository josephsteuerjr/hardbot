"""Локальная база обмена пульта (опыт exp/fast-exchange, 30.09.2026).

Пульт помнит последние гриды реестра, которые сервер уже принял (SQLite на
этой машине, вне общей папки и вне git), и шлёт импорт разницей к ним. Сервер
собирает из разницы тот же грид и сверяет отпечаток; не знает базы — 409
delta_base_unknown, и грид уходит целиком, как раньше.

Выключить: переменная окружения HARD_EXCHANGE_DELTA=0.
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import grid_delta

KEEP = 3
FALLBACK_ERRORS = {"delta_base_unknown", "delta_mismatch", "delta_and_sheets"}


def enabled() -> bool:
    return os.getenv("HARD_EXCHANGE_DELTA", "1").strip() not in ("0", "false", "no")


def db_path() -> Path:
    raw = os.getenv("HARD_EXCHANGE_DB")
    if raw:
        return Path(raw)
    root = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "Хард"
    root.mkdir(parents=True, exist_ok=True)
    return root / "exchange.sqlite"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(db_path())
    c.execute("CREATE TABLE IF NOT EXISTS grids (digest TEXT PRIMARY KEY, sheet TEXT,"
              " rows INTEGER, gz BLOB, at REAL)")
    return c


def remember(grid: list[list], sheet: str) -> str:
    raw = grid_delta.canonical(grid)
    import hashlib
    digest = hashlib.sha256(raw).hexdigest()
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO grids VALUES (?,?,?,?,?)",
                  (digest, sheet, len(grid), gzip.compress(raw, 6), time.time()))
        c.execute("DELETE FROM grids WHERE digest NOT IN (SELECT digest FROM grids"
                  " ORDER BY at DESC LIMIT ?)", (KEEP,))
    return digest


def latest(sheet: str) -> tuple[str, list[list]] | None:
    with _conn() as c:
        row = c.execute("SELECT digest, gz FROM grids WHERE sheet=? ORDER BY at DESC LIMIT 1",
                        (sheet,)).fetchone()
    if not row:
        return None
    return row[0], json.loads(gzip.decompress(row[1]))


def post_import(admin, path: str, body: dict, *, sheet: str, timeout: float = 180,
                log=lambda s: None):
    """POST импорта/предпросмотра: разницей, если есть база и это выгодно,
    иначе — как раньше. Успех (сервер назвал свою базу) запоминается."""
    sheets = body.get("sheets") or []
    grid = next((s.get("grid") for s in sheets
                 if isinstance(s, dict) and s.get("name") == sheet), None)
    answer = None
    if enabled() and isinstance(grid, list):
        base = latest(sheet)
        if base:
            delta = grid_delta.make_delta(base[1], grid)
            full_n = len(grid_delta.canonical(grid))
            delta_n = len(json.dumps(delta, ensure_ascii=False))
            if delta_n < full_n // 2:
                log(f"Шлю разницей: {delta_n // 1024} КБ вместо {full_n // 1024} КБ")
                slim = {k: v for k, v in body.items() if k != "sheets"}
                slim["sheets_delta"] = delta
                answer = admin("POST", path, slim, timeout=timeout)
                if isinstance(answer, dict) and answer.get("error") in FALLBACK_ERRORS:
                    log("Сервер не знает базы — шлю целиком")
                    answer = None
    if answer is None:
        answer = admin("POST", path, body, timeout=timeout)
    if (isinstance(grid, list) and isinstance(answer, dict) and not answer.get("error")
            and answer.get("delta_base")):
        try:
            remember(grid, sheet)
        except Exception:  # noqa: BLE001 — память пульта не важнее импорта
            pass
    return answer
