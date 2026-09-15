"""Адрес уточняется сам — по выезду, на котором должника застали.

ЗАЧЕМ. 56 % активного портфеля (сентябрь 2026) геокодированы только до
города, района или области: по таким точкам нельзя ни проверить выезд, ни
построить маршрут, ни посчитать нагрузку. Справочники лучше не станут — в
сельском Таджикистане дома на карте нет ни у кого. Зато выездник у двери был:
телефон стоял там несколько минут, снимок сделан там же, в форме отмечено
«по адресу проживает клиент». Это и есть самая точная координата должника,
какая вообще существует, — и она уже наша.

ПРАВИЛО. Точка берётся, только если сходятся три вещи:
  1) в форме контакт состоялся ПО АДРЕСУ (who_at_address «проживает…» или
     contact_with с самим должником) — «нет контакта» ничему не учит;
  2) телефон дал точные фиксы (≤ 60 м) и простоял в одном месте ≥ 3 минут,
     либо там же снят кадр с координатой;
  3) вердикт подтверждения не «у адреса не был»: если адрес точный, а человек
     стоял в трёх километрах, то это вопрос к выезду, а не к адресу.

ЧТО ПЕРЕЗАПИСЫВАЕМ. Точку без адреса и точки до города/района/области —
всегда. Кишлак, махаллю и джамоат — только при сильной улике (и простоял, и
снял). Улицу, дом и ручную метку выездника — никогда: точнее уже есть.
Каждая правка хранится с тем, что было до неё (addr_candidates), — её можно
понять и откатить.

Модуль сам решает и сам пишет; отдельной кнопки нет — как у геокодинга после
импорта.
"""
from __future__ import annotations

import statistics

import db
import evidence

FOUND_AT_ADDRESS = {
    "По адресу проживает клиент",
    "По адресу проживают родственники клиента",
    "По адресу проживают знакомые клиента",
}
CONTACT_WITH_DEBTOR = {"Должник", "Должник и 3 лицо"}
ACC_MAX_M = 60          # точнее этого телефон в поле не даёт и на улице
CLUSTER_M = 100         # «стоял на месте» — все точные фиксы в круге 100 м
STAY_MIN = 3            # минут на месте
COARSE = {"", "city", "district", "region"}
SOFT = {"jamoat", "village", "mahalla"}
KEEP = {"street", "exact", "house", "visit", "manual"}


def contact_at_address(values: dict) -> bool:
    return (str(values.get("who_at_address") or "") in FOUND_AT_ADDRESS
            or str(values.get("contact_with") or "") in CONTACT_WITH_DEBTOR)


def candidate(samples: list[dict]) -> dict | None:
    """Точка «здесь застали должника» по следам телефона, или None.

    Якорь — последний точный фикс (ближе всех к нажатию «Сохранить выезд»);
    кластер — точные фиксы в 100 м от него. Координата — медиана кластера,
    чтобы один выброс не сдвинул дом."""
    good = [s for s in samples if s.get("lat") is not None
            and 0 < (s.get("acc") or 0) <= ACC_MAX_M]
    if not good:
        return None
    good.sort(key=lambda s: s["ts"])
    anchor = good[-1]
    cluster = [s for s in good
               if evidence.haversine_m(s["lat"], s["lon"], anchor["lat"], anchor["lon"])
               <= CLUSTER_M]
    stay_min = (cluster[-1]["ts"] - cluster[0]["ts"]) / 60000.0 if len(cluster) > 1 else 0.0
    photo = any(s.get("k") == "photo" for s in cluster)
    if stay_min < STAY_MIN and not photo:
        return None
    return {
        "lat": round(statistics.median(s["lat"] for s in cluster), 6),
        "lon": round(statistics.median(s["lon"] for s in cluster), 6),
        "acc_m": int(statistics.median((s.get("acc") or 0) for s in cluster)),
        "stay_min": round(stay_min, 1), "photo": photo, "n": len(cluster),
    }


def consider(row, values: dict, ev: dict | None, samples: list[dict], *,
             employee: str = "", op_id: str | None = None) -> dict | None:
    """Уточнить адрес по этому выезду, если есть чем. Возвращает описание
    решения (для карточки и журнала) или None, если учиться не на чем."""
    if not contact_at_address(values):
        return None
    if ev and ev.get("level") == "far":
        return None
    cand = candidate(samples)
    if not cand:
        return None
    cur_src = str(row["geo_src"] or "").strip().lower()
    cur_prec = str(row["geo_precision"] or "").strip().lower()
    strong = cand["stay_min"] >= STAY_MIN and cand["photo"]
    if cur_src in ("manual", "visit") or cur_prec in KEEP:
        apply, reason = False, "точнее уже есть"
    elif row["geo_lat"] is None:
        apply, reason = True, "точки на карте не было"
    elif cur_prec in COARSE:
        apply, reason = True, f"адрес был известен до уровня «{cur_prec or 'неизвестно'}»"
    elif cur_prec in SOFT:
        apply = strong
        reason = (f"уточнён с уровня «{cur_prec}»: стоял {cand['stay_min']} мин и снимал"
                  if strong else f"уровень «{cur_prec}» — нужны и стоянка, и снимок")
    else:
        apply, reason = False, f"уровень «{cur_prec}» не трогаем"
    prev = {"src": row["geo_src"], "precision": row["geo_precision"],
            "lat": row["geo_lat"], "lon": row["geo_lon"]}
    applied_n = 0
    if apply:
        ids = [c["contract_id"] for c in db.person_contracts(row["contract_id"])]
        applied_n = db.apply_learned_point(ids or [row["contract_id"]],
                                           cand["lat"], cand["lon"])
    db.add_addr_candidate(row["contract_id"], cand["lat"], cand["lon"], cand["acc_m"],
                          employee=employee, visit_op=op_id, reason=reason,
                          prev=prev, applied=bool(apply and applied_n))
    return {"applied": bool(apply and applied_n), "reason": reason,
            "lat": cand["lat"], "lon": cand["lon"], "acc_m": cand["acc_m"],
            "stay_min": cand["stay_min"], "photo": cand["photo"],
            "contracts": applied_n}
