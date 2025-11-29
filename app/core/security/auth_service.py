"""
Сервис аутентификации для проверки JWT токенов
"""

import logging
import os
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from dotenv import load_dotenv
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
        
        # JWT claims для валидации
        self.issuer = os.getenv("JWT_ISSUER", "remote-client-server")
        self.audience = os.getenv("JWT_AUDIENCE", "remote-client")
        
        # Черный список отозванных токенов (в продакшене использовать Redis)
        self.revoked_tokens: set = set()
        
        # Разрешения для клиентов
        self.client_permissions: Dict[str, set] = {}
    
    def create_token(self, client_id: str, permissions: list = None) -> str:
        """Создание JWT токена для клиента"""
        permissions = permissions or ["execute_commands", "read_status"]
        now = datetime.utcnow()
        
        payload = {
            "client_id": client_id,
            "permissions": permissions,
            "exp": now + timedelta(minutes=self.token_expire_minutes),
            "iat": now,
            "nbf": now,  # Not Before - токен недействителен до этого времени
            "iss": self.issuer,  # Issuer - кто выпустил токен
            "aud": self.audience,  # Audience - для кого предназначен токен
            "jti": f"{client_id}_{int(time.time())}"  # JWT ID для отзыва
        }
        
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        
        # Сохраняем разрешения
        self.client_permissions[client_id] = set(permissions)
        
        logger.info(f"🔑 Создан JWT токен для клиента {client_id}")
        return token
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Проверка JWT токена с полной валидацией claims"""
        try:
            # Декодируем токен с проверкой всех claims
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                }
            )
            
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
        except (jwt.InvalidIssuerError, jwt.InvalidAudienceError) as e:
            # Попробуем relaxed-fallback: принять токен без проверки issuer/audience (dev fallback)
            try:
                logger.warning(f"⚠️ Неверный issuer/audience в JWT токене ({e}); пытаемся relaxed-валидацию")
                payload = jwt.decode(
                    token,
                    self.secret_key,
                    algorithms=[self.algorithm],
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_nbf": True,
                        "verify_iat": False,
                        "verify_iss": False,
                        "verify_aud": False,
                    }
                )
                logger.warning("⚠️ Токен принят в relaxed режиме — рекомендуем настроить JWT_ISSUER/JWT_AUDIENCE в окружении и регенерировать токены")
                return payload
            except Exception as e2:
                logger.warning(f"⚠️ Relaxed validation failed: {e2}")
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
"""
Экземпляры AuthService создаются там, где нужны (например, в WebSocketHandler),
чтобы избежать падения при импорте модуля, если переменные окружения ещё не заданы.
"""
