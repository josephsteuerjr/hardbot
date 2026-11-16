"""A real listener verifies TLS and process ownership without working services."""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def test_listener_requires_tls_and_releases_its_profile_after_restart(tmp_path):
    source=Path(__file__).parents[1]
    env={**os.environ,'PYTHONPATH':os.pathsep.join([str(source/'server'),str(source)]),'PYTHONIOENCODING':'utf-8'}
    args=[sys.executable,'-m','crm.mobile_server','--demo-home',str(tmp_path/'state')]
    denied=subprocess.run([*args,'--host','0.0.0.0'],env=env,cwd=source,capture_output=True,timeout=30)
    assert denied.returncode==2 and b'requires its own TLS' in denied.stderr
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
          .not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
          .sign(key,hashes.SHA256()))
    certificate=tmp_path/'tls.pem';private=tmp_path/'tls-key.pem';receipt=tmp_path/'listener.json'
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    args += ['--tls-cert',str(certificate),'--tls-key',str(private),'--receipt',str(receipt)]
    context=ssl.create_default_context(cafile=str(certificate))
    for attempt in range(2):
        receipt.unlink(missing_ok=True)
        with (tmp_path/f'listener-{attempt}.log').open('wb') as log:
            child=subprocess.Popen(args,env=env,cwd=source,stdout=log,stderr=log,creationflags=0x08000000 if os.name=='nt' else 0)
            try:
                deadline=time.monotonic()+30
                while not receipt.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.1)
                assert receipt.exists(),(tmp_path/f'listener-{attempt}.log').read_text(encoding='utf-8')
                url=json.loads(receipt.read_text())['url']+'health'
                assert url.startswith('https://')
                with urllib.request.urlopen(url,context=context,timeout=5) as response:
                    assert json.load(response)==dict(ok=True,service='crm-mobile',executor='synthetic')
                duplicate=subprocess.run(args,env=env,cwd=source,capture_output=True,timeout=30)
                assert duplicate.returncode!=0
            finally:
                child.terminate();child.wait(timeout=10)
