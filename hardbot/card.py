"""Карточка человека (договоры объединены) — локализованная, с балансом инфы."""

from aiogram.types import CallbackQuery

import db
import debt
from i18n import t, tv
from keyboards import card_kb


def render_person(rep_cid: str, lang: str) -> str | None:
    rows = db.person_contracts(rep_cid)
    if not rows:
        return None
    main = rows[0]
    partners = sorted({r["partner"] for r in rows if r["partner"]})
    unit = debt.unit(partners)
    total = sum(debt.contract_debt(r) for r in rows)
    # У человека с договорами ЗК (сумы) и Делимобиля (рубли) общей суммы нет —
    # показываем раздельно, а не одним бессмысленным числом без валюты.
    totals = debt.totals_by_unit(rows)
    total_s = " · ".join(debt.fmt(x["sum"], x["unit"]) for x in totals) or debt.fmt(total, unit)
    photos = sum(r["photos_count"] or 0 for r in rows)

    head = t("card_visited", lang) if main["visit_date"] else t("card_not_visited", lang)
    status = tv(main["overall_result"] or main["call_status"], lang)

    def first(field):
        return next((r[field] for r in rows if r[field]), None)

    lines = [
        f"{head} · {t('card_photos', lang, n=photos)}",
        "",
        f"👤 <b>{main['client']}</b>",
    ]
    if main["birth_date"]:
        lines.append(f"{t('card_birth', lang)}: {main['birth_date']}")
    lines += [
        f"{t('card_phone', lang)}: {first('phone') or '—'}",
        f"{t('card_phone2', lang)}: {first('phone_main') or '—'}",
        f"{t('card_address', lang)}: {first('address') or '—'}",
        "———————————",
        f"{t('card_total', lang)}: <b>{total_s}</b>  ·  {len(rows)} КД",
    ]
    # перечень договоров
    lines.append(f"<i>{t('card_contracts', lang)}:</i>")
    for r in rows:
        d = debt.contract_debt(r)
        lines.append(f"  ▫️ {r['partner'] or '—'} · {r['contract_id']} · "
                     f"{debt.fmt(d, debt.unit([r['partner']] if r['partner'] else []))}")
    lines += ["———————————", f"{t('card_status', lang)}: <b>{status}</b>"]

    if first("agreed_payment"):
        lines.append(f"{t('card_promise', lang)}: {tv(first('agreed_payment'), lang)}")
    if first("last_payment_sum"):
        lp = first("last_payment_sum")
        lpd = first("last_payment_date")
        lines.append(f"{t('card_last_pay', lang)}: {lp}" + (f" ({lpd})" if lpd else ""))

    if main["visit_date"]:
        details = [tv(main["call_status"], lang) if main["call_status"] else None,
                   tv(main["who_at_address"], lang) if main["who_at_address"] else None,
                   tv(main["client_location"], lang) if main["client_location"] else None,
                   main["visit_comment"] or main["call_comment"]]
        lines += ["", f"<i>{t('card_last_visit', lang)}: {main['visit_date']}</i>"]
        lines += [f"  ▫️ {v}" for v in details if v]
    if first("reminder_date"):
        lines += ["", f"{t('card_replan', lang)}: <b>{first('reminder_date')}</b>"]
    return "\n".join(lines)


async def show_card(target, rep_cid: str, lang: str):
    text = render_person(rep_cid, lang)
    msg = target.message if isinstance(target, CallbackQuery) else target
    if not text:
        await msg.answer("—")
        return
    kb = card_kb(rep_cid, lang)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)
