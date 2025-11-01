"""
Менеджер безопасного хранения секретов для Python сервера
Использует keyring (System Keychain/Credential Manager) или интеграцию с Vault
"""

import os
import base64
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Попытка импорта keyring (опционально)
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    logger.warning("⚠️ keyring не установлен - используйте: pip install keyring")

# Попытка импорта hvac для Vault (опционально)
try:
    import hvac
    HVAC_AVAILABLE = True
except ImportError:
    HVAC_AVAILABLE = False


class SecretsManager:
    """Менеджер безопасного хранения секретов"""
    
    # Имя сервиса для keyring
    SERVICE_NAME = "remote-client-server"
    
    # Ключи секретов
    KEY_ENCRYPTION = "encryption_key"
    KEY_SALT = "encryption_salt"
    KEY_JWT_SECRET = "jwt_secret"
    
    def __init__(self, username: str = None, use_vault: bool = False, vault_url: str = None, vault_token: str = None):
        """
        Инициализация менеджера секретов
        
        Args:
            username: Имя пользователя для keyring (по умолчанию "server")
            use_vault: Использовать HashiCorp Vault вместо keyring
            vault_url: URL Vault сервера
            vault_token: Токен для доступа к Vault
        """
        self.username = username or "server"
        self.use_vault = use_vault and HVAC_AVAILABLE
        self.use_keyring = KEYRING_AVAILABLE and not use_vault
        
        # Vault клиент
        self.vault_client = None
        if self.use_vault:
            try:
                self.vault_client = hvac.Client(url=vault_url, token=vault_token)
                if not self.vault_client.is_authenticated():
                    logger.error("❌ Vault authentication failed")
                    self.use_vault = False
                else:
                    logger.info("✅ Vault connected successfully")
            except Exception as e:
                logger.error(f"❌ Vault connection failed: {e}")
                self.use_vault = False
    
    def is_secure_storage_available(self) -> bool:
        """Проверка доступности безопасного хранилища"""
        return self.use_keyring or self.use_vault
    
    def get_encryption_key(self) -> Optional[str]:
        """Получение ключа шифрования"""
        return self._get_secret(self.KEY_ENCRYPTION, "SERVER_ENCRYPTION_KEY")
    
    def set_encryption_key(self, key: str) -> bool:
        """Сохранение ключа шифрования"""
        return self._set_secret(self.KEY_ENCRYPTION, key)
    
    def get_encryption_salt(self) -> Optional[bytes]:
        """Получение соли для шифрования"""
        salt_str = self._get_secret(self.KEY_SALT, "SERVER_ENCRYPTION_SALT")
        if not salt_str:
            return None
        
        # Пробуем декодировать как base64
        try:
            return base64.b64decode(salt_str)
        except Exception:
            # Если не base64, используем как есть
            return salt_str.encode('utf-8')
    
    def set_encryption_salt(self, salt: bytes) -> bool:
        """Сохранение соли для шифрования"""
        # Кодируем в base64 для безопасного хранения
        salt_str = base64.b64encode(salt).decode('ascii')
        return self._set_secret(self.KEY_SALT, salt_str)
    
    def get_jwt_secret(self) -> Optional[str]:
        """Получение JWT секретного ключа"""
        return self._get_secret(self.KEY_JWT_SECRET, "JWT_SECRET_KEY")
    
    def set_jwt_secret(self, secret: str) -> bool:
        """Сохранение JWT секретного ключа"""
        return self._set_secret(self.KEY_JWT_SECRET, secret)
    
    def _get_secret(self, key: str, env_var: str) -> Optional[str]:
        """
        Получение секрета из хранилища
        
        Приоритет:
        1. Vault (если настроен)
        2. System Keyring (если доступен)
        3. Переменная окружения (fallback)
        """
        # Приоритет 1: Vault
        if self.use_vault:
            try:
                secret_path = f"secret/data/remote-client/{key}"
                response = self.vault_client.secrets.kv.v2.read_secret_version(path=secret_path)
                value = response['data']['data'].get(key)
                if value:
                    return value
            except Exception as e:
                logger.debug(f"Vault lookup failed for {key}: {e}")
        
        # Приоритет 2: System Keyring
        if self.use_keyring:
            try:
                value = keyring.get_password(self.SERVICE_NAME, self._make_key(key))
                if value:
                    return value
            except Exception as e:
                logger.debug(f"Keyring lookup failed for {key}: {e}")
        
        # Приоритет 3: Environment Variable (fallback)
        value = os.getenv(env_var)
        if value:
            logger.warning(f"⚠️ ВНИМАНИЕ: Используется секрет из ENV ({env_var}) вместо безопасного хранилища!")
            logger.warning(f"💡 Рекомендуется использовать: python manage_secrets.py set {key}")
            return value
        
        return None
    
    def _set_secret(self, key: str, value: str) -> bool:
        """Сохранение секрета в хранилище"""
        if not value:
            logger.error(f"❌ Значение секрета {key} не может быть пустым")
            return False
        
        # Vault
        if self.use_vault:
            try:
                secret_path = f"secret/data/remote-client/{key}"
                self.vault_client.secrets.kv.v2.create_or_update_secret(
                    path=secret_path,
                    secret={key: value}
                )
                logger.info(f"✅ Секрет {key} сохранен в Vault")
                return True
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения в Vault: {e}")
                return False
        
        # System Keyring
        if self.use_keyring:
            try:
                keyring.set_password(self.SERVICE_NAME, self._make_key(key), value)
                logger.info(f"✅ Секрет {key} сохранен в System Keyring")
                return True
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения в Keyring: {e}")
                return False
        
        logger.error("❌ Безопасное хранилище недоступно - используйте переменные окружения")
        return False
    
    def delete_secret(self, key: str) -> bool:
        """Удаление секрета из хранилища"""
        if self.use_vault:
            try:
                secret_path = f"secret/data/remote-client/{key}"
                self.vault_client.secrets.kv.v2.delete_metadata_and_all_versions(path=secret_path)
                logger.info(f"✅ Секрет {key} удален из Vault")
                return True
            except Exception as e:
                logger.error(f"❌ Ошибка удаления из Vault: {e}")
                return False
        
        if self.use_keyring:
            try:
                keyring.delete_password(self.SERVICE_NAME, self._make_key(key))
                logger.info(f"✅ Секрет {key} удален из System Keyring")
                return True
            except Exception as e:
                logger.error(f"❌ Ошибка удаления из Keyring: {e}")
                return False
        
        return False
    
    def list_secrets(self) -> list:
        """Список установленных секретов (без значений)"""
        secrets = []
        keys = [self.KEY_ENCRYPTION, self.KEY_SALT, self.KEY_JWT_SECRET]
        
        for key in keys:
            if self.use_keyring:
                try:
                    value = keyring.get_password(self.SERVICE_NAME, self._make_key(key))
                    if value:
                        secrets.append(key)
                except Exception:
                    pass
            elif self.use_vault:
                try:
                    secret_path = f"secret/data/remote-client/{key}"
                    response = self.vault_client.secrets.kv.v2.read_secret_version(path=secret_path)
                    if response['data']['data'].get(key):
                        secrets.append(key)
                except Exception:
                    pass
        
        return secrets
    
    def validate_secrets(self) -> tuple[bool, list]:
        """
        Проверка наличия всех необходимых секретов
        
        Returns:
            (success, missing_secrets)
        """
        missing = []
        
        if not self.get_encryption_key():
            missing.append('encryption_key')
        
        if not self.get_encryption_salt():
            missing.append('encryption_salt')
        
        if not self.get_jwt_secret():
            missing.append('jwt_secret')
        
        return (len(missing) == 0, missing)
    
    def get_storage_info(self) -> Dict[str, Any]:
        """Информация о хранилище секретов"""
        info = {
            "service_name": self.SERVICE_NAME,
            "username": self.username,
            "keyring_available": KEYRING_AVAILABLE,
            "vault_available": HVAC_AVAILABLE,
            "secure_storage_available": self.is_secure_storage_available()
        }
        
        if self.use_vault:
            info["storage_type"] = "HashiCorp Vault (Most Secure)"
            info["location"] = self.vault_client.url if self.vault_client else "N/A"
        elif self.use_keyring:
            info["storage_type"] = "System Keyring (Secure)"
            info["location"] = self._get_keyring_location()
        else:
            info["storage_type"] = "Environment Variables (Less Secure)"
            info["location"] = "ENV"
        
        return info
    
    def _make_key(self, key: str) -> str:
        """Создание уникального ключа для keyring"""
        return f"{self.username}_{key}"
    
    def _get_keyring_location(self) -> str:
        """Определение местоположения keyring в зависимости от ОС"""
        import platform
        system = platform.system()
        
        if system == "Darwin":
            return "macOS Keychain"
        elif system == "Windows":
            return "Windows Credential Manager"
        elif system == "Linux":
            return "Linux Secret Service (gnome-keyring/kwallet)"
        else:
            return "System Keyring"


# Глобальный экземпляр (singleton)
_secrets_manager = None

def get_secrets_manager() -> SecretsManager:
    """Получение глобального экземпляра SecretsManager"""
    global _secrets_manager
    if _secrets_manager is None:
        # Проверяем настройки Vault из ENV
        use_vault = os.getenv("USE_VAULT", "false").lower() == "true"
        vault_url = os.getenv("VAULT_URL")
        vault_token = os.getenv("VAULT_TOKEN")
        
        _secrets_manager = SecretsManager(
            use_vault=use_vault,
            vault_url=vault_url,
            vault_token=vault_token
        )
    
    return _secrets_manager
