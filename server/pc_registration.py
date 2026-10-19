"""One-use signed installation challenges, followed by a manager's observed approval.

Proof of key possession binds the pending profile to an installation. It does
not attest physical hardware or office location; the manager must observe them.
"""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
import db


def init_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS pc_registration_challenges (
        id TEXT PRIMARY KEY, nonce TEXT NOT NULL, public_key TEXT NOT NULL,
        fingerprint TEXT NOT NULL, label TEXT NOT NULL, expires_at TEXT NOT NULL,
        consumed_at TEXT, issued_ip TEXT NOT NULL)''')


def challenge(public_key, fingerprint, label, ip):
    try:
        raw=base64.b64decode(public_key,validate=True)
        Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError,TypeError):
        return {'ok':False,'error':'Приложение не передало ключ компьютера.'}
    if not fingerprint or len(fingerprint)>128 or hashlib.sha256(raw).hexdigest()!=fingerprint:
        return {'ok':False,'error':'Идентификатор компьютера не соответствует ключу.'}
    now=datetime.now(timezone.utc)
    identifier,nonce=secrets.token_urlsafe(24),secrets.token_urlsafe(32)
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM pc_registration_challenges WHERE expires_at<?',((now-timedelta(days=1)).isoformat(),))
        # Bound unauthenticated storage even when callers generate new keys.
        recent=c.execute('SELECT count(*) FROM pc_registration_challenges WHERE issued_ip=? AND expires_at>?',
                         (str(ip)[:64],now.isoformat())).fetchone()[0]
        if recent >= 120:
            return {'ok':False,'error':'Слишком много запросов входа. Повторите через пять минут.'}
        c.execute('INSERT INTO pc_registration_challenges VALUES(?,?,?,?,?,?,NULL,?)',
            (identifier,nonce,public_key,fingerprint,str(label or 'Компьютер')[:120],
             (now+timedelta(minutes=5)).isoformat(),str(ip)[:64]))
        c.commit()
    return {'ok':True,'challenge_id':identifier,'nonce':nonce}


def consume(conn, proof, employee, username, password):
    if not isinstance(proof,dict): return None
    r=conn.execute('SELECT * FROM pc_registration_challenges WHERE id=?',
                   (str(proof.get('challenge_id') or ''),)).fetchone()
    if not r or r['consumed_at'] or r['expires_at']<=datetime.now(timezone.utc).isoformat(): return None
    message='\n'.join((r['id'],r['nonce'],employee,username,hashlib.sha256(password.encode()).hexdigest()))
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(r['public_key'],validate=True)).verify(
            base64.b64decode(proof.get('signature',''),validate=True),message.encode())
    except (ValueError,TypeError,InvalidSignature): return None
    conn.execute('UPDATE pc_registration_challenges SET consumed_at=? WHERE id=?',
                 (datetime.now(timezone.utc).isoformat(),r['id']))
    return dict(r)
