"""Contextual date headers and established business-caption aliases."""
import rbr_manual_dates
from edit_tracking import EXCEL_KEYS,LOGICAL_TITLES,normalized
from edit_tracking import logical_headers as _logical_headers

def logical_headers(headers):
    aliases={normalized('Статус звонка'):'Cтатус звонка',
             normalized('Cтатус звонка'):'Cтатус звонка',
             normalized('Статус звонка СОФТ'):'статус звонка софт',
             normalized('ответственный Сотрудник'):'ответственный Сотрудник ХАРД'}
    return [aliases.get(normalized(title),title) for title in _logical_headers(headers)]
