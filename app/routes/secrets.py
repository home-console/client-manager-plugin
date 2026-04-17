"""
API для управления секретами шифрования
"""

import secrets
import base64
import logging
from fastapi import APIRouter, Depends, HTTPException, Security, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, Dict, Any

from ..core.websocket_handler import WebSocketHandler
from ..dependencies import get_websocket_handler
from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/secrets", tags=["secrets"])
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


def _is_admin_payload(payload: Dict[str, Any]) -> bool:
    perms = payload.get("permissions", []) or []
    roles = payload.get("roles", []) or []
    return ("admin" in perms) or ("admin" in roles)


async def require_admin(
    handler: WebSocketHandler = Depends(get_websocket_handler),
    credentials: HTTPAuthorizationCredentials = Security(security),
    authorization: str | None = Header(None),
) -> Dict[str, Any]:
    """
    Dependency: require admin authorization for secret management endpoints.

    Supported:
    - Static admin token (settings.admin_token) via Bearer token
    - JWT Bearer token via handler.auth_service with 'admin' role/permission
    """
    if not handler:
        raise HTTPException(status_code=503, detail="WebSocket handler unavailable")

    token = (credentials.credentials or "").strip()
    if not token and authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Authorization header required")

    settings = get_settings()

    # Fast path: static admin token (simple operational admin access)
    admin_token = getattr(settings, "admin_token", None)
    if admin_token and token == admin_token:
        return {"subject": "admin_token", "permissions": ["admin"]}

    # JWT path: handler.auth_service must be configured
    auth_svc = getattr(handler, "auth_service", None)
    if not auth_svc:
        raise HTTPException(status_code=503, detail="Auth service unavailable")

    try:
        payload = auth_svc.verify_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if not _is_admin_payload(payload):
        raise HTTPException(status_code=403, detail="Admin permission required")

    return payload


@router.get("/version", response_model=SecretsVersionResponse)
async def get_secrets_version(
    handler: WebSocketHandler = Depends(get_websocket_handler),
    _: Dict[str, Any] = Depends(require_admin),
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
    _: Dict[str, Any] = Depends(require_admin),
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
    handler: WebSocketHandler = Depends(get_websocket_handler),
    _: Dict[str, Any] = Depends(require_admin),
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

