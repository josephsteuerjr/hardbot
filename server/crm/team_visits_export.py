"""Visit workbooks use the same authors, scopes and date boundaries as team results."""
from base64 import b64encode
from collections import defaultdict
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
from zoneinfo import ZoneInfo

from . import access
from .contract import CRMError, instant


def local_time(value, zone):
    try:
        return instant(value).astimezone(zone).replace(tzinfo=None)
    except CRMError:
        return None


def details(conn, principal, visits, zone):
    links = defaultdict(list)
    ids = [row['event_id'] for row, _, _ in visits]
    readable = None if access.reads_all(principal) else access.readable_contracts(conn, principal)
    for start in range(0, len(ids), 400):
        batch = ids[start:start+400]
        placeholders = ','.join('?' for _ in batch)
        for row in conn.execute(
            'SELECT l.event_id,c.contract_uuid,c.external_id,p.name FROM crm_event_contracts l '
            'JOIN crm_contracts c USING(contract_uuid) JOIN crm_people p USING(person_uuid) '
            'WHERE l.event_id IN (' + placeholders + ') ORDER BY c.external_id,c.contract_uuid', batch
        ):
            links[row['event_id']].append(row)
    result = []
    for row, day, author in visits:
        contracts = links[row['event_id']]
        visible = [c for c in contracts if readable is None or c['contract_uuid'] in readable]
        payload = json.loads(row['payload_json'])
        values = payload.get('values') or {}
        full = bool(contracts) and len(visible) == len(contracts)
        legacy = payload.get('legacy_raw') or {}
        point = (payload.get('field_visit') or {}).get('point') if full else None
        position = f"{point['lat']}, {point['lon']}" if isinstance(point,dict) and 'lat' in point and 'lon' in point else ''
        if full and not position and legacy.get('visit_lat') is not None and legacy.get('visit_lon') is not None:
            position = f"{legacy['visit_lat']}, {legacy['visit_lon']}"
        result.append(dict(
            day=date.fromisoformat(day), employee=author, contracts=len(contracts),
            clients=' / '.join(dict.fromkeys(c['name'] for c in visible)),
            external_ids=', '.join(dict.fromkeys(c['external_id'] for c in visible)),
            result=(values.get('call_status') or 'Без результата') if full else '',
            agreement=(values.get('agreed_payment') or '') if full else '',
            comment=(values.get('visit_comment') or values.get('call_comment') or '') if full else '',
            saved=local_time(legacy.get('saved_at') if row['origin']=='legacy' else row['occurred_at'],zone),
            received=local_time(legacy.get('received_at') if row['origin']=='legacy' else row['received_at'],zone),
            point=position, restricted=len(visible)!=len(contracts),
        ))
    return sorted(result, key=lambda v:(v['day'],v['employee']['name'].casefold(),v['saved'] or datetime.min))


def workbook(report, visits, *, daily=False):
    import xlsxwriter
    from country_scope import COUNTRIES
    from pult.core.portable_xlsx import cell, formats, setup_sheet

    countries = {region['code']:region['name'] for region in COUNTRIES}

    def name(staff):
        return countries.get(staff['country'],''), ', '.join(o for o in staff['offices'] if o!='!none')

    def add(book, title, headers, rows, widths):
        sheet=book.add_worksheet(title)
        setup_sheet(sheet)
        for col,width in enumerate(widths):
            sheet.set_column(col,col,width)
        sheet.set_row(0,32)
        sheet.freeze_panes(1,1)
        sheet.repeat_rows(0)
        for col,heading in enumerate(headers):
            cell(sheet,0,col,heading,fmts['header'])
        for line,row in enumerate(rows,1):
            for col,value in enumerate(row):
                fmt = 'datetime' if isinstance(value,datetime) else 'date' if isinstance(value,date) else 'integer' if isinstance(value,int) else 'text'
                cell(sheet,line,col,value,fmts[fmt])
        sheet.autofilter(0,0,len(rows),len(headers)-1)

    staff=sorted(report['staff'],key=lambda r:r['name'].casefold())
    summary=[[r['name'],*name(r),r['visit'],r['visit_norm'],r['visit_plan']] for r in staff]
    summary.append(['Итого','','',sum(r['visit'] for r in staff),None,sum(r['visit_plan'] or 0 for r in staff)])
    headings=['Сотрудник','Республика','Офис','Выезды','Норма в день','План']
    widths=[29,20,24,12,15,12]
    if daily:
        headings.insert(3,'Звонки')
        widths.insert(3,12)
        for line, calls in zip(summary,[r['call'] for r in staff]+[sum(r['call'] for r in staff)]):
            line.insert(3,calls)
    entries=[[v['day'],*name(v['employee']),v['employee']['name'],v['clients'],v['external_ids'],v['contracts'],
              v['result'],v['agreement'],v['comment'],v['saved'],v['received'],v['point'],
              'Часть сведений ограничена' if v['restricted'] else ''] for v in visits]
    start,end=map(date.fromisoformat,(report['since'],report['through']))
    dates=(start+timedelta(days=i) for i in range((end-start).days+1))
    days=[[day, sum(r['by_day'].get(day.isoformat(),0) for r in staff)]
          for day in dates]
    body=BytesIO()
    with xlsxwriter.Workbook(body,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False}) as book:
        fmts=formats(book)
        add(book,'Сводка',headings,summary,widths)
        add(book,'Выезды',['Дата','Республика','Офис','Сотрудник','Клиент','Договор','Договоров','Результат',
                           'Договорённость','Комментарий','Сохранён в приложении','Принят сервером','Гео','Доступ'],
            entries,[13,19,22,29,31,22,12,24,27,46,21,21,24,28])
        add(book,'По дням',['Дата','Выезды'],days,[15,14])
        if daily:
            local=instant(report['as_of']).astimezone(ZoneInfo(report['timezone']))
            add(book,'Период',['Рабочий день','Сформирован','Часовой пояс'],
                [[start,local.replace(tzinfo=None),report['timezone']]],[18,24,25])
    payload=body.getvalue()
    filename=f"Дневной отчёт {report['since']}.xlsx" if daily else f"Выезды {report['since']} – {report['through']}.xlsx"
    result=dict(filename=filename,content_base64=b64encode(payload).decode('ascii'),sha256=sha256(payload).hexdigest())
    if daily:
        result.update(day=report['since'],as_of=report['as_of'],timezone=report['timezone'])
    return result
