"""Листы операционного отчёта Софта. B:AC повторяют шаблон встречи."""
from collections import defaultdict
from decimal import Decimal

import book
import soft_reporting as sr


def merge_money(chunks):
    amounts = defaultdict(Decimal)
    for chunk in chunks:
        for item in chunk:
            amounts[item["unit"]] += Decimal(str(item["sum"]))
    return [{"unit": unit, "sum": float(value)} for unit, value in sorted(amounts.items()) if value]


def cash(items):
    if not items:
        return 0, ""
    if len({x["unit"] for x in items}) == 1:
        return sum(x["sum"] for x in items), items[0]["unit"]
    return ' · '.join(f'{x["sum"]:,.2f} {x["unit"]}'.replace(',', ' ') for x in items), ""


def write(bk, soft, period):
    sheet = bk.sheet("Софт · операции", tab="funnel")
    ws = sheet.ws
    head = bk.fmt(bold=True, text_wrap=True, align="center", valign="vcenter",
                  bg_color=book.HEAD_FILL, border=1, font_color=book.INK)
    ws.merge_range('B1:AG1', 'Операционная воронка Софта · ' + period, bk.fmt(bold=True, font_size=14, font_color=book.ACCENT))
    for col, title in [(1, 'ФИО Сотрудника'), (2, 'Портфель, шт.'), (3, 'Портфель сумма'), (4, 'Кол-во звонков, шт.')]:
        ws.merge_range(1, col, 2, col, title, head)
    for col, title in enumerate(sr.STATUSES, 5):
        if title != 'Обещает оплатить':
            ws.merge_range(1, col, 2, col, title, head)
    for span, title in [('S2:T2', 'Обещает оплатить (PTP)'), ('V2:W2', 'KP'), ('Y2:Z2', 'RPC')]:
        ws.merge_range(span, title, head)
    for col, title in [(18, 'шт.'), (19, 'Сумма'), (21, 'шт.'), (22, 'Сумма'), (24, 'шт.'), (25, 'RPC, %')]:
        ws.write_string(2, col, title, head)
    for col, title in [(20, 'Средняя сумма обещаний'), (23, 'Средняя сумма выполненного обещания'),
                       (26, 'PTP от RPC, %'), (27, 'KP от PTP, %'), (28, 'Эффективность от портфеля'),
                       (29, 'Другие статусы'), (30, 'Частично оплачено, шт.'), (31, 'Частичные оплаты, сумма'),
                       (32, 'PTP без даты/суммы/валюты')]:
        ws.merge_range(1, col, 2, col, title, head)
    ws.set_row(1, 55); ws.set_row(2, 26)
    employees = soft['employees']
    total = {'employee': 'Итого:', 'contracts': sum(r['contracts'] for r in employees),
             'calls': sum(r['calls'] for r in employees),
             'debt_money': merge_money(r['operation']['portfolio_money'] for r in employees)}
    total['operation'] = {
        'statuses': {s: sum(r['operation']['statuses'][s] for r in employees) for s in sr.STATUSES},
        **{k: sum(r['operation'][k] for r in employees) for k in ('rpc', 'ptp', 'kp', 'other', 'partial', 'incomplete', 'amount_missing')},
        **{k: merge_money(r['operation'][k] for r in employees) for k in ('ptp_money', 'kp_money', 'partial_money')}}
    for index, r in enumerate([*employees, total], 3):
        op = r['operation']; line = index + 1; is_total = r is total
        style = dict(bold=is_total, bg_color=book.TOTAL_FILL if is_total else '', border=1, valign='vcenter')
        int_fmt = bk.fmt(num_format='#,##0', **style)
        pct_fmt = bk.fmt(num_format='0.0%', **style)
        ws.write_string(index, 1, r['employee'], bk.fmt(**style))
        for col, value in [(2, r['contracts']), (21, op['kp']), (29, op['other']), (30, op['partial']), (32, op['incomplete'])]:
            ws.write_number(index, col, value, int_fmt)
        for col, status in enumerate(sr.STATUSES, 5):
            ws.write_number(index, col, op['statuses'][status], int_fmt)
        monies = {}
        for col, key in [(3, 'debt_money'), (19, 'ptp_money'), (22, 'kp_money'), (31, 'partial_money')]:
            value, unit = cash((r[key] if is_total else op['portfolio_money']) if key == 'debt_money' else op[key]); monies[col] = (value, unit)
            fmt = bk.fmt(num_format='#,##0.00' + (f'" {unit}"' if unit else ''), **style)
            if isinstance(value, str): ws.write_string(index, col, value, fmt)
            else: ws.write_number(index, col, value, fmt)
        if is_total and employees:
            last = 3 + len(employees)
            for col in [2, 3, *range(5, 20), 21, 22, 29, 30, 31, 32]:
                if col in monies and isinstance(monies[col][0], str): continue
                name = _column(col)
                value = monies[col][0] if col in monies else (
                    r['contracts'] if col == 2 else op['kp'] if col == 21 else
                    op['other'] if col == 29 else op['partial'] if col == 30 else
                    op['incomplete'] if col == 32 else op['statuses'][sr.STATUSES[col - 5]])
                fmt = bk.fmt(num_format='#,##0.00' + (f'" {monies[col][1]}"' if monies[col][1] else ''), **style) if col in monies else int_fmt
                ws.write_formula(index, col, f'=SUM({name}4:{name}{last})', fmt, value)
        ws.write_formula(index, 4, f'=SUM(F{line}:S{line})+AD{line}', int_fmt, r['calls'])
        ws.write_formula(index, 24, f'=F{line}+I{line}+N{line}+P{line}+Q{line}+R{line}+S{line}', int_fmt, op['rpc'])
        for col, num, den, cached in [(25, 'Y', 'E', op['rpc'] / r['calls'] if r['calls'] else ''),
                                      (26, 'S', 'Y', op['ptp'] / op['rpc'] if op['rpc'] else ''),
                                      (27, 'V', 'S', op['kp'] / op['ptp'] if op['ptp'] else '')]:
            ws.write_formula(index, col, f'=IFERROR({num}{line}/{den}{line},"")', pct_fmt, cached)
        for col, numcol, dencol, denominator in [(20, 19, 'S', op['ptp']), (23, 22, 'V', op['kp'])]:
            value, unit = monies[numcol]
            if isinstance(value, str) or (col == 20 and op['amount_missing']): continue
            ws.write_formula(index, col, f'=IFERROR({_column(numcol)}{line}/{dencol}{line},"")',
                             bk.fmt(num_format='#,##0.00' + (f'" {unit}"' if unit else ''), **style), value / denominator if denominator else '')
        paid, paid_unit = monies[22]; portfolio, debt_unit = monies[3]
        if not isinstance(paid, str) and not isinstance(portfolio, str) and (not paid_unit or paid_unit == debt_unit):
            ws.write_formula(index, 28, f'=IFERROR(W{line}/D{line},"")', pct_fmt, paid / portfolio if portfolio else '')
    end = 4 + len(employees)
    note = ('RPC — статусы шаблона; варианты sms объединены с основными статусами. '
            'KP — полная оплата реестра до срока обещания, платёж относится к последнему обещанию. '
            'Ручные записи оплат не дают KP. Неизвестные суммы и даты не восстановлены из текущей карточки. '
            'История оплат накапливается с этой версии; ранее доступны только сохранившиеся платежи реестра. '
            'Портфель — текущее закрепление, звонки — по автору и дню события. Валюты не суммируются.')
    ws.merge_range(end + 1, 1, end + 3, 32, note, bk.fmt(text_wrap=True, valign='top', font_color=book.NOTE_COLOR))
    ws.set_column('A:A', 2); ws.set_column('B:B', 30); ws.set_column('C:AG', 14)
    for col in ('D', 'T', 'U', 'W', 'X', 'AF'): ws.set_column(f'{col}:{col}', 21)
    ws.freeze_panes(3, 2); ws.autofilter(2, 1, end - 2, 32)
    ws.set_landscape(); ws.set_paper(8); ws.fit_to_pages(1, 0); ws.repeat_rows(0, 2)
    _promises(bk, employees, period)


def _column(index):
    name = ''
    while index >= 0:
        name = chr(index % 26 + 65) + name
        index = index // 26 - 1
    return name


def _promises(bk, employees, period):
    ws = bk.sheet('Софт · обещания', tab='detail')
    ws.title('Обещания Софта · ' + period)
    ws.ws.merge_range(0, 0, 0, 8, 'Обещания Софта · ' + period,
                      bk.fmt(bold=True, font_size=13, font_color=book.ACCENT))
    ws.head(['Сотрудник', 'ID звонка', 'КД', 'День обещания', 'Срок', 'Обещано', 'Оплачено в срок', 'Валюта', 'Результат'])
    labels = {'kept': 'Выполнено (KP)', 'partial': 'Частичная оплата', 'pending': 'Полной оплаты в срок нет', 'incomplete': 'Не хватает даты/суммы/валюты'}
    for r in employees:
        for p in r['operation']['promises']:
            ws.append([r['employee'], p['call_id'], p['rep'], p['date'], p['due'],
                       (p['amount'], p['unit']), (p['received'], p['unit']), p['unit'], labels[p['state']]],
                      ['text', 'int', 'text', 'date', 'date', 'money', 'money', 'text', 'text'])
    ws.widths((30, 12, 20, 16, 16, 20, 20, 12, 36)); ws.freeze('A3')
