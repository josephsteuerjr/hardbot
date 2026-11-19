"""Explicit operator commands; never infer a database or cutover cohort."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))  # flat /app layout in Docker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from crm import schema,storage,primary_fence
from crm.contract import canonical,digest


def review_cutover(ids):
    with storage.connect(readonly=True) as conn, primary_fence.connect(readonly=True) as primary:
        primary.execute('BEGIN')
        review = primary_fence.cutover_review(primary_fence._cutover_rows(conn, primary, ids))
    return dict(review, contracts=len(ids), ids_sha256=digest(ids), old_work_writes='will_close', activated=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['migrate','plan','activate','status','logistics-staff','contact-status'])
    parser.add_argument('--contracts',type=Path);parser.add_argument('--expected-sha256')
    parser.add_argument('--roster',type=Path)
    parser.add_argument('--evidence',type=Path)
    args=parser.parse_args()
    if args.action=='contact-status':
        from crm import withdrawals
        if not args.evidence:
            parser.error('contact-status requires --evidence')
        evidence=json.loads(args.evidence.read_text(encoding='utf-8-sig'))
        apply=args.expected_sha256 is not None
        if apply and digest(evidence)!=args.expected_sha256:
            raise ValueError('The reviewed evidence digest must match')
        with storage.connect(readonly=not apply) as conn:
            if apply:
                storage.begin(conn)
            result=withdrawals.apply_confirmed(conn,evidence,apply=apply)
        print(canonical(result));return
    if args.action=='logistics-staff':
        from crm import logistics
        if not args.roster:
            parser.error('logistics-staff requires --roster')
        manifest=json.loads(args.roster.read_text(encoding='utf-8-sig'))
        with storage.connect(readonly=args.expected_sha256 is None) as conn:
            if args.expected_sha256 is not None:
                storage.begin(conn)
            result=logistics.configure_staff(conn,manifest,args.expected_sha256)
        print(canonical(result));return
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
            print(canonical(primary_fence.activate(ids, expected_sha256=args.expected_sha256)));return
        print(canonical(review));return
    with storage.connect(readonly=True) as conn:
        meta={r[0]:r[1] for r in conn.execute("SELECT key,value FROM crm_meta WHERE key IN ('schema_version','distribution_mode','primary_fence_id')")}
        status=[dict(r) for r in conn.execute('SELECT worker,heartbeat_at,ok,code FROM crm_runtime_status')]
        print(canonical(dict(meta=meta,workers=status,primary_ready=primary_fence.ready(conn),
            pending_projections=conn.execute('SELECT COUNT(*) FROM crm_primary_pending').fetchone()[0],
            source_conflicts=conn.execute("SELECT COUNT(*) FROM crm_migration_issues WHERE resolved_at IS NULL AND kind IN ('primary_assignment_conflict','primary_work_conflict')").fetchone()[0])))


if __name__=='__main__':main()
