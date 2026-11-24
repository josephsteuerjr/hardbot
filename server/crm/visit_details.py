"""Optional evidence collected while recording a field visit."""
import math
from .contract import CRMError, operation_id


def normalize(value):
    if not isinstance(value, dict) or set(value) - {'phone', 'point', 'photos'}:
        raise CRMError('Некорректные данные выезда.')
    result = {}
    if 'phone' in value:
        phone = value['phone']
        if not isinstance(phone, str) or not 1 <= len(phone.strip()) <= 80:
            raise CRMError('Проверьте номер телефона.')
        result['phone'] = phone.strip()
    if 'point' in value:
        point = value['point']
        if not isinstance(point, dict) or set(point) != {'lat', 'lon'}:
            raise CRMError('Укажите широту и долготу.')
        for key, bound in [('lat', 90), ('lon', 180)]:
            coord = point[key]
            if type(coord) not in (int, float) or not -bound <= coord <= bound or not math.isfinite(coord):
                raise CRMError('Проверьте координаты выезда.')
        result['point'] = point
    if 'photos' in value:
        photos = value['photos']
        if not isinstance(photos, list) or not 1 <= len(photos) <= 10:
            raise CRMError('К выезду можно добавить до 10 фото.')
        ids = [operation_id(x) for x in photos]
        if len(set(ids)) != len(ids):
            raise CRMError('Фотография выбрана дважды.')
        result['photos'] = ids
    return result


def verify_photos(conn, principal, contract_ids, photos):
    from . import access, documents
    if len(contract_ids) != 1:
        raise CRMError('Для выезда с фото выберите один договор.')
    access.contracts(conn, principal, contract_ids, capability='document')
    for identifier in photos:
        row = conn.execute('SELECT * FROM crm_documents WHERE document_id=? AND contract_uuid=? AND actor_uid=?',
                           (identifier, contract_ids[0], principal.actor_uid)).fetchone()
        if not row or row['media_type'] not in {'image/jpeg', 'image/png'}:
            raise CRMError('Фото выезда ещё не принято для этого договора.', 'document_missing', 409)
        documents.verify(row)
