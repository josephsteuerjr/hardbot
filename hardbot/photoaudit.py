"""Разбор фото по договору: даты снимков и СИГНАЛЫ К РАЗБОРУ.

Чистый счёт: НИКАКИХ обращений к БД — все данные приходят аргументами, поэтому
модуль физически не может расширить доступ в обход авторизации (web.py). Из
проекта импортируется только формула расстояния.

Нуль сигналов ≠ «чисто»; отсутствие метаданных ≠ нарушение. Слова «нарушение»
в текстах нет вообще: каждый сигнал — повод посмотреть, с обязательным
невиновным объяснением там, где оно есть. Все клиентские метки (EXIF, время
нажатия, гео) — данные с телефона подозреваемого, их можно сдвинуть;
неподделываемых ровно две: created_at (доезд на сервер) и sha256 содержимого.

Уровни: info (заметка) | review (стоит посмотреть). Никаких других.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from routing_client import haversine_km

# У EXIF-точки нет поля погрешности, а камеры андроида штатно пишут последнюю
# известную точку — консервативная константа вместо несуществующего acc.
EXIF_UNKNOWN_ACC_M = 1000
# EXIF-GPS, снятый сильно раньше/позже самого снимка, — кэш чипа, а не место
# съёмки: в дистанцию такой фикс не пускаем.
GPS_VS_SHOT_MAX_S = 600


def _dist_m(a: tuple, b: tuple) -> float:
    """haversine_km отдаёт КИЛОМЕТРЫ (routing_client.py), а все пороги проекта
    — в метрах. Без множителя оба гео-правила молча не срабатывали бы никогда."""
    return haversine_km(a, b) * 1000.0


def thresholds(get_setting) -> dict:
    """Пороги из settings с дефолтами (get_setting передаётся функцией —
    модуль сам в БД не ходит)."""
    def _int(key, default):
        try:
            return int(get_setting(key) or default)
        except (TypeError, ValueError):
            return default
    return {"stale_h": _int("photo_stale_h", 24),
            "skew_s": _int("photo_skew_s", 300),
            "gap_m": _int("photo_gap_m", 500)}


def _parse_iso(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:19])
    except ValueError:
        return None


def shot_time(row: dict) -> tuple[str | None, str, str]:
    """(iso, kind, trust). ПРАВИЛО ПРИОРИТЕТА ДАТ — одно на систему.

    В арифметику попадает только то, что лежит в ОДНОЙ шкале с created_at:
      1 exif_dt_norm (EXIF, приведённый по зоне)  kind='exif'   trust='device'
      2 shot_at_client при source=='camera'       kind='client' trust='device'
      3 captured_at (гео-фикс) — ТОЛЬКО у строк meta_origin='upload' и строго
        раньше created_at (иначе это фолбэк «or now» или момент досыла
        из очереди)                               kind='geo'    trust='device'
      4 created_at                                kind='upload' trust='server'

    Сырой exif_dt (стенные часы телефона, зона неизвестна) в арифметике НЕ
    участвует — сервер живёт в UTC, у выездника в UZ/TJ разрыв +5 ч, и любой
    честный снимок выглядел бы снятым в будущем. Он отдаётся отдельным полем
    shot_at_device вместе с shot_tz — только для показа.

    Галерейному снимку без EXIF время съёмки НЕИЗВЕСТНО: его shot_at_client —
    момент прикрепления, а не съёмки (та же ловушка file.lastModified, из-за
    которой камера и галерея различаются кнопкой)."""
    def g(k):
        try:
            return row[k]
        except (KeyError, IndexError):
            return None
    if g("exif_dt_norm"):
        return g("exif_dt_norm"), "exif", "device"
    if g("shot_at_client") and (g("source") or "") == "camera":
        return g("shot_at_client"), "client", "device"
    cap, cre = _parse_iso(g("captured_at")), _parse_iso(g("created_at"))
    if (g("meta_origin") or "") == "upload" and cap and cre and cap < cre:
        return g("captured_at"), "geo", "device"
    return g("created_at"), "upload", "server"


def _sig(code, level, text, **extra):
    return {"code": code, "level": level, "text": text, **extra}


_META_TEXTS = {
    None: "оболочка сотрудника старше вер. 33 — метаданные не передавались; "
          "обновление добровольное, это не признак сокрытия",
    "no_row": "файл на диске есть, строки индекса нет (фото до бэкфилла или "
              "сбой записи)",
    "absent": "оболочка умеет читать метаданные, но в этом файле их нет",
    "truncated": "метаданные файла оборваны — разобрать не удалось",
    "error": "метаданные не разобрались",
    "oversize": "метаданные не поместились в передачу",
    "backfill": "фото до внедрения индекса — известна только дата загрузки",
}


def _meta_signal(state, shrunk, shot_kind):
    if state == "heic":
        text = ("HEIC: пережато на устройстве, метаданных больше нет"
                if shrunk else
                "HEIC: оригинал на диске, разбор метаданных возможен позже")
        return _sig("meta_unavailable", "info", text)
    if state == "backfill" and shot_kind != "upload":
        return None    # у камерного бэкфилла время гео-фикса известно — не пугаем
    if state in _META_TEXTS or state is None:
        return _sig("meta_unavailable", "info", _META_TEXTS.get(state) or
                    _META_TEXTS["error"])
    return None


def build(ctx: dict, rows, files: dict, *, visits, thresholds,
          date_from: str = "", date_to: str = "", dup_rows=()) -> dict:
    """Склейка диска и индекса + сигналы.

    ctx — из db.photo_contract_dirs; rows — строки photo_geo; files —
    {ключ: Path}; visits — строки visits по договорам человека; dup_rows —
    строки photo_geo с теми же sha256 (могут быть чужих КД — маскирует web).
    Фильтр date_from/date_to — ПО uploaded_at: это единственная
    неподделываемая дата; shot_at отдаётся рядом, сортирует клиент."""
    by_file = {r["file"]: r for r in rows}
    keys = sorted(set(files) | set(by_file), reverse=True)
    contracts_by_id = {c["contract_id"]: c for c in ctx.get("contracts", [])}
    person_dirs = set(ctx.get("dirs") or [])

    # sha256 → строки (для дедупа), включая чужие КД
    sha_map: dict[str, list] = {}
    for r in dup_rows or ():
        if r["sha256"]:
            sha_map.setdefault(r["sha256"], []).append(r)

    items, unknown_meta, warnings = [], 0, []
    for key in keys:
        row = by_file.get(key)
        path = files.get(key)
        cdir = key.split("/", 1)[0]

        def g(k, default=None):
            if row is None:
                return default
            try:
                return row[k]
            except (KeyError, IndexError):
                return default

        uploaded_at = g("created_at")
        if row is None and path is not None:
            try:
                uploaded_at = datetime.fromtimestamp(
                    path.stat().st_mtime).isoformat(timespec="seconds")
            except OSError:
                uploaded_at = None
        up_day = (uploaded_at or "")[:10]
        if date_from and up_day and up_day < date_from:
            continue
        if date_to and up_day and up_day > date_to:
            continue

        state = "no_row" if row is None else g("meta_state")
        shot_iso, shot_kind, shot_trust = (uploaded_at, "upload", "server") \
            if row is None else shot_time(row)
        if state in (None, "no_row", "heic", "absent", "truncated", "error",
                     "oversize", "backfill"):
            unknown_meta += 1

        item = {
            "file": key, "contract_dir": cdir,
            "missing": path is None,
            "size_bytes": g("size_bytes"),
            "sha256": g("sha256"),
            "uploaded_at": uploaded_at,
            "shot_at": shot_iso, "shot_at_kind": shot_kind,
            "shot_at_trust": shot_trust,
            "shot_at_device": g("exif_dt"), "shot_tz": g("exif_tz"),
            "attached_at": g("shot_at_client")
            if (g("source") or "") != "camera" else None,
            "source": g("source"), "meta_state": state,
            "meta_origin": g("meta_origin"),
            "shell_version": g("shell_version"),
            "device": " ".join(x for x in (g("exif_make"), g("exif_model"))
                               if x) or None,
            "software": g("exif_software"),
            "geo": ({"lat": g("lat"), "lon": g("lon"), "acc_m": g("acc_m"),
                     "at": g("captured_at")} if g("lat") is not None else None),
            "exif_geo": ({"lat": g("exif_lat"), "lon": g("exif_lon"),
                          "at": g("exif_gps_at")}
                         if g("exif_lat") is not None else None),
            "photographer_id": g("telegram_id"),
            "visit": None,
            "signals": [],
        }
        sig = item["signals"]

        # якорный визит: ближайший по человеку в пределах ±36 ч от загрузки
        anchor = None
        up_dt = _parse_iso(uploaded_at)
        if up_dt and visits:
            best = None
            for v in visits:
                v_dt = _parse_iso(v["created_at"])
                if not v_dt:
                    continue
                delta = abs((up_dt - v_dt).total_seconds())
                if delta <= 36 * 3600 and (best is None or delta < best[0]):
                    best = (delta, v)
            if best is not None:
                v = best[1]
                item["visit"] = {"at": v["created_at"],
                                 "dist_m": v["dist_m"],
                                 "geo_far": v["geo_far"]}
                anchor = _parse_iso(v["created_at"])

        shot_dt = _parse_iso(shot_iso)
        device_shot = shot_dt if shot_trust == "device" else None

        # 1. Снято до закрепления кейса — единственное правило без конфаундов.
        # Дата закрепления — СНИМОК на момент загрузки (addr-блок), уже
        # разобранный _as_date; NULL = не распознана, правило не применяется.
        assign_at = g("assign_date_at")
        if device_shot and assign_at and shot_kind in ("exif", "client"):
            margin = timedelta(hours=14) if (
                shot_kind == "exif" and not g("exif_dt_norm")) else timedelta()
            try:
                a_day = date.fromisoformat(str(assign_at)[:10])
                if device_shot + margin < datetime(a_day.year, a_day.month,
                                                   a_day.day):
                    sig.append(_sig(
                        "shot_before_assignment", "review",
                        "снимок сделан до того, как договор закрепили за "
                        "сотрудником — доказательством этого выезда быть "
                        "не может"))
            except ValueError:
                pass

        # 2. Снято сильно раньше визита/загрузки
        if device_shot:
            ref = anchor or up_dt
            if ref and (ref - device_shot).total_seconds() > \
                    thresholds["stale_h"] * 3600 and (anchor or up_dt is not None):
                if anchor is not None:   # без визита сравнение вырождается
                    sig.append(_sig(
                        "shot_long_before_anchor",
                        "info" if (g("source") or "") == "camera" else "review",
                        f"снимок старше визита больше чем на "
                        f"{thresholds['stale_h']} ч — либо снят заранее, либо "
                        "телефон был долго без сети (офлайн-очередь досылает "
                        "позже)"))

        # 3. Галерея — факт, не обвинение
        if (g("source") or "") == "gallery":
            sig.append(_sig(
                "source_gallery", "info",
                "приложено из галереи; место и время съёмки приложением не "
                "подтверждались (галерейный вход заведён по просьбе из поля)"))

        # 4-5. Дедуп по содержимому
        sha = g("sha256")
        if sha and sha in sha_map:
            for other in sha_map[sha]:
                if other["file"] == key:
                    continue
                other_dir = (other["file"] or "").split("/", 1)[0]
                if other_dir == cdir:
                    sig.append(_sig(
                        "same_file_same_contract", "info",
                        "байт-в-байт этот же файл уже приложен к этому "
                        "договору (повторная загрузка, не пересъёмка)",
                        other_file=other["file"]))
                else:
                    same_person = other_dir in person_dirs
                    sig.append(_sig(
                        "same_file_other_contract",
                        "info" if same_person else "review",
                        "тот же файл приложен к другому договору"
                        + (" того же человека" if same_person else ""),
                        other_contract=other_dir,
                        other_uploaded_at=other["created_at"],
                        same_person=same_person))

        # 6. EXIF-GPS далеко от адреса должника. Допуск — СНИМОК на момент
        # загрузки (addr_tol_m); NULL = точка не адресная, сигнала нет.
        exif_geo = item["exif_geo"]
        tol = g("addr_tol_m")
        a_lat, a_lon = g("addr_lat"), g("addr_lon")
        gps_fresh = True
        if exif_geo and exif_geo.get("at") and device_shot:
            gps_dt = _parse_iso(str(exif_geo["at"]).rstrip("Z"))
            if gps_dt and abs((gps_dt - device_shot).total_seconds()) > \
                    GPS_VS_SHOT_MAX_S:
                gps_fresh = False    # кэшированный фикс — не место съёмки
        if exif_geo and tol is not None and a_lat is not None and gps_fresh:
            d = _dist_m((exif_geo["lat"], exif_geo["lon"]), (a_lat, a_lon))
            if d > tol + EXIF_UNKNOWN_ACC_M:
                sig.append(_sig(
                    "exif_gps_far", "info",
                    f"точка из метаданных снимка в {round(d)} м от адреса "
                    "должника (у EXIF-точки нет погрешности — допуск взят "
                    "с запасом)", dist_m=round(d)))

        # 7. EXIF-GPS против точки телефона
        geo = item["geo"]
        if exif_geo and geo and geo.get("lat") is not None and gps_fresh:
            d = _dist_m((exif_geo["lat"], exif_geo["lon"]),
                        (geo["lat"], geo["lon"]))
            if d > (geo.get("acc_m") or 0) + thresholds["gap_m"] + \
                    EXIF_UNKNOWN_ACC_M:
                sig.append(_sig(
                    "exif_gps_vs_device", "review",
                    f"место в метаданных файла расходится с точкой телефона "
                    f"на {round(d)} м", dist_m=round(d)))

        # 8. Часы вперёд — ТОЛЬКО по меткам в серверной шкале (epoch-времени
        # клиента и нормализованному EXIF); сырой exif_dt без зоны дал бы
        # плашку на каждом честном снимке из Узбекистана (+5 ч).
        ahead_src = None
        if g("exif_dt_norm"):
            ahead_src = _parse_iso(g("exif_dt_norm"))
        elif g("shot_at_client") and (g("source") or "") == "camera":
            ahead_src = _parse_iso(g("shot_at_client"))
        if ahead_src and up_dt and \
                (ahead_src - up_dt).total_seconds() > thresholds["skew_s"]:
            sig.append(_sig(
                "clock_ahead", "review",
                "время съёмки впереди времени загрузки — часы телефона "
                "переведены вперёд"))

        # 9. Камера без точки
        if (g("source") or "") == "camera" and item["geo"] is None:
            sig.append(_sig(
                "camera_without_point", "info",
                "снято камерой, но без гео-точки — скорее всего, геолокация "
                "запрещена в настройках"))

        # 10. Метаданных нет — явно НЕ нарушение
        ms = _meta_signal(state, g("shrunk"), shot_kind)
        if ms:
            sig.append(ms)

        items.append(item)

    return {"contract_id": ctx.get("cid"), "person": ctx.get("person"),
            "dirs": ctx.get("dirs") or [], "items": items,
            "unknown_meta": unknown_meta, "warnings": warnings}
