"""Courier-reported envelope delivery, separate from a field visit."""
import re
from .contract import CRMError


def normalize(payload):
    if type(payload.get('delivered')) is not bool:
        raise CRMError('Укажите результат передачи конверта.')
    address = payload.get('delivery_address', '')
    phone = payload.get('delivery_phone', '-')
    comment = payload.get('delivery_comment', '')
    for value, limit in [(address,2000),(phone,100),(comment,4000)]:
        if not isinstance(value,str) or len(value)>limit:
            raise CRMError('Проверьте адрес, телефон и комментарий.')
    address,phone,comment = address.strip(),phone.strip(),comment.strip()
    if payload['delivered'] and not address:
        raise CRMError('Укажите адрес передачи конверта.')
    if not comment:
        raise CRMError('Укажите получателя конверта или причину неудачи.')
    if phone in {'','-','—','–'}:
        phone='-'
    elif not re.fullmatch(r'[+\d() .-]+',phone) or not 5<=len(re.sub(r'\D','',phone))<=20:
        raise CRMError('Введите телефон или прочерк.')
    payload.update(delivery_address=address,delivery_phone=phone,delivery_comment=comment)
