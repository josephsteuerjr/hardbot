"""Private local-socket exchange worker, separate from both HTTP services."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from . import legacy_exchange, legacy_mirror
from .contract import now


def tick(database, employees):
    snapshot=legacy_exchange.capture(database)
    receipt=legacy_exchange.apply(snapshot,None)
    outgoing=legacy_mirror.deliver(database,set(employees)) if employees else []
    return dict(at=now(),captured_at=snapshot['captured_at'],incoming=receipt,
                outgoing=dict(delivered=sum(r['state']=='delivered' for r in outgoing),needs_review=sum(r['state']=='needs_review' for r in outgoing)))


def save(path, value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--health',action='store_true')
    args=parser.parse_args();config=json.loads(args.config.read_text())
    if set(config)!={'database','employees','status','interval_seconds'}:raise ValueError('Explicit worker configuration required')
    if not 30<=config['interval_seconds']<=3600:raise ValueError('Invalid worker interval')
    if not isinstance(config['employees'],list) or len(config['employees'])>100:raise ValueError('Invalid worker cohort')
    status=Path(config['status'])
    if args.health:
        value=json.loads(status.read_text())
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(value['at'])).total_seconds()
        if not value.get('ok') or not -60<=age<max(300,config['interval_seconds']*4):raise SystemExit(1)
        return
    while True:
        started=time.monotonic()
        try:
            result=dict(ok=True,**tick(config['database'],config['employees']))
            save(status,result)
            print(json.dumps(dict(ok=True,at=result['at'],events=result['incoming']['events'],fields=result['incoming']['changed_fields'],issues=result['incoming']['issues'],outgoing=result['outgoing'])),flush=True)
        except Exception as error:
            # No request bodies, DSNs, client names or credentials in logs.
            save(status,dict(ok=False,at=now(),error=type(error).__name__,code=getattr(error,'code','exchange_failed')))
            print(json.dumps(dict(ok=False,error=type(error).__name__,code=getattr(error,'code','exchange_failed'))),flush=True)
            if args.once:raise SystemExit(1)
        if args.once:return
        time.sleep(max(1,config['interval_seconds']-(time.monotonic()-started)))


if __name__=='__main__':main()
