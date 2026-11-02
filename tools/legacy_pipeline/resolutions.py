"""Exact, one-snapshot reconciliation. No reusable 'prefer this role' rule."""
import json
from .collection import identity, FORECAST


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def resolve(result, rows, approved, *, master_sha256, source_hashes):
    if approved.get('master_sha256') != master_sha256 or approved.get('source_hashes') != source_hashes:
        return 0  # A previous decision is never attached to a new snapshot.
    decisions = {stable(x['conflict']):x['choice'] for x in approved.get('decisions', [])}
    positions = {identity(row):i for i,row in enumerate(rows) if identity(row)}
    pending=[];resolved=0
    for conflict in result.conflicts:
        choice=decisions.get(stable(conflict))
        if choice not in ('master','file') or conflict['kind'] not in ('ambiguous_value','ambiguous_forecast_group'):
            pending.append(conflict);continue
        key=tuple(conflict['key']);pos=positions[key]
        fields=[conflict['field']] if 'field' in conflict else list(FORECAST)
        values=[conflict['incoming']] if 'field' in conflict else conflict['incoming']
        result.changes[:]=[x for x in result.changes if not (tuple(x['key'])==key and x['field'] in fields)]
        if choice=='file':
            for title,value in zip(fields,values):
                result.changes.append(dict(row=pos,key=key,field=title,before=rows[pos].get(title),after=value))
        resolved+=1
    result.conflicts[:]=pending
    return resolved
