"""Explicit server key for headless Python; Windows profiles keep their DPAPI."""
import hashlib,hmac,os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def cipher(key_file,purpose):
    key=Path(key_file).read_bytes()
    if len(key)!=32:raise ValueError('Server secret key must contain 32 bytes')
    return AESGCM(hmac.new(key,purpose,hashlib.sha256).digest())


def seal(data,key_file,purpose):
    nonce=os.urandom(12)
    return nonce+cipher(key_file,purpose).encrypt(nonce,data,purpose)


def open_sealed(data,key_file,purpose):
    return cipher(key_file,purpose).decrypt(data[:12],data[12:],purpose)
