"""Одноразовый бэкфилл индекса фото: строки photo_geo для уже накопленных
снимков. Дату съёмки НЕ выдумывает.

У снимков до вер. 33 метаданных нет и взять их неоткуда: EXIF уничтожен
пережатием на клиенте, имя файла — uuid без времени. Правда о них: mtime
(момент доезда на сервер), sha256 (считается здесь), у камерных — строка
photo_geo с гео, и след audit «кто загружал». Строка заводится с
created_at=mtime и meta_state='backfill' — поиск честно пишет «известна
только дата загрузки».

Безопасен на работающем боте: файлы свежее пяти минут пропускаются — в
web._photo между hard-link и record_photo стоят iterdir и ensure_photos_count
с отдельным connect(), и если бэкфилл успел бы вставить свою строку первым,
она перекрылась бы амендментом (see db.record_photo), но гонку дешевле не
создавать вовсе. Идемпотентен: повторный прогон меняет 0 строк.

CLI: python photo_backfill.py            — dry-run со счётчиками
     python photo_backfill.py --apply    — запись
     python photo_backfill.py --apply --limit 100
"""

from __future__ import annotations

import argparse
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path

import db
import photostore

_SKIP_FRESH_S = 300
_CHUNK = 1024 * 1024
_SUFFIXES = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp",
             ".heic", ".heif", ".avif"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _uploader_from_audit(conn, cdir: str, mtime_iso: str) -> int | None:
    """telegram_id загрузившего — ТОЛЬКО при однозначном совпадении: ровно один
    актор с action='photo' по этому каталогу в окне ±120 с от mtime файла.
    Неоднозначная атрибуция здесь ВЫДАЁТ ДОСТУП (photo_uploaders_for_dirs
    открывает архив по следу «кто снимал») — лучше NULL, чем догадка."""
    try:
        centre = datetime.fromisoformat(mtime_iso)
    except ValueError:
        return None
    lo = (centre - timedelta(seconds=120)).isoformat(timespec="seconds")
    hi = (centre + timedelta(seconds=120)).isoformat(timespec="seconds")
    actors = [r["actor"] for r in conn.execute(
        "SELECT DISTINCT actor FROM audit WHERE action='photo' AND target=? "
        "AND ts BETWEEN ? AND ?", (cdir, lo, hi)) if r["actor"]]
    return actors[0] if len(actors) == 1 else None


def backfill(root: Path | None = None, *, apply: bool = False,
             limit: int | None = None) -> dict:
    root = Path(root or photostore.PHOTO_DIR)
    started = time.time()
    stats = {"scanned": 0, "inserted": 0, "amended": 0, "skipped_fresh": 0,
             "already": 0, "attributed": 0}
    if not root.is_dir():
        return stats
    with db.connect() as conn:
        known = {r["file"]: dict(r) for r in conn.execute(
            "SELECT file, sha256, captured_at, meta_state, meta_origin "
            "FROM photo_geo")}
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            for f in sorted(p for p in d.iterdir() if p.is_file()):
                if f.name.startswith("~") or f.name.endswith(".part"):
                    continue
                if f.suffix.lower() not in _SUFFIXES:
                    continue
                st = f.stat()
                if st.st_mtime > started - _SKIP_FRESH_S:
                    stats["skipped_fresh"] += 1
                    continue
                if limit is not None and \
                        stats["inserted"] + stats["amended"] >= limit:
                    return stats
                stats["scanned"] += 1
                key = photostore.photo_key(f, root=root)
                row = known.get(key)
                if row is None:
                    mtime_iso = datetime.fromtimestamp(
                        st.st_mtime).isoformat(timespec="seconds")
                    tg = _uploader_from_audit(conn, d.name, mtime_iso)
                    if tg:
                        stats["attributed"] += 1
                    stats["inserted"] += 1
                    if apply:
                        conn.execute(
                            "INSERT INTO photo_geo (contract_id, file, "
                            "telegram_id, created_at, sha256, size_bytes, "
                            "meta_state, meta_origin) VALUES (?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(file) DO NOTHING",
                            (d.name, key, tg, mtime_iso, _sha256(f),
                             st.st_size, "backfill", "backfill"))
                elif row["sha256"] is None:
                    # Камерная строка с гео: дозаполняем ТОЛЬКО серверные факты.
                    # meta_state у строк с captured_at не трогаем — иначе на
                    # одной карточке было бы «снято по гео-фиксу» и «известна
                    # только дата загрузки» одновременно.
                    stats["amended"] += 1
                    if apply:
                        if row["captured_at"]:
                            conn.execute(
                                "UPDATE photo_geo SET sha256=?, size_bytes=?, "
                                "meta_origin=COALESCE(meta_origin,'upload') "
                                "WHERE file=? AND sha256 IS NULL",
                                (_sha256(f), st.st_size, key))
                        else:
                            conn.execute(
                                "UPDATE photo_geo SET sha256=?, size_bytes=?, "
                                "meta_origin=COALESCE(meta_origin,'backfill'), "
                                "meta_state=COALESCE(meta_state,'backfill') "
                                "WHERE file=? AND sha256 IS NULL",
                                (_sha256(f), st.st_size, key))
                else:
                    stats["already"] += 1
        if apply:
            conn.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="записать (без флага — dry-run)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    db.init_db()
    stats = backfill(apply=args.apply, limit=args.limit)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] scanned={stats['scanned']} inserted={stats['inserted']} "
          f"amended={stats['amended']} already={stats['already']} "
          f"skipped_fresh={stats['skipped_fresh']} "
          f"attributed={stats['attributed']}")


if __name__ == "__main__":
    main()
