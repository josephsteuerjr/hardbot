"""A separate workbook; existing Soft templates and their formulas are intact."""
from datetime import datetime, timezone

import book
from promise_report import STATUSES


def _money(items, key):
    return sum(row.get(key) or 0 for row in items)


def _stamp(value):
    if not value:
        return 'нет сведений'
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo:
            moment = moment.astimezone(timezone.utc)
        return moment.strftime('%d.%m.%Y %H:%M') + ' UTC'
    except ValueError:
        return value


def write(data):
    bk = book.Book()
    params = data['params']
    summary = bk.sheet('Выполнение обещаний', tab='main')
    details = bk.sheet('Обещания', tab='detail')
    for sheet in (summary, details):
        sheet.ws.hide_gridlines(2)
        sheet.ws.set_landscape()
        sheet.ws.set_paper(8)
        sheet.ws.fit_to_pages(1, 0)
    summary.title('Выполнение обещаний Софта', size=14)
    summary.title('Обещания: ' + params['from_date'] + ' — ' + params['to_date'])
    summary.title('Проверка оплат: ' + params['asof'] + ('; срок оплаты: ' + params['due'] if params['due'] else ''))
    summary.title('Последний успешный импорт: ' + _stamp(data['payment_data']['last_import_at']))
    summary.title('По сохранённым реестровым оплатам; полнота дневного реестра не подтверждена.', size=10)
    if data['open_deadlines']:
        summary.title('Срез промежуточный: у части обещаний срок оплаты ещё не завершён.', size=10)
    if data['payment_data']['future_dated_rows']:
        summary.title('Будущие даты платежей в источнике: ' + str(data['payment_data']['future_dated_rows']), size=10)
    summary.row += 1
    head = summary.head(['Сотрудник', 'Офис', 'Валюта', 'Обещаний (PTP)', 'Выполнено (KP)',
                         'Частичная оплата', 'Ожидаем срока', 'Срок прошёл, оплаты нет',
                         'Недостаточно данных', 'KP от PTP, %', 'Обещано', 'Оплачено в срок'])
    summary.ws.set_row(head, 46)
    summary.ws.repeat_rows(0, head)

    details.title('Обещания Софта', size=14)
    details.title('Источник: сохранённые звонки; оплаты — наблюдения импорта реестра.', size=10)
    dhead = details.head(['Сотрудник', 'Офис', 'Валюта', 'КД', 'ID звонка', 'День обещания',
                          'Срок оплаты', 'Обещано', 'Оплачено в срок', 'Осталось',
                          'Результат', 'Срок завершён'])
    details.ws.set_row(dhead, 34)
    detail_start = details.row + 1
    items = data['items']
    for item in items:
        at = details.append([item['employee'], item['office'], item['currency'], item['rep'], item['call_id'],
                            item['date'], item['due'], item['amount'], item['received'], item['remaining'],
                            item['status'], 'да' if item['deadline_closed'] else 'нет'],
                           ['text', 'text', 'text', 'text', 'int', 'date', 'date', 'num', 'num', 'num', 'text', 'text'])
        for col,key in [(7,'amount'),(8,'received')]:
            if item[key] is not None:
                details.ws.write_number(at,col,item[key],bk.fmt(num_format='#,##0.00'))
        if item['amount'] is not None:
            line = at+1
            details.ws.write_formula(at, 9, f'=MAX(0,H{line}-I{line})',
                                     bk.fmt(num_format='#,##0.00'), item['remaining'])
    detail_end = max(detail_start, details.row)
    refs = {col: f"'Обещания'!${col}${detail_start}:${col}${detail_end}" for col in ('A','C','H','I','K')}
    first = summary.row
    for employee in data['employees']:
        at = summary.append([employee['employee'], employee['office'], employee['currency']], ['text']*3)
        line = at+1
        criteria = f'{refs["A"]},A{line},{refs["C"]},C{line}'
        summary.ws.write_formula(at,3,'=COUNTIFS('+criteria+')',bk.fmt(num_format='#,##0'),employee['ptp'])
        for col, key in enumerate(STATUSES,4):
            formula = f'=COUNTIFS({criteria},{refs["K"]},"{STATUSES[key]}")'
            summary.ws.write_formula(at,col,formula,bk.fmt(num_format='#,##0'),employee[key])
        summary.ws.write_formula(at,9,f'=IFERROR(E{line}/D{line},"")',bk.fmt(num_format='0.0%'),
                                 employee['kp_rate'] if employee['kp_rate'] is not None else '')
        own = [row for row in items if row['employee']==employee['employee'] and row['currency']==employee['currency']]
        for col, key, ref in [(10,'amount','H'),(11,'received','I')]:
            if not employee['currency']:
                continue
            summary.ws.write_formula(at,col,f'=SUMIFS({refs[ref]},{criteria})',bk.fmt(num_format='#,##0.00'),_money(own,key))
    end = summary.row-1
    total_at = summary.append(['Итого'],['text'],bold=True)
    totals = data['totals']
    for col,key in [(3,'ptp'),(4,'kept'),(5,'partial'),(6,'waiting'),(7,'unpaid'),(8,'incomplete')]:
        formula = f'=SUM({chr(65+col)}{first+1}:{chr(65+col)}{end+1})' if end>=first else '=0'
        summary.ws.write_formula(total_at,col,formula,bk.fmt(bold=True,top=1,top_color=book.LINE,num_format='#,##0'),totals[key])
    summary.ws.write_formula(total_at,9,f'=IFERROR(E{total_at+1}/D{total_at+1},"")',
                             bk.fmt(bold=True,top=1,top_color=book.LINE,num_format='0.0%'),
                             totals['kp_rate'] if totals['kp_rate'] is not None else '')
    # Monetary grand totals remain blank when currencies are mixed or unknown.
    units = {item['currency'] for item in items}
    if len(units)==1 and '' not in units:
        summary.put(total_at,2,next(iter(units)),bold=True)
        for col,key in [(10,'amount'),(11,'received')]:
            letter = chr(65+col)
            formula=f'=SUM({letter}{first+1}:{letter}{end+1})' if end>=first else '=0'
            summary.ws.write_formula(total_at,col,formula,bk.fmt(bold=True,top=1,top_color=book.LINE,num_format='#,##0.00'),_money(items,key))
    summary.widths((32,18,10,13,13,15,14,21,18,14,21,21))
    details.widths((32,18,10,20,13,17,17,20,20,20,32,17))
    summary.autofilter(head,end,12)
    details.autofilter(dhead,details.row-1,12)
    summary.freeze(f'D{head+2}')
    details.freeze('D4')
    details.ws.repeat_rows(0,dhead)
    return bk.close()
