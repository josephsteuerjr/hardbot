"""
Воронка Collection: Penetration → Contact Rate → RPC → PTP → KP/BP.

Просьба Альберта (15.09.2026): «данная воронка показывает полную конверсию, от
выезда до транзакции». Пять показателей, каждый со своим знаменателем, — то
есть не пять независимых колонок, а лестница, где числитель одной ступени
становится знаменателем следующей:

    задание  →  выезды  →  контакты  →  контакты с клиентом  →  обещания  →  оплата
             Penetration  Contact Rate         RPC                PTP          KP / BP

Модуль ЧИСТЫЙ, как debt.py: ни БД, ни i18n, ни разметки. На вход — значения
полей карточки выезда, на выход — классы. Считать проценты и складывать строки
будет digest_data, рисовать — книга. Так правило «кто такой контакт» живёт в
одном месте и проверяется тестом без базы.

Откуда берутся классы. Все три поля — выпадающие списки реестра (fields.py,
лист «Статусы»), выездник выбирает из готового, свободного ввода там нет.
Поэтому классификация — это сопоставление литералу, а не разбор текста; литерал
сверен с fields.DROPDOWNS при импорте модуля (см. _assert_known внизу).

Два решения, которые стоит знать читателю отчёта:

  * RPC считается СТРОГО по «С кем был контакт?»: должник или должник вместе с
    третьим лицом. Родственник, у которого есть влияние на должника, в RPC не
    входит — он виден отдельной колонкой «в т.ч. родственники». Смешать их
    значило бы решить за офис, что разговор с матерью должника равен разговору
    с самим должником, а Альберт просил как раз разделить эти объёмы.

  * Числитель PTP берётся ВНУТРИ RPC: обещание клиента, полученное при контакте
    с клиентом. Обещание третьего лица («жена заплатит») считается отдельно и в
    PTP не идёт — иначе показатель мог бы перевалить за 100 %, а знаменатель у
    него по определению Альберта именно RPC.
"""

from __future__ import annotations

from datetime import date

# --- «С кем был контакт?» (реестр, колонка BA) -------------------------------
CONTACT_CLIENT = ("Должник", "Должник и 3 лицо")
CONTACT_THIRD = ("3 лицо",)
CONTACT_NONE = ("Контакт не установлен/ должника не знают",)

# --- «Договорились на оплату?» (колонка BB) ----------------------------------
PROMISE_CLIENT = ("Обещает оплатить клиент", "Обещает оплатить клиент и 3-е лицо")
PROMISE_THIRD = ("Обещает оплатить 3-е лицо",)
PROMISE_NONE = ("На оплату договориться не удалось",
                "3-е лицо передаст информацию клиенту")

# --- «Кто был на адресе?» (колонка BC) ---------------------------------------
# Нужен ровно для одного: отделить родственников и знакомых («есть влияние на
# должника») от адреса, где должника не знают вовсе, — это и есть «мёртвый
# адрес», объём которого Альберт назвал целью Contact Rate.
AT_ADDRESS_KNOWN = ("По адресу проживает клиент",
                    "По адресу проживают родственники клиента",
                    "По адресу проживают знакомые клиента")
AT_ADDRESS_RELATIVES = ("По адресу проживают родственники клиента",)
AT_ADDRESS_STRANGER = ("По адресу клиента не знают/ не общаются",)
AT_ADDRESS_UNKNOWN = ("Не удалось установить кто проживает по адресу",)


def contact_kind(value) -> str:
    """client / third / none / '' — с кем состоялся контакт на выезде.

    Пустое значение — это не «контакта не было», а «поле не заполнено»: старые
    выезды снимка не сохраняли. Такие выезды не попадают ни в числитель, ни в
    знаменатель Contact Rate, а считаются отдельно (см. digest_data: «без
    анкеты»), иначе незаполненное поле молча улучшало бы показатель.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if text in CONTACT_CLIENT:
        return "client"
    if text in CONTACT_THIRD:
        return "third"
    if text in CONTACT_NONE:
        return "none"
    return ""


def promise_kind(value) -> str:
    """client / third / none / '' — чьё обещание оплаты получено на выезде."""
    text = (value or "").strip()
    if not text:
        return ""
    if text in PROMISE_CLIENT:
        return "client"
    if text in PROMISE_THIRD:
        return "third"
    if text in PROMISE_NONE:
        return "none"
    return ""


def address_kind(value) -> str:
    """client / relatives / friends / stranger / unknown / '' — кто на адресе."""
    text = (value or "").strip()
    if not text:
        return ""
    if text == "По адресу проживает клиент":
        return "client"
    if text in AT_ADDRESS_RELATIVES:
        return "relatives"
    if text == "По адресу проживают знакомые клиента":
        return "friends"
    if text in AT_ADDRESS_STRANGER:
        return "stranger"
    if text in AT_ADDRESS_UNKNOWN:
        return "unknown"
    return ""


# --- Судьба обещания ---------------------------------------------------------
PROMISE_KEPT = "kept"
PROMISE_BROKEN = "broken"
PROMISE_PENDING = "pending"
PROMISE_NODATE = "nodate"

PROMISE_STATE_TEXT = {PROMISE_KEPT: "исполнено", PROMISE_BROKEN: "сорвано",
                      PROMISE_PENDING: "ждём срока", PROMISE_NODATE: "без даты"}


def first_visit_day(days) -> date | None:
    """Самый ранний выезд к должнику за окно — граница «своего» обещания.

    Обещание принадлежит ДОЛЖНИКУ, а не поездке: к одному человеку ездят по
    нескольку раз, и обещание, данное на первом выезде, живёт дальше. Поэтому
    граница «чужой даты» одна на должника и берётся по ПЕРВОМУ выезду к нему, а
    не по каждому выезду отдельно. Пустые дни пропускаем: выезд без даты не
    должен делать границу отсутствующей.

    Дни сюда подаются за ВСЮ историю выездов к должнику, а не за окно отчёта:
    окно — свойство отчёта, а исход обещания должен быть одинаков и в книге за
    30 дней, и в вечерней книге за один день (см. db.first_visit_days).
    """
    real = [d for d in (days or ()) if d is not None]
    return min(real) if real else None


def promise_state(promise_day: date | None, *, paid: bool,
                  last_payment_day: date | None, on: date,
                  first_visit_day: date | None = None,
                  visit_day: date | None = None) -> str:
    """kept / broken / pending / nodate — что стало с обещанием.

    Правило исполнения — РОВНО то же, что у «Просрочено обещаний» в сводке
    (db.overdue_promises): кейс «Оплачен» или платёж не раньше даты обещания
    значит «сдержал», иначе после наступления срока — «сорвал». Два показателя,
    считающие одно и то же по-разному, поссорили бы книгу с приложением.

    `on` — день, НА КОТОРЫЙ судим. Для книги это сегодня, а не конец периода:
    обещание, данное 3-го на 10-е, до 10-го числа не исполнено и не сорвано, и
    вопрос Альберта «с какими долгами умеем работать» требует настоящего исхода,
    а не того, что было видно на закрытии месяца.

    `first_visit_day` защищает от чужой даты: колонку «дата обещания» заполняет
    и офис по телефону, и в реестре может лежать обещание месячной давности.
    Обещание, датированное раньше ПЕРВОГО выезда к должнику, не относится ни к
    одному из его выездов — это nodate, и в книге оно видно отдельной колонкой,
    а не тихо портит KP. Первого В ЖИЗНИ, а не первого в окне отчёта: окном
    граница делала исход обещания свойством ОТЧЁТА, и вечерняя книга (окно —
    один день) читала вчерашний срыв как «без даты».

    Именно первого, а не «этого»: обещание принадлежит должнику (решение
    владельца 22.09.2026). Сравнение с датой каждого выезда гасило сорванное
    обещание, стоило съездить к должнику второй раз, — повторный выезд
    оказывался позже даты обещания, и срыв превращался в «без даты». Работа с
    должником улучшала показатель BP ровно тем, что работа шла.
    """
    if visit_day is not None:
        # Старое имя параметра значило «день ЭТОГО выезда» — ровно ту поломку,
        # что описана выше. Принять такой вызов молча нельзя: книга каждый
        # вечер уходит живым руководителям, и тихо неверный BP дороже падения.
        raise TypeError(
            "funnel.promise_state: параметра visit_day больше нет — обещание "
            "принадлежит должнику, а не выезду. Передавай "
            "first_visit_day=funnel.first_visit_day(дни ВСЕХ выездов к этому "
            "должнику), а не день этого выезда и не первый выезд окна.")
    if promise_day is None:
        return PROMISE_NODATE
    if first_visit_day is not None and promise_day < first_visit_day:
        return PROMISE_NODATE
    if paid or (last_payment_day is not None and last_payment_day >= promise_day):
        return PROMISE_KEPT
    # Срок «сегодня» ещё не сорван: день не кончился. Та же граница, что в
    # db.overdue_promises (`pd >= target` — обещание ещё не наступило), иначе
    # книга и сводка «Просрочено обещаний» спорили бы каждое утро.
    if promise_day >= on:
        return PROMISE_PENDING
    return PROMISE_BROKEN


# --- Ранжирование ------------------------------------------------------------
# «Показатель хочется иметь с ранжированием по объёму обещаний оплаты. Будем
# иметь понимание, с какими долгами умеем работать». Границы — круглые рубли;
# порядок корзин задан здесь, чтобы книга не сортировала их по алфавиту
# («до 50 тыс» иначе оказывается между «1 млн и выше» и «150–500 тыс»).
AMOUNT_BUCKETS = (
    (50_000, "до 50 тыс"),
    (150_000, "50–150 тыс"),
    (500_000, "150–500 тыс"),
    (1_000_000, "500 тыс – 1 млн"),
    (None, "1 млн и выше"),
)
AMOUNT_UNKNOWN = "сумма не указана"
AMOUNT_ORDER = [title for _, title in AMOUNT_BUCKETS] + [AMOUNT_UNKNOWN]

OVERDUE_BUCKETS = (
    (0, "в срок"),
    (7, "1–7 дней"),
    (30, "8–30 дней"),
    (90, "31–90 дней"),
    (None, "свыше 90 дней"),
)
OVERDUE_ORDER = [title for _, title in OVERDUE_BUCKETS]


def amount_bucket(amount) -> str:
    """Корзина по сумме обещания. None/0 — «сумма не указана»: обещание без
    суммы это не обещание на ноль рублей, и в корзину «до 50 тыс» оно не идёт."""
    if not amount or amount <= 0:
        return AMOUNT_UNKNOWN
    for edge, title in AMOUNT_BUCKETS:
        if edge is None or amount < edge:
            return title
    return AMOUNT_BUCKETS[-1][1]


def overdue_bucket(days) -> str:
    """Корзина по сроку просрочки обещания, в днях."""
    value = max(0, int(days or 0))
    for edge, title in OVERDUE_BUCKETS:
        if edge is None or value <= edge:
            return title
    return OVERDUE_BUCKETS[-1][1]


# --- Счётчики и проценты -----------------------------------------------------
# Порядок ступеней = порядок колонок в книге. Меняя его здесь, меняешь книгу.
#
# ЕДИНИЦЫ. Первые четыре ступени считают ВЫЕЗДЫ — так их и определил Альберт
# («к объёму выездов»), и так они складываются в лестницу. Исполнение обещания
# считает ЛЮДЕЙ: обещание принадлежит должнику, а не поездке, и человек, давший
# обещание дважды за месяц, оплатит его один раз. Поэтому у KP/BP свой
# знаменатель `ptp_persons`, и kept + broken + pending + nodate сходится с ним
# в точности — эту сумму можно проверить прямо в книге.
COUNTERS = ("visits", "carded", "contacts", "dead", "rpc", "third",
            "relatives", "ptp", "ptp_persons", "promise_third",
            "kept", "broken", "pending", "nodate")

# Показатель → (числитель, знаменатель). Знаменатель ступени — это числитель
# предыдущей: в этом и состоит воронка, и здесь это видно одной таблицей.
RATES = {
    "penetration": ("visits", "plan"),
    "contact_rate": ("contacts", "carded"),
    "rpc": ("rpc", "contacts"),
    "ptp": ("ptp", "rpc"),
    "kp": ("kept", "ptp_persons"),
    "bp": ("broken", "ptp_persons"),
}


def empty_counts() -> dict:
    """Нулевая строка воронки — чтобы сложение и пустой период выглядели одинаково."""
    return dict.fromkeys(COUNTERS, 0)


def add_counts(*chunks) -> dict:
    """Сложить несколько строк воронки в одну (сотрудник → офис → страна → итог)."""
    out = empty_counts()
    for chunk in chunks:
        for key in COUNTERS:
            out[key] += int((chunk or {}).get(key) or 0)
    return out


def rate(numerator, denominator) -> float | None:
    """Доля в процентах или None, если знаменателя нет.

    None — это НЕ ноль: «0 %» значит «делали и не вышло», а отсутствие
    знаменателя значит «не делали». В книге первое — цифра, второе — прочерк, и
    средний Contact Rate по команде не должен занижаться теми, кто не выезжал.
    """
    try:
        denominator = int(denominator or 0)
    except (TypeError, ValueError):
        return None
    if denominator <= 0:
        return None
    return round(int(numerator or 0) * 100 / denominator, 1)


def rates(counts: dict, plan: int = 0) -> dict:
    """Все пять показателей воронки по счётчикам. plan — задание на период."""
    source = dict(counts or {})
    source["plan"] = int(plan or 0)
    return {name: rate(source.get(num), source.get(den))
            for name, (num, den) in RATES.items()}


def _assert_known() -> None:
    """Литералы классификации обязаны существовать в словарях реестра.

    Переименуют вариант в «Статусах» — модуль не импортируется, и это увидит
    тест, а не Альберт в колонке, которая молча стала нулевой.
    """
    import fields

    checks = {
        "contact_with": CONTACT_CLIENT + CONTACT_THIRD + CONTACT_NONE,
        "agreed_payment": PROMISE_CLIENT + PROMISE_THIRD + PROMISE_NONE,
        "who_at_address": (AT_ADDRESS_KNOWN + AT_ADDRESS_STRANGER
                           + AT_ADDRESS_UNKNOWN),
    }
    for key, used in checks.items():
        known = set(fields.DROPDOWNS[key])
        missing = [v for v in used if v not in known]
        if missing:
            raise RuntimeError(
                f"воронка: значения нет в словаре «{key}» реестра: "
                + ", ".join(missing))
        # Обратная проверка: новый вариант в реестре не должен беззвучно
        # выпасть из воронки — он не попадёт ни в одну ступень.
        extra = [v for v in fields.DROPDOWNS[key] if v not in set(used)]
        if extra:
            raise RuntimeError(
                f"воронка не знает новых значений «{key}»: " + ", ".join(extra))


_assert_known()
