"""Explicit operator commands; never infer a database or cutover cohort."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))  # flat /app layout in Docker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from crm import schema,storage,primary_fence
from crm.contract import canonical,digest


def review_cutover(ids):
    rows = []
    with storage.connect(readonly=True) as conn:
        for start in range(0, len(ids), 200):
            rows.extend(primary_fence._projection_rows(conn, ids[start:start+200]))
    with primary_fence.connect(readonly=True) as primary:
        drift = dict(responsible=0, responsible_soft=0)
        for row in rows:
            current = primary.execute(
                'SELECT partner,responsible,responsible_soft FROM clients WHERE contract_id=?',
                (row['legacy_id'],),
            ).fetchone()
            if not current or row['creditor_id'] != 'registry-partner:'+str(current['partner'] or '').strip():
                raise ValueError('Primary identity mismatch')
            row['primary_assignments'] = {
                field: current[field] or '' for field in drift
            }
            for field, value in row['primary_assignments'].items():
                if value != row[field]:
                    drift[field] += 1
    return dict(contracts=len(ids), selection_sha256=digest(rows), ids_sha256=digest(ids),
                assignment_differences=drift, old_work_writes='will_close', activated=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['migrate','plan','activate','status'])
    parser.add_argument('--contracts',type=Path);parser.add_argument('--expected-sha256')
    args=parser.parse_args()
    if args.action=='migrate':
        with storage.connect() as conn:
            schema.initialize(conn)
            if storage.postgres_enabled():conn.execute((Path(__file__).with_name('crm_runtime_permissions.sql')).read_text())
        print(canonical(dict(schema_version=schema.VERSION,migrated=True)));return
    if args.action in {'plan','activate'}:
        ids=json.loads(args.contracts.read_text()) if args.contracts else None
        if not isinstance(ids,list) or not ids or any(not isinstance(v,str) for v in ids) or len(ids)!=len(set(ids)):raise ValueError('Explicit unique contract UUID list required')
        ids=sorted(ids)
        review=review_cutover(ids)
        if args.action=='activate':
            if args.expected_sha256!=review['selection_sha256']:
                raise ValueError('The reviewed contract selection and current assignments must match')
            if any(review['assignment_differences'].values()):
                raise ValueError('Reconcile the current assignee differences before activation')
            print(canonical(primary_fence.activate(ids)));return
        print(canonical(review));return
    with storage.connect(readonly=True) as conn:
        meta={r[0]:r[1] for r in conn.execute("SELECT key,value FROM crm_meta WHERE key IN ('schema_version','distribution_mode','primary_fence_id')")}
        status=[dict(r) for r in conn.execute('SELECT worker,heartbeat_at,ok,code FROM crm_runtime_status')]
        print(canonical(dict(meta=meta,workers=status,primary_ready=primary_fence.ready(conn),
            pending_projections=conn.execute('SELECT COUNT(*) FROM crm_primary_pending').fetchone()[0],
            source_conflicts=conn.execute("SELECT COUNT(*) FROM crm_migration_issues WHERE resolved_at IS NULL AND kind IN ('primary_assignment_conflict','primary_work_conflict')").fetchone()[0])))


if __name__=='__main__':main()
