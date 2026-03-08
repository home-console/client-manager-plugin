"""
Безопасный обмен ключами для первоначальной синхронизации секретов.

Использует гибридное шифрование:
  1. Генерируется одноразовый AES-256-GCM ключ (session key)
  2. JSON-полезная нагрузка шифруется AES-GCM (без ограничений по размеру)
  3. Session key шифруется RSA-OAEP-SHA256 публичным ключом клиента (32 байт << 190 байт лимита)
  4. Клиент: расшифровывает session key приватным RSA-ключом, затем расшифровывает данные AES-GCM

Это решает ограничение RSA-OAEP-SHA256 (макс ~190 байт для RSA-2048), 
т.к. наш payload (encryption_key + encryption_salt) > 200 байт.
"""

import base64
import json
import logging
import os
from typing import Optional, Dict
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


class KeyExchangeService:
    """Сервис для безопасного обмена ключами"""
    
    def __init__(self):
        # Храним публичные ключи клиентов (временные, до синхронизации секретов)
        self.client_public_keys: Dict[str, rsa.RSAPublicKey] = {}
    
    def register_client_public_key(self, client_id: str, public_key_pem: str) -> bool:
        """
        Регистрация публичного ключа клиента для шифрования секретов
        
        Args:
            client_id: ID клиента (может быть "unknown" при первом подключении)
            public_key_pem: PEM формат публичного ключа RSA
        
        Returns:
            True если успешно зарегистрирован
        """
        try:
            public_key = serialization.load_pem_public_key(
                public_key_pem.encode('utf-8'),
                backend=default_backend()
            )
            
            if not isinstance(public_key, rsa.RSAPublicKey):
                logger.error(f"❌ Неверный тип ключа: ожидается RSA, получен {type(public_key)}")
                return False
            
            self.client_public_keys[client_id] = public_key
            logger.info(f"✅ Публичный ключ клиента {client_id} зарегистрирован для безопасной передачи секретов")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка регистрации публичного ключа клиента {client_id}: {e}")
            return False
    
    def encrypt_secrets_for_client(self, client_id: str, secrets: Dict) -> Optional[str]:
        """
        Шифрует секреты публичным ключом клиента используя гибридное шифрование.

        Схема (решает проблему лимита RSA-OAEP ~190 байт):
          1. Генерируем случайный AES-256-GCM session key (32 байта)
          2. Шифруем JSON-payload через AES-GCM (без ограничения по размеру)
          3. Шифруем session key через RSA-OAEP-SHA256 (32 байта << 190 байт ✅)
          4. Возвращаем base64(JSON{"k": <rsa_enc_key>, "d": <aes_enc_data>, "n": <nonce>}))

        Args:
            client_id: ID клиента
            secrets: Словарь с секретами (encryption_key, encryption_salt, ...)

        Returns:
            Base64-encoded JSON с гибридно-зашифрованными секретами, или None при ошибке
        """
        if client_id not in self.client_public_keys:
            logger.error(f"❌ Публичный ключ для клиента {client_id} не найден")
            return None

        public_key = self.client_public_keys[client_id]

        try:
            # Шаг 1: Генерируем одноразовый AES-256 session key и nonce
            session_key = os.urandom(32)   # 256-bit AES key
            nonce = os.urandom(12)          # 96-bit GCM nonce (стандарт)

            # Шаг 2: Шифруем payload через AES-256-GCM
            secrets_bytes = json.dumps(secrets, separators=(",", ":")).encode("utf-8")
            aesgcm = AESGCM(session_key)
            encrypted_data = aesgcm.encrypt(nonce, secrets_bytes, None)

            # Шаг 3: Шифруем session key через RSA-OAEP-SHA256
            # 32 байта << максимум ~190 байт для RSA-2048 ✅
            encrypted_session_key = public_key.encrypt(
                session_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )

            # Шаг 4: Упаковываем результат
            result = {
                "k": base64.b64encode(encrypted_session_key).decode("utf-8"),  # RSA-encrypted AES key
                "d": base64.b64encode(encrypted_data).decode("utf-8"),          # AES-GCM encrypted data
                "n": base64.b64encode(nonce).decode("utf-8"),                   # GCM nonce
                "enc": "RSA-OAEP-SHA256+AES-256-GCM",                          # алгоритм для клиента
            }
            result_b64 = base64.b64encode(json.dumps(result, separators=(",", ":")).encode()).decode("utf-8")

            logger.info(
                f"✅ Секреты зашифрованы для клиента {client_id} "
                f"(AES-GCM payload: {len(encrypted_data)} байт, RSA-wrapped key: {len(encrypted_session_key)} байт)"
            )
            return result_b64

        except Exception as e:
            logger.error(f"❌ Ошибка шифрования секретов для клиента {client_id}: {e}")
            return None
    
    def remove_client_key(self, client_id: str):
        """Удаление публичного ключа клиента после успешной синхронизации"""
        if client_id in self.client_public_keys:
            del self.client_public_keys[client_id]
            logger.debug(f"🗑️ Публичный ключ клиента {client_id} удален")
    
    def has_key_for_client(self, client_id: str) -> bool:
        """Проверка наличия публичного ключа для клиента"""
        return client_id in self.client_public_keys

