"""Server-generated daily books and the latest recorded three-hour reports."""
from base64 import b64encode
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import access, assignments, manager_reports, primary_fence, shared_auth, storage, three_hour
from .contract import CRMError, canonical, digest, instant, now

COUNTRY_TIMEZONES={'tj':'Asia/Dushanbe','uz':'Asia/Tashkent'}


def daily_authority(conn, principal):
    codes=three_hour.country_scopes(conn,principal)
    for code in ('uz','tj'):
        if code in codes:return replace(principal,timezone=COUNTRY_TIMEZONES[code])
    return principal


def daily_key(conn, principal):
    roster = assignments.team_results_roster(conn, principal)
    territory = sorted((uid, row['country'], sorted(row['offices']), row.get('group'))
                       for uid, row in roster.items())
    return 'daily:' + digest([manager_reports.scope_key(principal), principal.timezone,
                              access.reads_all(principal), territory])


def archived_day(conn, principal, key, day):
    return conn.execute('SELECT report_json FROM crm_report_snapshots WHERE actor_uid=? AND scope_key=? AND slot=?',
                        (principal.actor_uid, key, day)).fetchone()


def daily(principal, day=None):
    with storage.connect(readonly=True) as conn:
        principal=daily_authority(conn,principal)
    if day is None:
        return assignments.statistics(principal, period='today', download='daily')
    try:
        selected = date.fromisoformat(day)
        if selected.isoformat() != day:
            raise ValueError()
    except (TypeError, ValueError):
        raise CRMError('Укажите рабочий день.') from None
    if selected == instant(now()).astimezone(ZoneInfo(principal.timezone)).date():
        return assignments.statistics(principal, period='today', download='daily')
    with storage.connect(readonly=True) as conn:
        key = daily_key(conn, principal)
        row = archived_day(conn, principal, key, day)
    if not row:
        raise CRMError('За этот день нет сохранённого отчёта для вашей команды.', 'snapshot_missing', 404)
    return json.loads(row['report_json'])


def latest_three_hours(principal, country='', day=None):
    from country_scope import info

    if day is not None:
        try:
            day = date.fromisoformat(day).isoformat()
        except (TypeError, ValueError):
            raise CRMError('Укажите дату среза.') from None
    with storage.connect(readonly=True) as conn:
        principal = access.refresh_write(conn, principal)
        manager_reports.require(principal)
        scopes = three_hour.country_scopes(conn, principal)
        if country:
            scopes = {country: manager_reports.country_members(conn, principal, country)}
        if not scopes:
            if conn.execute("SELECT 1 FROM crm_meta WHERE key='deployment_namespace' "
                            "AND value='crm-shared-pilot-v1'").fetchone():
                raise CRMError('Для вашей команды пока нет трёхчасового среза.', 'snapshot_missing', 404)
            scopes = {'': None}
        snapshots = []
        for code, members in scopes.items():
            key = manager_reports.scope_key(principal, code, members)
            sql = 'SELECT slot,report_json FROM crm_report_snapshots WHERE actor_uid=? AND scope_key=?'
            parameters = [principal.actor_uid, key]
            if code:
                local=instant(now()).astimezone(ZoneInfo(COUNTRY_TIMEZONES[code]))
                sql += ' AND slot LIKE ?'
                parameters.append('%'+local.isoformat()[-6:])
            if day:
                sql += ' AND slot>=? AND slot<?'
                parameters.extend([day, day+'T~'])
            row = conn.execute(sql+' ORDER BY captured_at DESC LIMIT 1', parameters).fetchone()
            label = info(code)['name'] if code else ''
            if not row:
                raise CRMError(f'Пока нет сохранённого трёхчасового среза{f" · {label}" if label else ""}.',
                               'snapshot_missing', 404)
            snapshots.append((label,row['slot'],json.loads(row['report_json'])))
    report = dict(snapshots[0][2], sheets=[])
    cuts = []
    for label, slot, snap in snapshots:
        cuts.append(dict(territory=label,slot=slot,as_of=snap['as_of']))
        for sheet in manager_reports.snapshot_summary(snap['sheets']):
            title = label+(' · SMS' if sheet['sms'] else ' · звонки') if label else sheet['name']
            report['sheets'].append(dict(sheet,name=title,as_of=snap['as_of'],currency=snap['currency'],timezone=snap['timezone'],
                                         yesterday_incomplete_contracts=snap['yesterday_incomplete_contracts']))
    report['cuts'] = cuts
    return report


def download(principal, *, preset, country='', day=None):
    manager_reports.require(principal)
    if principal.role not in {'manager','owner'}:
        raise CRMError('Выгрузка доступна руководителю.', 'forbidden', 403)
    if preset == 'daily':
        if country:
            raise CRMError('Дневной отчёт содержит все доступные территории.')
        return daily(principal, day)
    if preset != 'three-hour':
        raise CRMError('Неизвестный вид отчёта.')
    from pult.core.portable_xlsx import write_report

    report = latest_three_hours(principal, country, day)
    stamp = min(instant(cut['as_of']) for cut in report['cuts'])
    point = stamp.astimezone(ZoneInfo(report['timezone']))
    name = f"Трёхчасовой свод {point:%Y-%m-%d %H-%M}.xlsx"
    with TemporaryDirectory(prefix='crm-report-') as directory:
        target = Path(directory)/name
        receipt = write_report(report, target, kind='three-hour')
        return dict(filename=name, sha256=receipt['sha256'], content_base64=b64encode(target.read_bytes()).decode('ascii'),
                    as_of=stamp.isoformat(), timezone=report['timezone'], cuts=report['cuts'])


def active_managers():
    import db
    with primary_fence.connect(readonly=True) as conn:
        rows = conn.execute("SELECT a.telegram_id,a.employee,a.role,m.name FROM accounts a "
                            "JOIN managers m USING(telegram_id) WHERE a.status='active' AND a.password_review_pending=0 "
                            "AND a.role IN ('manager','full') AND a.role=COALESCE(NULLIF(m.role,''),'manager')")
        return {r['telegram_id']:r['role'] for r in rows
                if r['telegram_id'] and db._name_key(r['employee'])==db._name_key(r['name'])}


def archive_day(principal, key, day):
    manager_reports.require(principal)
    with storage.connect(readonly=True) as conn:
        current=access.refresh_write(conn,principal)
        manager_reports.require(current)
        if (current.access_model==2 and
                manager_reports.authority_version(current)!=manager_reports.authority_version(principal)):
            raise CRMError('Полномочие отчёта изменилось. Повторите запрос.','scope_changed',403)
    book = assignments.statistics(current, period='custom', date_from=day, date_to=day, download='daily')
    if active_managers().get(current.actor_uid) not in {'manager','full'}:
        return False
    with storage.connect() as conn:
        storage.begin(conn)
        refreshed=access.refresh_write(conn,current)
        manager_reports.require(refreshed)
        if (refreshed.access_model==2 and
                manager_reports.authority_version(refreshed)!=manager_reports.authority_version(current)):
            raise CRMError('Полномочие отчёта изменилось. Повторите запрос.','scope_changed',403)
        if daily_key(conn, refreshed) != key:
            return False
        conn.execute('INSERT INTO crm_report_snapshots VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(actor_uid,scope_key,slot) DO NOTHING',
                     (str(uuid4()),refreshed.actor_uid,key,day,book['as_of'],canonical(book)))
        conn.commit()
    return True


def schedule():
    """Run one pass using the current account and team assignments."""
    with storage.connect(readonly=True) as conn:
        mode = conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
    if not mode or mode[0] != 'cutover':
        return dict(captured=0,archived=0,denied=0)
    result = dict(captured=0,archived=0,denied=0)
    for actor_uid, role in active_managers().items():
        try:
            principal = shared_auth.principal(dict(uid=actor_uid,role=role))
            manager_reports.require(principal)
            with storage.connect(readonly=True) as conn:
                daily_principal = daily_authority(conn,principal)
                previous_day = (instant(now()).astimezone(ZoneInfo(daily_principal.timezone)).date()-timedelta(days=1)).isoformat()
                key = daily_key(conn, daily_principal)
                missing_day = not archived_day(conn, principal, key, previous_day)
                missing_slices = []
                for country,members in three_hour.country_scopes(conn,principal).items():
                    scoped=replace(principal,timezone=COUNTRY_TIMEZONES[country])
                    point=instant(now()).astimezone(ZoneInfo(scoped.timezone))
                    slot=point.replace(hour=point.hour//3*3,minute=0,second=0,microsecond=0).isoformat()
                    if not conn.execute('SELECT 1 FROM crm_report_snapshots WHERE actor_uid=? AND scope_key=? AND slot=?',
                                        (actor_uid,manager_reports.scope_key(scoped,country,members),slot)).fetchone():
                        missing_slices.append((country,scoped))
            for country,scoped in missing_slices:
                if active_managers().get(actor_uid)==role:
                    manager_reports._capture(scoped,country,register=False)
                    result['captured'] += 1
            if missing_day and archive_day(daily_principal,key,previous_day):
                result['archived'] += 1
        except CRMError:
            result['denied'] += 1
    return result
