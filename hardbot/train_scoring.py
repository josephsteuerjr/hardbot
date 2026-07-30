"""
Обучение модели ожидаемого сбора по общему реестру.

Запускается НА МАШИНЕ ВЛАДЕЛЬЦА (нужны pandas/openpyxl и сам реестр с реальными ПД),
не на сервере-витрине. Результат — маленький `scoring_model.json` (~5 КБ), только
коэффициенты, без единой персональной строки: его и везём на сервер.

    python train_scoring.py "путь/к/реестру.xlsx"            # обучить и показать отчёт
    python train_scoring.py "реестр.xlsx" --write            # ещё и записать модель

ЧТО СЧИТАЕТСЯ ЦЕЛЬЮ. «Текущие платежи» — единственная колонка, которая сходится
с фактической динамикой долга: корреляция со снижением «Долг на начало месяца» минус
«Долг на сегодня» равна 0.988, медиана расхождения — ноль. Помесячные «Оплаты за MM.YY»
в цель НЕ идут: у них корреляция со снижением долга 0.208, и они противоречат остатку.
Зато как ПРИЗНАК «платил в прошлые месяцы» они работают (лифт ×2.4).

ЧЕГО МОДЕЛЬ НЕ ЗНАЕТ И ЗНАТЬ НЕ ДОЛЖНА. «Долг на сегодня» (он же «долг всего»
в выгрузке) измерен ПОСЛЕ платежа — у заплативших он нулевой. Обучение на нём даёт
красивую, но бессмысленную модель. Учимся на «Долг на начало месяца».
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import scoring_model
from scoring_model import LEAK, features

# Сглаживание: чем меньше группа, тем ближе её лифт к базовому уровню. Без него
# редкое значение с двумя удачными договорами получало бы лифт ×10.
K_P, K_A = 120.0, 40.0
CAP_LO, CAP_HI = 0.25, 4.0        # потолок лифта — защита от переоценки хвостов
MIN_N = 30                        # значения реже этого в модель не кладём

PAY_MONTHS = ("Оплаты за 01.26", "Оплаты за 02.26", "Оплаты за 03.26",
              "Оплаты за 04.26", "Оплаты за 05.26", "Оплаты за 06.26")


def _to_num(v) -> float:
    from util import num
    return num(v)


def load_registry(path: str) -> list[dict]:
    """Реестр (xlsx, шапка на второй строке) → список договоров в виде, который
    понимает `scoring_model.features`."""
    import pandas as pd
    from db import _norm_title

    df = pd.read_excel(path, header=1).iloc[1:]          # 0 — блоки, 2 — «сх/хард»
    df.columns = [str(c).strip() for c in df.columns]
    by = {}
    for c in df.columns:
        by.setdefault(_norm_title(c), c)

    def col(title):
        real = by.get(_norm_title(title))
        if real is None:
            return [""] * len(df)
        s = df[real]
        if hasattr(s, "columns"):
            s = s.iloc[:, 0]
        return ["" if v is None or (isinstance(v, float) and v != v) else str(v).strip()
                for v in s]

    paid_months = [0] * len(df)
    for m in PAY_MONTHS:
        for i, v in enumerate(col(m)):
            if _to_num(v) > 0:
                paid_months[i] += 1

    # Долг ДО периода: «Долг на начало месяца», иначе «при передаче в работу».
    d0, d1 = col("Долг на начало месяца"), col("Долг на момент передачи в работу")
    debt_before = [a if _to_num(a) > 0 else b for a, b in zip(d0, d1)]

    fields = {k: col(t) for k, t in (
        ("product_type", "Тип продукта(только в ЗК)"), ("partner", "Партнер"),
        ("country", "Страна"), ("assign_date", "Дата закрепления"),
        ("kd_date", "Дата оформления КД"), ("promise_date", "дата обещания"),
        ("result", "Общий результат"), ("contact_with", "С кем был контакт?"),
        ("who", "Кто был на адресе?"), ("location", "Местоположение клиента"),
        ("visits", "Кол-во выездов"))}
    target = col("Текущие платежи")

    rows = []
    for i in range(len(df)):
        item = {k: v[i] for k, v in fields.items()}
        item["debt"] = debt_before[i]
        item["paid_months"] = paid_months[i]
        item["_target"] = _to_num(target[i])
        rows.append(item)
    return rows


def fit(rows: list[dict], today: date | None = None) -> dict:
    """Лифты по каждому признаку. Возвращает готовую модель (dict для JSON)."""
    today = today or date.today()
    feats = [features(r, today) for r in rows]
    y_amt = [r["_target"] for r in rows]
    y_bin = [1 if a > 0 else 0 for a in y_amt]

    n = len(rows)
    base_p = sum(y_bin) / n if n else 0.0
    paid = [a for a, b in zip(y_amt, y_bin) if b]
    base_a = sum(paid) / len(paid) if paid else 0.0
    if not base_p or not base_a:
        raise SystemExit("в выборке нет ни одного платежа — учить не на чем")

    lifts: dict[str, dict[str, list]] = {}
    for feature in feats[0]:
        groups = defaultdict(lambda: [0, 0, 0.0])       # n, платежей, сумма платежей
        for f, b, a in zip(feats, y_bin, y_amt):
            g = groups[f[feature]]
            g[0] += 1
            g[1] += b
            g[2] += a if b else 0.0
        out = {}
        for value, (cnt, pays, total) in groups.items():
            if cnt < MIN_N:
                continue                                # редкое значение → нейтрально
            lp = ((pays + K_P * base_p) / (cnt + K_P)) / base_p
            la = (((total + K_A * base_a) / (pays + K_A)) / base_a) if pays else 1.0
            out[value] = [round(min(max(lp, CAP_LO), CAP_HI), 4),
                          round(min(max(la, CAP_LO), CAP_HI), 4), cnt]
        # «Оплачен/рассрочка» — метка, известная только после оплаты. Оставляем её
        # в модели видимой, но принудительно нейтральной, чтобы утечка не вернулась
        # через эту дверь при следующем переобучении.
        if LEAK in out:
            out[LEAK] = [1.0, 1.0, out[LEAK][2]]
        lifts[feature] = out

    model = {"base_p": round(base_p, 6), "base_a": round(base_a, 2),
             "rows": n, "trained_on": today.isoformat(), "lifts": lifts}

    # Калибровка суммы. Лифты перемножаются, а признаки коррелируют — на хвосте
    # ожидание раздувается (верх очереди показывал «≈238 000 ₽» при реальной
    # медиане платежа ~11 тыс). Приводим СУММУ ожиданий к фактически собранному:
    # порядок не меняется, а число становится тем, что можно показать человеку.
    # Считаем ТОЛЬКО по тем договорам, которые реально попадут в очередь: снятые
    # killer-фильтром («Оплачен», «Смерть», «Тюрьма») в неё не входят, а платежей
    # на них приходится много — калибровка по всем завышала очередь почти вдвое.
    probe = scoring_model.Model({**model, "calibration": 1.0})
    scorable = [(r, a) for r, a in zip(rows, y_amt) if not _is_killed(r)]
    predicted = sum(probe.expected(r, today) for r, _a in scorable)
    actual = sum(a for _r, a in scorable)
    model["calibration"] = round(actual / predicted, 6) if predicted > 0 else 1.0
    return model


def _is_killed(row: dict) -> bool:
    """Договор, который скоринг в очередь не пустит (зеркало killer-фильтров)."""
    from scoring_config import KILLER_RESULTS
    res = str(row.get("result") or "").strip().lower()
    return bool(res) and ("не существ" in res or res in KILLER_RESULTS)


def evaluate(model: dict, rows: list[dict], today: date | None = None) -> list[tuple]:
    """Сколько сбора собирается в верхней части очереди. [(доля, сбор, лифт)]."""
    m = scoring_model.Model(model)
    scored = sorted(((m.expected(r, today), r["_target"]) for r in rows),
                    key=lambda t: -t[0])
    total = sum(t for _s, t in scored)
    out = []
    for q in (0.05, 0.10, 0.20, 0.30, 0.50):
        k = max(1, int(len(scored) * q))
        got = sum(t for _s, t in scored[:k])
        out.append((q, got, (got / total / q) if total else 0.0))
    return out


def _split(rows: list[dict], ratio: float = 0.7):
    """Детерминированное разбиение: обучаемся на одних, меряем на других."""
    train, test = [], []
    for i, r in enumerate(rows):
        (train if (i * 2654435761) % 1000 < ratio * 1000 else test).append(r)
    return train, test


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    write = "--write" in argv
    rows = load_registry(path)
    print(f"договоров: {len(rows)}")

    train, test = _split(rows)
    holdout = fit(train)
    print(f"обучение {len(train)}, проверка {len(test)}")
    print(f"база: P(оплата)={holdout['base_p']:.4f}  сумма={holdout['base_a']:,.0f}"
          .replace(",", " "))
    print("\n  верх очереди     доля сбора    лифт")
    for q, got, lift in evaluate(holdout, test):
        print(f"  топ-{q*100:>4.0f}%        {got:>14,.0f}  {lift:>6.2f}x".replace(",", " "))

    full = fit(rows)                       # финальная модель — на всех данных
    print(f"\nпризнаков: {len(full['lifts'])}, "
          f"значений: {sum(len(v) for v in full['lifts'].values())}")
    if write:
        scoring_model.MODEL_PATH.write_text(
            json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"записано: {scoring_model.MODEL_PATH}")
    else:
        print("(запуск без --write: модель не сохранена)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
