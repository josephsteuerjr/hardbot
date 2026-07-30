"""
Модель ожидаемого сбора: сколько денег принесёт выезд к этому должнику.

ЧТО ИЗМЕНИЛОСЬ ПО СУТИ. Прежний скоринг складывал баллы за долг, кластер и свежесть
с весами, выставленными вручную. Здесь считается ДЕНЬГИ: ожидаемый сбор с договора

    E = P(заплатит) × сумма(если заплатит)

где обе части — произведение сглаженных лифтов по признакам, посчитанных на реальном
портфеле (17 075 договоров). Модель наивно-байесовская и мультипликативная — выбор
осознанный: она читается человеком («в РФ ×2.2, контакта нет ×0.28»), не тянет в бота
ни одной зависимости и её можно показать директору строкой, а не графиком.

ПРОВЕРКА НА ОТЛОЖЕННОЙ ВЫБОРКЕ (30% договоров, модель их не видела):
    верх очереди     новая модель      порядок по долгу (как было)
    топ-10%          61.4% сбора       31.3%
    топ-20%          73.2%             48.0%

ДВЕ УТЕЧКИ, которые пришлось выловить при обучении — и почему это важно помнить:
  • «долг всего» в выгрузке совпадает с «Долг на сегодня» (17 073 строки из 17 075),
    то есть это долг ПОСЛЕ платежа: у заплативших он равен нулю. Модель, обученная
    на нём, выучивала тавтологию «долг ноль ⟹ заплатил». Учим на долге ДО периода.
    В бою же текущий долг — это и есть долг до выезда, поэтому применять к нему можно.
  • «Общий результат» = «Оплачен»/«Рассрочка» проставляют ПОСЛЕ оплаты. Просто
    вычеркнуть их мало: утечка переезжает в группу пустых значений (проверено —
    она сразу дала ×3.9). Такие договоры помечаются отдельным значением, которое
    принудительно нейтрально.

Признаки извлекаются ЗДЕСЬ, и обучатель (`train_scoring.py`) зовёт эту же функцию.
Иначе неизбежно расхождение между тем, на чём учились, и тем, что считаем в бою.

Коэффициенты — в `scoring_model.json` рядом с модулем. Нет файла → все лифты
нейтральны, и скоринг просто вырождается в «долг × базовая вероятность»: бот
продолжает работать, а не падает.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

from util import num as _num

MODEL_PATH = Path(os.getenv("SCORING_MODEL",
                            Path(__file__).with_name("scoring_model.json")))

# Долг в валюте портфеля. Границы подобраны по реальному распределению: медиана
# ~15 тыс, 90-й процентиль ~110 тыс, дальше длинный хвост до 2.6 млн.
DEBT_EDGES = ((5_000, "<5к"), (20_000, "5-20к"), (50_000, "20-50к"),
              (100_000, "50-100к"), (300_000, "100-300к"), (float("inf"), ">300к"))
WORK_EDGES = ((7, "<7д"), (30, "7-30д"), (90, "30-90д"), (float("inf"), ">90д"))
AGE_EDGES = ((1.5, "<1.5г"), (2, "1.5-2г"), (3, "2-3г"), (float("inf"), ">3г"))

MISSING = "—"
LEAK = "__после_оплаты__"          # результат, известный только после оплаты


def _bucket(value, edges) -> str:
    if value is None:
        return MISSING
    for limit, label in edges:
        if value < limit:
            return label
    return edges[-1][1]


def _norm(v) -> str:
    s = str(v or "").strip().lower()
    return s or MISSING


def _parse_date(v) -> date | None:
    if isinstance(v, date):
        return v
    s = str(v or "").strip()[:10]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _days_since(v, today: date) -> float | None:
    d = _parse_date(v)
    return None if d is None else (today - d).days


def features(item: dict, today: date | None = None) -> dict[str, str]:
    """Договор → значения признаков модели. ОДНА функция для обучения и для боя."""
    today = today or date.today()
    res = _norm(item.get("result"))
    if "оплачен" in res or "рассрочка" in res:
        res = LEAK

    paid_months = _num(item.get("paid_months"))          # платил в прошлые месяцы
    age_days = _days_since(item.get("kd_date"), today)
    work_days = _days_since(item.get("assign_date"), today)

    return {
        "debt": _bucket(_num(item.get("debt")) or None, DEBT_EDGES),
        "product": _norm(item.get("product_type")),
        "partner": _norm(item.get("partner")),
        "country": _norm(item.get("country")),
        "paid_before": ("нет" if paid_months <= 0
                        else ("1-2 мес" if paid_months <= 2 else "3+ мес")),
        "days_work": _bucket(work_days, WORK_EDGES),
        "age_kd": _bucket(None if age_days is None else age_days / 365.25, AGE_EDGES),
        "promise": "есть" if _parse_date(item.get("promise_date")) else "нет",
        "result": res,
        "contact": _norm(item.get("contact_with")),
        "who": _norm(item.get("who")),
        "location": _norm(item.get("location")),
        "visits": ("0" if _num(item.get("visits")) <= 0
                   else ("1" if _num(item.get("visits")) == 1 else "2+")),
    }


class Model:
    """Лифты по признакам + базовые вероятность и сумма."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.base_p = float(data.get("base_p") or 0.08)
        self.base_a = float(data.get("base_a") or 20_000.0)
        self.lifts: dict[str, dict[str, list]] = data.get("lifts") or {}
        self.trained_on = data.get("trained_on") or ""
        self.rows = int(data.get("rows") or 0)
        # Калибровка. Модель мультипликативная, признаки коррелируют (крупный долг
        # + ДМ + контакт с должником — это часто один и тот же договор), поэтому
        # на хвосте лифты перемножаются и сумма улетает: без поправки верх очереди
        # показывал «≈238 000 ₽». Порядок от этого верен, а число — нет, и именно
        # число видит выездник. Коэффициент подобран так, чтобы СУММА ожиданий по
        # портфелю сходилась с фактически собранным.
        self.calibration = float(data.get("calibration") or 1.0)

    @property
    def is_trained(self) -> bool:
        return bool(self.lifts)

    def _pair(self, feature: str, value: str) -> tuple[float, float]:
        """(лифт вероятности, лифт суммы). Незнакомое значение — нейтрально:
        новый статус в реестре не должен ни возносить, ни хоронить договор."""
        got = (self.lifts.get(feature) or {}).get(value)
        if not got:
            return 1.0, 1.0
        return float(got[0]), float(got[1])

    def expected(self, item: dict, today: date | None = None) -> float:
        """Ожидаемый сбор с договора, в валюте портфеля."""
        p, a = self.base_p, self.base_a
        for feature, value in features(item, today).items():
            lp, la = self._pair(feature, value)
            p *= lp
            a *= la
        value = max(0.0, min(0.95, p)) * max(0.0, a) * self.calibration
        # Больше долга не соберёшь. Ограничение доменное, а не подгонка: без него
        # мультипликативная модель на хвосте выдавала «≈172 000 ₽» там, где человек
        # должен 40 000. Порядок очереди это почти не трогает, а число становится
        # тем, что не стыдно показать выезднику.
        debt = _num(item.get("debt"))
        return min(value, debt) if debt > 0 else value

    def contributions(self, item: dict, today: date | None = None) -> list[tuple[str, str, float]]:
        """[(признак, значение, во сколько раз меняет ожидание)], сильные — первыми."""
        out = []
        for feature, value in features(item, today).items():
            lp, la = self._pair(feature, value)
            if abs(lp * la - 1.0) > 0.02:
                out.append((feature, value, lp * la))
        out.sort(key=lambda t: -abs(1.0 - t[2]))
        return out


_LABEL = {
    "debt": "долг", "product": "продукт", "partner": "партнёр", "country": "страна",
    "paid_before": "платил раньше", "days_work": "в работе", "age_kd": "возраст КД",
    "promise": "обещание", "result": "результат", "contact": "контакт",
    "who": "на адресе", "location": "местоположение", "visits": "выездов",
}


def explain(model: "Model", item: dict, top: int = 3, today: date | None = None) -> list[str]:
    """Короткие человекочитаемые причины: «в РФ ×2.2», «контакта нет ×0.3»."""
    out = []
    for feature, value, mult in model.contributions(item, today)[:top]:
        name = value if value != MISSING else _LABEL.get(feature, feature)
        out.append(f"{name[:24]} ×{mult:.1f}")
    return out


_CACHE: Model | None = None


def load(path: Path | None = None) -> Model:
    """Прочитать модель с диска. Отсутствие файла — не ошибка: бот обязан работать
    и без обученной модели (лифты нейтральны), иначе один потерянный файл
    останавливает выезды."""
    global _CACHE
    if _CACHE is not None and path is None:
        return _CACHE
    p = path or MODEL_PATH
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    model = Model(data)
    if path is None:
        _CACHE = model
    return model


def reset_cache() -> None:
    global _CACHE
    _CACHE = None
