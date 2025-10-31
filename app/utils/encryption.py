from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import os
import hmac as hmac_module
import hashlib


def derive_key(password: str, salt: bytes) -> bytes:
    """Деривация ключа через PBKDF2"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=310_000
    )
    return kdf.derive(password.encode())

def encrypt_aes_gcm(key: bytes, plaintext: bytes) -> bytes:
    """AES-GCM шифрование"""
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ciphertext

def decrypt_aes_gcm(key: bytes, data: bytes) -> bytes:
    """AES-GCM дешифрование"""
    if len(data) < 12:
        raise ValueError("invalid ciphertext length")
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)

def compute_hmac(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA256"""
    return hmac_module.new(key, data, hashlib.sha256).digest()