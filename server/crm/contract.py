"""Stable field meaning, exact values, command and timestamp envelopes."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import re
import uuid

SCHEMA_VERSION = 1
NAMESPACE = uuid.UUID("95c044ee-8090-4ab8-a15f-68aa12332181")
KINDS = {
    "call",
    "visit",
    "note",
    "contact",
    "promise",
    "task",
    "reported_payment",
    "payment_confirmed",
    "document",
    "correction",
    "task_complete",
    "delivery",
}
USEFUL = KINDS - {"correction"}
CALL_KEYS = {"soft_call_status", "soft_comm_date", "soft_comment"}
VISIT_KEYS = {
    "call_status",
    "visit_date",
    "visit_comment",
    "contact_with",
    "agreed_payment",
    "who_at_address",
    "client_location",
    "purpose_rf",
    "tips",
    "extra_address",
    "call_comment",
}
CONTACT_KEYS = {"phone", "phone_main", "address", "address_customer", "extra_address"}
DELIVERY_FIELDS = {
    'delivery_result':'Курьер: результат','delivery_address':'Курьер: адрес передачи',
    'delivery_phone':'Курьер: телефон','delivery_comment':'Курьер: комментарий',
    'delivery_received_at':'Курьер: принято CRM','delivery_author':'Курьер: сотрудник',
}
PROMISE_KEYS = {"promise_date", "forecast_sum", "forecast", "meeting_date"}
MONEY_KEYS = {
    "debt_total",
    "debt_today",
    "issued_sum",
    "last_payment_sum",
    "forecast_sum",
}
DATE_KEYS = {
    "birth_date",
    "kd_date",
    "assign_date",
    "last_payment_date",
    "visit_date",
    "soft_comm_date",
    "soft_next_call_date",
    "promise_date",
    "meeting_date",
}


class CRMError(ValueError):
    def __init__(self, message, code="invalid", status=400, details=None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    # Bulk baselines can exceed 100 MB. Hash the identical canonical byte
    # stream without retaining a second JSON string and UTF-8 byte buffer.
    checksum = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(value):
        checksum.update(chunk.encode("utf-8"))
    return checksum.hexdigest()


def identifier(kind, value):
    return str(uuid.uuid5(NAMESPACE, kind + ":" + str(value)))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def instant(value):
    if not isinstance(value, str):
        raise CRMError("Укажите время с часовым поясом.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CRMError("Некорректное время.") from exc
    if parsed.tzinfo is None:
        raise CRMError("У времени отсутствует часовой пояс.")
    return parsed.astimezone(timezone.utc)


def money(value, *, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int)):
        raise CRMError("Денежная сумма передаётся точным десятичным значением.")
    if not re.fullmatch(r"-?\d{1,15}(\.\d{1,2})?", str(value)):
        raise CRMError("Сумма: не более двух знаков после запятой.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise CRMError("Некорректная сумма.") from exc
    if not result.is_finite():
        raise CRMError("Некорректная сумма.")
    return format(result.quantize(Decimal(".01")), "f")


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise CRMError("Укажите валюту тремя латинскими буквами.")
    return value


def operation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,100}", value):
        raise CRMError("Некорректный идентификатор операции.")
    return value


def group(key):
    if key in DELIVERY_FIELDS:return 'delivery'
    if key in PROMISE_KEYS:
        return "forecast"
    if key in CALL_KEYS:
        return "call"
    if key in VISIT_KEYS:
        return "visit"
    if key in CONTACT_KEYS:
        return "contact"
    return "accounting"


def fields():
    from fields import FIELDS
    from db import NUM_KEYS, DATE_KEYS

    return [
        dict(
            key=f.key,
            title=f.title,
            section=f.block,
            group=group(f.key),
            type=(
                "integer"
                if f.key == "visits_count"
                else (
                    "money"
                    if f.key in NUM_KEYS
                    else "date" if f.key in DATE_KEYS else "text"
                )
            ),
            source="accounting" if group(f.key) == "accounting" else "work_event",
            clear="explicit",
            nullable=True,
        )
        for f in FIELDS
    ] + [dict(key=key,title=title,group='delivery',type='text',source='work_event',clear='explicit',nullable=True,readonly=True)
         for key,title in DELIVERY_FIELDS.items()]


@lru_cache(maxsize=1)
def _field_types():
    return {definition["key"]: definition["type"] for definition in fields()}


def field_value(key, value, *, clear=False):
    from fields import DROPDOWNS

    field_type = _field_types().get(key)
    if field_type is None:
        raise CRMError("Неизвестное поле.", details={"key": key})
    if value is None:
        if not clear:
            raise CRMError("Очистка поля должна быть явной.", details={"key": key})
        return None
    if field_type == "money":
        return money(value)
    if field_type == "integer":
        if isinstance(value, bool) or not re.fullmatch(r"\d{1,8}", str(value)):
            raise CRMError("Ожидается целое число.")
        return str(int(value))
    if not isinstance(value, str) or len(value) > 32700:
        raise CRMError("Некорректный текст поля.", details={"key": key})
    if key in DROPDOWNS and value not in DROPDOWNS[key]:
        raise CRMError("Выберите допустимое значение поля.", details={"key": key})
    if field_type == "date" and value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise CRMError(
                "Дата должна иметь формат ГГГГ-ММ-ДД.", details={"key": key}
            ) from exc
    return value
