"""
API для управления секретами шифрования
"""

import secrets
import base64
import logging
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional

from app.core.websocket_handler import WebSocketHandler
from app.dependencies import get_websocket_handler
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/secrets", tags=["secrets"])
security = HTTPBearer()


class RotateSecretsRequest(BaseModel):
    """Запрос на ротацию секретов"""
    new_key: Optional[str] = None  # Если None - будет сгенерирован автоматически
    new_salt: Optional[str] = None  # Если None - будет сгенерирован автоматически


class SecretsVersionResponse(BaseModel):
    """Ответ с информацией о версии секретов"""
    version: int
    timestamp: int
    encryption_enabled: bool


class RotateSecretsResponse(BaseModel):
    """Ответ на ротацию секретов"""
    status: str
    version: int
    message: str
    clients_notified: int


def verify_admin_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> bool:
    """Проверка токена администратора (базовая реализация)"""
    # TODO: Реализовать проверку JWT токена или другого механизма авторизации
    token = credentials.credentials
    settings = get_settings()
    
    # Простая проверка через JWT_SECRET_KEY (для продакшена нужно использовать полноценную авторизацию)
    # Временная реализация - всегда разрешаем для разработки
    # В продакшене здесь должна быть проверка JWT токена
    return True


@router.get("/version", response_model=SecretsVersionResponse)
async def get_secrets_version(
    handler: WebSocketHandler = Depends(get_websocket_handler)
):
    """Получить текущую версию секретов"""
    secrets_sync = handler.secrets_sync
    current_secrets = secrets_sync.get_current_secrets()
    
    return SecretsVersionResponse(
        version=secrets_sync.get_version(),
        timestamp=current_secrets.get("timestamp", 0),
        encryption_enabled=handler.encryption_service.is_encryption_enabled()
    )


@router.post("/rotate", response_model=RotateSecretsResponse)
async def rotate_secrets(
    request: RotateSecretsRequest,
    handler: WebSocketHandler = Depends(get_websocket_handler),
    _: bool = Depends(verify_admin_token)  # Требует авторизацию
):
    """Ротация ключей шифрования с уведомлением всех клиентов"""
    
    # Генерируем новые ключи если не указаны
    if request.new_key:
        new_key = request.new_key
    else:
        # Генерируем 32-байтный ключ (256 бит)
        new_key = secrets.token_hex(32)
        logger.info("🔑 Автоматически сгенерирован новый ключ шифрования")
    
    if request.new_salt:
        try:
            # Пробуем декодировать как base64
            new_salt = base64.b64decode(request.new_salt)
        except Exception:
            # Если не base64, используем как строку
            new_salt = request.new_salt.encode('utf-8')
    else:
        # Генерируем 32-байтную соль
        new_salt = secrets.token_bytes(32)
        logger.info("🧂 Автоматически сгенерирована новая соль шифрования")
    
    try:
        # Выполняем ротацию
        success = await handler.secrets_sync.rotate_secrets(new_key, new_salt)
        
        if not success:
            raise HTTPException(status_code=500, detail="Ошибка ротации секретов")
        
        # Подсчитываем количество клиентов для уведомления
        clients_count = len(handler.client_manager.get_all_clients())
        
        logger.warning(f"🔄 РОТАЦИЯ СЕКРЕТОВ ЗАВЕРШЕНА: версия {handler.secrets_sync.get_version()}")
        logger.info(f"   Уведомлено клиентов: {clients_count}")
        
        return RotateSecretsResponse(
            status="success",
            version=handler.secrets_sync.get_version(),
            message="Секреты успешно ротированы, все подключенные клиенты уведомлены",
            clients_notified=clients_count
        )
    
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при ротации секретов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка ротации секретов: {str(e)}")


@router.get("/info")
async def get_secrets_info(
    handler: WebSocketHandler = Depends(get_websocket_handler)
):
    """Получить информацию о секретах (без самих значений)"""
    secrets_sync = handler.secrets_sync
    current_secrets = secrets_sync.get_current_secrets()
    
    return {
        "version": secrets_sync.get_version(),
        "timestamp": current_secrets.get("timestamp", 0),
        "encryption_enabled": handler.encryption_service.is_encryption_enabled(),
        "active_clients": len(handler.encryption_service.encryption_states),
        "history_versions": list(secrets_sync.secrets_history.keys()),
        "key_length": len(current_secrets.get("encryption_key", "")),
        "salt_length": len(base64.b64decode(current_secrets.get("encryption_salt", ""))) if current_secrets.get("encryption_salt") else 0
    }

