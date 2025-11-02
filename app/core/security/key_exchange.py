"""
Безопасный обмен ключами для первоначальной синхронизации секретов
Использует RSA шифрование для передачи секретов от сервера к клиенту
"""

import base64
import logging
from typing import Optional, Dict
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
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
        Шифрует секреты публичным ключом клиента
        
        Args:
            client_id: ID клиента
            secrets: Словарь с секретами (encryption_key, encryption_salt, version, timestamp)
        
        Returns:
            Base64 зашифрованные секреты или None при ошибке
        """
        if client_id not in self.client_public_keys:
            logger.error(f"❌ Публичный ключ для клиента {client_id} не найден")
            return None
        
        public_key = self.client_public_keys[client_id]
        
        try:
            # Сериализуем секреты в JSON
            import json
            secrets_json = json.dumps(secrets)
            secrets_bytes = secrets_json.encode('utf-8')
            
            # Шифруем RSA-OAEP
            encrypted = public_key.encrypt(
                secrets_bytes,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            
            # Кодируем в base64
            encrypted_b64 = base64.b64encode(encrypted).decode('utf-8')
            
            logger.info(f"✅ Секреты зашифрованы для клиента {client_id} (размер: {len(encrypted_b64)} байт)")
            return encrypted_b64
            
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

