"""
Сервис аутентификации для проверки JWT токенов
"""

import logging
import os
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError

logger = logging.getLogger(__name__)


class AuthService:
    """Сервис аутентификации"""
    
    def __init__(self, secret_key: str = None, algorithm: str = "HS256"):
        self.secret_key = secret_key or os.getenv("JWT_SECRET_KEY")
        if not self.secret_key:
            raise RuntimeError("JWT_SECRET_KEY must be set via environment for AuthService")
        self.algorithm = algorithm
        self.token_expire_minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
        
        # Черный список отозванных токенов (в продакшене использовать Redis)
        self.revoked_tokens: set = set()
        
        # Разрешения для клиентов
        self.client_permissions: Dict[str, set] = {}
    
    def create_token(self, client_id: str, permissions: list = None) -> str:
        """Создание JWT токена для клиента"""
        permissions = permissions or ["execute_commands", "read_status"]
        
        payload = {
            "client_id": client_id,
            "permissions": permissions,
            "exp": datetime.utcnow() + timedelta(minutes=self.token_expire_minutes),
            "iat": datetime.utcnow(),
            "jti": f"{client_id}_{int(time.time())}"  # JWT ID для отзыва
        }
        
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        
        # Сохраняем разрешения
        self.client_permissions[client_id] = set(permissions)
        
        logger.info(f"🔑 Создан JWT токен для клиента {client_id}")
        return token
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Проверка JWT токена"""
        try:
            # Декодируем токен
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            
            # Проверяем, не отозван ли токен
            jti = payload.get("jti")
            if jti in self.revoked_tokens:
                logger.warning(f"⚠️ Попытка использования отозванного токена: {jti}")
                return None
            
            logger.debug(f"✅ Токен валиден для клиента {payload.get('client_id')}")
            return payload
            
        except ExpiredSignatureError:
            logger.warning("⚠️ JWT токен истек")
            return None
        except InvalidTokenError as e:
            logger.warning(f"⚠️ Невалидный JWT токен: {e}")
            return None
    
    def revoke_token(self, token: str) -> bool:
        """Отзыв токена"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm], options={"verify_exp": False})
            jti = payload.get("jti")
            
            if jti:
                self.revoked_tokens.add(jti)
                logger.info(f"🚫 Токен отозван: {jti}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка отзыва токена: {e}")
            return False
    
    def has_permission(self, client_id: str, permission: str) -> bool:
        """Проверка разрешения для клиента"""
        client_perms = self.client_permissions.get(client_id, set())
        return permission in client_perms
    
    def add_permission(self, client_id: str, permission: str):
        """Добавление разрешения клиенту"""
        if client_id not in self.client_permissions:
            self.client_permissions[client_id] = set()
        
        self.client_permissions[client_id].add(permission)
        logger.info(f"➕ Добавлено разрешение '{permission}' для клиента {client_id}")
    
    def remove_permission(self, client_id: str, permission: str):
        """Удаление разрешения у клиента"""
        if client_id in self.client_permissions:
            self.client_permissions[client_id].discard(permission)
            logger.info(f"➖ Удалено разрешение '{permission}' у клиента {client_id}")
    
    def get_client_permissions(self, client_id: str) -> set:
        """Получить разрешения клиента"""
        return self.client_permissions.get(client_id, set())
    
    def cleanup_client(self, client_id: str):
        """Очистка данных клиента"""
        if client_id in self.client_permissions:
            del self.client_permissions[client_id]
            logger.debug(f"Очищены разрешения для клиента {client_id}")
    
    def get_stats(self) -> dict:
        """Получить статистику аутентификации"""
        return {
            "authenticated_clients": len(self.client_permissions),
            "revoked_tokens": len(self.revoked_tokens),
            "token_expire_minutes": self.token_expire_minutes
        }


# Глобальный экземпляр (будет заменен на DI)
auth_service = AuthService()
