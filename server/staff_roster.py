"""Staff without portfolios remain selectable. This roster never invents contracts."""
import hashlib
import json
import db
import employee_identity


def init_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS staff_roster (
        employee_id TEXT PRIMARY KEY REFERENCES employee_identities(id),
        office TEXT NOT NULL DEFAULT '', direction TEXT NOT NULL DEFAULT '',
        products TEXT NOT NULL DEFAULT '[]', manager_uid INTEGER,
        active INTEGER NOT NULL DEFAULT 1, source_sha256 TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    columns={r[1] for r in conn.execute('PRAGMA table_info(staff_roster)')}
    for name,default in [('offices','[]'),('product_names','{}')]:
        if name not in columns:
            conn.execute(f"ALTER TABLE staff_roster ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")


def product_employee(employee,product):
    """Explicit roster mapping; historical contract authors/queues keep their names."""
    with db.connect() as conn:
        for row in rows(conn):
            names=json.loads(row['product_names'])
            if db._name_key(employee) in {db._name_key(x) for x in [row['employee'],*names.values()]}:
                return names.get(product,row['employee'])
    return employee


def rows(conn, *, active_only=True):
    return [dict(r) for r in conn.execute(
        'SELECT r.*,i.display_name employee FROM staff_roster r '
        'JOIN employee_identities i ON i.id=r.employee_id '
        + ('WHERE r.active=1 ' if active_only else '') + 'ORDER BY r.office,i.display_name')]


def candidates(conn):
    known = rows(conn)
    if conn.execute('SELECT 1 FROM staff_roster LIMIT 1').fetchone():
        return known
    # Compatibility for an unmigrated database; the operator sees roster coverage.
    names = {r[0] for r in conn.execute(
        "SELECT responsible FROM clients UNION SELECT responsible_soft FROM clients") if r[0]}
    return [{'employee':n, 'office':'', 'products':'[]', 'manager_uid':None} for n in sorted(names)]


def manager_employees(conn, mgr):
    result=[]
    cities={str(x).strip().casefold() for x in mgr.get('office_cities') or []}
    names={db._name_key(x) for x in mgr.get('employees') or []}
    direction=str(mgr.get('direction') or '').strip()
    uid=int(mgr['telegram_id'])
    for r in rows(conn):
        if r['manager_uid'] is not None and r['manager_uid'] != uid:
            continue
        aliases=json.loads(r['product_names'])
        offices=json.loads(r['offices']) or [r['office']]
        covered=(bool({db._name_key(x) for x in [r['employee'],*aliases.values()]} & names)
                 or bool({x.casefold() for x in offices} & cities)
                 or bool(direction and (direction=='all' or direction==r['direction'])))
        # Assigning a manager in the roster cannot expand that manager's scope.
        if covered:
            result.append(r['employee'])
    return result


def preview(items):
    if not isinstance(items,list) or not 1 <= len(items) <= 10000:
        raise ValueError('Передайте список сотрудников (1–10000 строк).')
    normalized=[]
    keys=set()
    product_owners={}
    with db.connect() as c:
        for item in items:
            if not isinstance(item,dict): raise ValueError('Некорректная строка справочника.')
            name=str(item.get('employee') or '').strip()
            key=db._name_key(name)
            products=sorted(set(item.get('products') or []))
            if not name or len(name)>120 or key in keys or not products or any(x not in ('hard','soft') for x in products):
                raise ValueError('Проверьте ФИО, повторения и направления Хард/Софт.')
            keys.add(key)
            manager=item.get('manager_uid')
            if manager is not None and (type(manager) is not int or not c.execute(
                    "SELECT 1 FROM managers WHERE telegram_id=? AND COALESCE(NULLIF(role,''),'manager') IN ('manager','full')",(manager,)).fetchone()):
                raise ValueError('Руководитель не найден или не имеет права подтверждения.')
            direction=str(item.get('direction') or '').strip()
            if direction not in ('tj','uz',''): raise ValueError('Страна: tj или uz.')
            office=str(item.get('office') or '').strip()[:120]
            offices=item.get('offices') or [office]
            aliases=item.get('product_names') or {}
            if not isinstance(offices,list) or any(not isinstance(x,str) or len(x)>120 for x in offices):
                raise ValueError('Проверьте список офисов.')
            if not isinstance(aliases,dict) or any(k not in products or not isinstance(v,str) or not v.strip() or len(v)>120 for k,v in aliases.items()):
                raise ValueError('Проверьте имена сотрудника в портфелях Хард/Софт.')
            aliases={k:v.strip() for k,v in aliases.items()}
            for product in products:
                alias_key=(product,db._name_key(aliases.get(product,name)))
                if alias_key in product_owners:
                    raise ValueError('Один портфель указан у разных сотрудников.')
                product_owners[alias_key]=key
            normalized.append(dict(employee=name,office=str(item.get('office') or '').strip()[:120],
                offices=sorted(set(offices)),product_names=aliases,
                direction=direction,products=products,manager_uid=manager,active=bool(item.get('active',True))))
        if employee_identity.conflicts(c,[x['employee'] for x in normalized]):
            raise ValueError('Неоднозначные ФИО: сначала уточните идентичности сотрудников.')
        current=rows(c,active_only=False)
    canonical=json.dumps(normalized,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return {'ok':True,'items':normalized,'sha256':hashlib.sha256(canonical.encode()).hexdigest(),
            'rows':len(normalized),'current_rows':len(current),'deactivation':'only explicit inactive rows'}


def apply(items, expected_sha256, *, by):
    checked=preview(items)
    if checked['sha256'] != expected_sha256:
        return {'ok':False,'error':'Предпросмотр изменился. Проверьте список заново.'}
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        employee_identity.ensure(c,[x['employee'] for x in checked['items']])
        for item in checked['items']:
            identity=c.execute('SELECT employee_id FROM employee_aliases WHERE name=?',(item['employee'],)).fetchone()[0]
            c.execute('INSERT INTO staff_roster(employee_id,office,direction,products,manager_uid,active,source_sha256,offices,product_names) '
                'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(employee_id) DO UPDATE SET office=excluded.office,'
                'direction=excluded.direction,products=excluded.products,manager_uid=excluded.manager_uid,'
                'offices=excluded.offices,product_names=excluded.product_names,'
                'active=excluded.active,source_sha256=excluded.source_sha256,updated_at=CURRENT_TIMESTAMP',
                (identity,item['office'],item['direction'],json.dumps(item['products']),item['manager_uid'],
                 int(item['active']),expected_sha256,json.dumps(item['offices'],ensure_ascii=False),json.dumps(item['product_names'],ensure_ascii=False)))
        c.commit()
    db.audit_access(0,'staff_roster_apply',str(checked['rows']),f'by={by} sha256={expected_sha256}')
    return {'ok':True,'rows':checked['rows'],'sha256':expected_sha256}
