"""Anchored, idempotent activity intervals; union prevents multi-device counting."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from . import storage, store, access
from .contract import CRMError, instant, now, operation_id

DDL = '''
CREATE TABLE IF NOT EXISTS crm_active_intervals(
 operation_id TEXT PRIMARY KEY,employee_uuid TEXT NOT NULL,actor_uid BIGINT NOT NULL,
 installation_id TEXT NOT NULL,kind TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,
 received_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS crm_active_window ON crm_active_intervals(employee_uuid,starts_at,ends_at);
'''


def ingest(principal, body):
    if (not isinstance(body, dict) or set(body) != {'schema_version','operation_id','anchor_id',
            'installation_id','start_ms','end_ms','kind'} or body['schema_version'] != 1
            or body['kind'] not in {'active','idle'}):
        raise CRMError('Неизвестный формат активного интервала.')
    operation=operation_id(body['operation_id'])
    start,end=body['start_ms'],body['end_ms']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= 31*86400000 or end-start>30000:
        raise CRMError('Длительность интервала должна быть от 1 до 30000 мс.')
    received=now()
    with storage.connect() as conn:
        store.begin(conn,[storage.operation_lock(principal,operation),'active:'+principal.employee_uuid])
        principal=access.refresh_write(conn,principal)
        old=store.replay(conn,principal,operation,body)
        if old is not None:return old
        anchor=conn.execute('SELECT server_at FROM crm_clock_anchors WHERE anchor_id=? AND actor_uid=? AND installation_id=?',
                            (body['anchor_id'],principal.actor_uid,body['installation_id'])).fetchone()
        if not anchor:raise CRMError('Серверная точка отсчёта недоступна.','clock_invalid',409)
        first=instant(anchor[0])+timedelta(milliseconds=start)
        last=instant(anchor[0])+timedelta(milliseconds=end)
        if last>instant(received)+timedelta(seconds=10) or first<instant(received)-timedelta(days=31):
            raise CRMError('Интервал вне подтверждённого окна времени.','clock_invalid',409)
        existing=conn.execute('SELECT operation_id FROM crm_active_intervals WHERE operation_id=?',(operation,)).fetchone()
        if existing:raise CRMError('ID интервала уже использован.','operation_conflict',409)
        conn.execute('INSERT INTO crm_active_intervals VALUES(?,?,?,?,?,?,?,?)',
            (operation,principal.employee_uuid,principal.actor_uid,body['installation_id'],body['kind'],
             first.isoformat(timespec='milliseconds'),last.isoformat(timespec='milliseconds'),received))
        result=dict(ok=True,status='accepted',operation_id=operation,received_at=received)
        store.acknowledge(conn,principal,operation,body,result,received)
        return result


def union(intervals):
    result=[]
    for first,last in sorted(intervals):
        if result and first<=result[-1][1]:result[-1]=(result[-1][0],max(last,result[-1][1]))
        else:result.append((first,last))
    return result


def summary(principal, since=None, until=None):
    zone=ZoneInfo(principal.timezone)
    today=instant(now()).astimezone(zone).date()
    try:first=date.fromisoformat(since) if since else today;last=date.fromisoformat(until) if until else first
    except (TypeError,ValueError):raise CRMError('Укажите даты периода.')
    if not first<=last or (last-first).days>365:raise CRMError('Выберите период до 366 дней.')
    start=datetime.combine(first,time.min,zone);end=datetime.combine(last+timedelta(days=1),time.min,zone)
    with storage.connect(readonly=True) as conn:
        rows=conn.execute('SELECT kind,starts_at,ends_at FROM crm_active_intervals WHERE employee_uuid=? AND starts_at<? AND ends_at>?',
            (principal.employee_uuid,end.astimezone(ZoneInfo('UTC')).isoformat(),start.astimezone(ZoneInfo('UTC')).isoformat())).fetchall()
    buckets={}
    utc=ZoneInfo('UTC')
    for row in rows:
        a=max(start.astimezone(utc),instant(row['starts_at']))
        b=min(end.astimezone(utc),instant(row['ends_at']))
        while a<b:
            day=a.astimezone(zone).date()
            boundary=datetime.combine(day+timedelta(days=1),time.min,zone).astimezone(utc)
            stop=min(b,boundary)
            buckets.setdefault(day,{'active':[],'idle':[]})[row['kind']].append((a,stop))
            a=stop
    days=[];current=first
    while current<=last:
        ranges={kind:union(buckets.get(current,{}).get(kind,[])) for kind in ('active','idle')}
        active=sum((y-x).total_seconds()*1000 for x,y in ranges['active'])
        idle=sum((y-x).total_seconds()*1000 for x,y in ranges['idle'])
        # Linear intersection of the two already merged interval lists.
        i=j=0
        while i<len(ranges['idle']) and j<len(ranges['active']):
            x,y=ranges['idle'][i];u,v=ranges['active'][j]
            idle-=max(0,(min(y,v)-max(x,u)).total_seconds()*1000)
            if y<=v:i+=1
            else:j+=1
        days.append(dict(day=current.isoformat(),active_ms=round(active),idle_ms=round(idle)));current+=timedelta(days=1)
    return dict(timezone=principal.timezone,days=days,active_ms=sum(d['active_ms'] for d in days),
                idle_ms=sum(d['idle_ms'] for d in days),as_of=now(),idle_threshold_seconds=120)
