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


def principal_names(employee):
    """Declared names of this same principal, never inferred from spelling.

    The list is an ownership hint for the caller's durable queue, not a team
    permission grant. Ambiguous or inactive roster rows cannot add aliases.
    """
    if not employee:
        return []
    with db.connect() as conn:
        people = rows(conn)
    families = [(r['employee_id'], {r['employee'], *json.loads(r['product_names']).values()}) for r in people]
    matched = [(identifier,names) for identifier,names in families
               if db._name_key(employee) in {db._name_key(n) for n in names}]
    if len(matched) != 1:
        return [employee]
    identifier,names = matched[0]
    unique = {n for n in names if {ident for ident,family in families
              if db._name_key(n) in {db._name_key(x) for x in family}} == {identifier}}
    return sorted({employee, *unique})


def rows(conn, *, active_only=True):
    return [dict(r) for r in conn.execute(
        'SELECT r.*,i.display_name employee FROM staff_roster r '
        'JOIN employee_identities i ON i.id=r.employee_id '
        + ('WHERE r.active=1 ' if active_only else '') + 'ORDER BY r.office,i.display_name')]


def candidates(conn):
    """Keep roster decisions and add new owners from the current portfolios.

    Both the public picker and self-registration use this function. An import
    must make a new owner selectable even after the roster has been populated.
    Reading candidates never creates an account or approves a device.
    """
    all_rows = rows(conn, active_only=False)
    known = [r for r in all_rows if r['active']]
    covered = set()
    roster_ids = {r['employee_id'] for r in all_rows}
    for row in all_rows:
        covered.add(db._name_key(row['employee']))
        covered.update(db._name_key(x) for x in json.loads(row['product_names']).values())
    # Include inactive identities and their aliases: an import must never undo
    # deactivation. An enrolled person's canonical name remains selectable,
    # while aliases must not be offered as another person.
    aliases = list(conn.execute('SELECT name,employee_id FROM employee_aliases'))
    roster_ids.update(r['employee_id'] for r in aliases if db._name_key(r['name']) in covered)
    covered.update(db._name_key(r['name']) for r in aliases if r['employee_id'] in roster_ids)
    enrolled = {db._name_key(r[0]) for r in conn.execute('SELECT employee FROM accounts')}
    enrolled_ids = {r['employee_id'] for r in aliases if db._name_key(r['name']) in enrolled}
    covered.update(db._name_key(r['name']) for r in aliases
                   if r['employee_id'] in enrolled_ids and db._name_key(r['name']) not in enrolled)
    offices = {}
    for row in conn.execute('SELECT employee,city FROM employee_city'):
        offices.setdefault(db._name_key(row['employee']), set()).add(row['city'] or '')
    additions = {}
    for row in conn.execute(
            "SELECT responsible employee, 'hard' product FROM clients WHERE TRIM(COALESCE(responsible,''))<>'' "
            "UNION SELECT responsible_soft employee, 'soft' product FROM clients WHERE TRIM(COALESCE(responsible_soft,''))<>'' "
            "ORDER BY employee,product"):
        name = row['employee'].strip()
        key = db._name_key(name)
        # Product labels distinguish portfolio columns, not a second person.
        # Do not rewrite the owner or infer a new identity from that label.
        base = key.rsplit(' ', 1)
        decorated_known = len(base) == 2 and base[1] in ('хард', 'софт', 'hard', 'soft') and base[0] in covered | enrolled
        if key in covered or decorated_known:
            continue
        item = additions.setdefault(key, {'employee':name, 'office':'', 'products':[], 'manager_uid':None})
        if row['product'] not in item['products']:
            item['products'].append(row['product'])
    ambiguous = {db._name_key(n) for n in employee_identity.conflicts(conn, [r['employee'] for r in additions.values()])}
    for key, item in additions.items():
        if key in ambiguous:
            continue
        cities = offices.get(key, set())
        item['office'] = next(iter(cities)) if len(cities) == 1 else ''
        item['products'] = json.dumps(sorted(item['products']))
        known.append(item)
    return sorted(known, key=lambda r: db._name_key(r['employee']))


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
