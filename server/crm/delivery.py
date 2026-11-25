"""Courier-reported envelope delivery, separate from a field visit."""
import re
import json
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


def latest(conn,contracts):
    """Read-only last received result, also used by the common workbook."""
    ids=sorted(contracts);result={}
    for offset in range(0,len(ids),400):
        chunk=ids[offset:offset+400]
        query="SELECT c.contract_uuid,e.payload_json,e.received_at,s.name FROM crm_events e JOIN crm_event_contracts c USING(event_id) LEFT JOIN crm_employees s USING(employee_uuid) WHERE e.kind='delivery' AND e.projection_status='applied' AND c.contract_uuid IN ("+','.join('?' for _ in chunk)+') ORDER BY e.sequence DESC'
        for row in conn.execute(query,chunk):
            if row['contract_uuid'] in result:continue
            data=json.loads(row['payload_json'])
            result[row['contract_uuid']]=dict(delivery_result='Конверт передан' if data['delivered'] else 'Конверт не передан',
                delivery_address=data['delivery_address'],delivery_phone=data['delivery_phone'],delivery_comment=data['delivery_comment'],
                delivery_received_at=row['received_at'],delivery_author=row['name'] or '')
    return result
