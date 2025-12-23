"""
Конфигурация приложения
"""

import os
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# Определяем путь к корню проекта (2 уровня вверх от этого файла)
BASE_DIR = Path(__file__).parent.parent
ENV_FILE = BASE_DIR / ".env"

# Явно загружаем .env файл перед инициализацией Settings
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE, override=False)
else:
    # Также пробуем загрузить из текущей директории (fallback)
    load_dotenv(dotenv_path=".env", override=False)


class Settings(BaseSettings):
    """Настройки приложения"""
    
    # Сервер
    server_host: str = Field(default="0.0.0.0")
    server_port: int = Field(default=10000)
    server_reload: bool = Field(default=True)
    
    # SSL
    ssl_keyfile: str = Field(default="server.key")
    ssl_certfile: str = Field(default="server.crt")
    
    # Шифрование
    server_encryption_key: str = Field(default=None)
    encryption_salt: bytes = Field(default=b"remote-client-salt")
    
    # JWT
    jwt_secret_key: str = Field(default=None)
    jwt_expire_minutes: int = Field(default=60)
    
    # Валидация команд
    command_validation_mode: str = Field(default="strict")
    max_command_length: int = Field(default=1000)
    
    # Логирование
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    
    # CORS
    # CORS: по-умолчанию локальный фронтенд (без wildcard '*')
    cors_origins: List[str] = Field(
        default=["http://localhost:3000"],
    )
    
    # Ограничения
    max_connections: int = Field(default=1000)
    max_command_timeout: int = Field(default=300)
    max_output_size: int = Field(default=10 * 1024 * 1024)
    
    # Файловые трансферы
    file_allowed_base_dir: str | None = Field(default=None)
    file_max_transfer_size: int | None = Field(default=None)
    file_per_client_quota_bytes: int | None = Field(default=None)
    
    # Мониторинг
    enable_metrics: bool = Field(default=True)
    metrics_port: int = Field(default=9090)
    
    # WebSocket лимиты
    websocket_max_message_bytes: int = Field(default=1 * 1024 * 1024)
    websocket_max_messages_per_minute: int = Field(default=600)
    
    # TLS downgrade (⚠️ НЕ ВКЛЮЧАЙТЕ В ПРОДАКШЕНЕ!)
    allow_tls_downgrade: bool = Field(default=False)

    # S3 / MinIO settings for terminal recordings
    s3_endpoint: str | None = Field(default=None)
    s3_access_key_id: str | None = Field(default=None)
    s3_secret_access_key: str | None = Field(default=None)
    s3_bucket: str | None = Field(default=None)
    s3_region: str | None = Field(default="us-east-1")
    s3_use_ssl: bool = Field(default=True)
    s3_presign_expiry_seconds: int = Field(default=3600)
    recordings_retention_days: int = Field(default=30)

    # Remote client установки через SSH
    remote_client_repo: str = Field(
        default="remote-home-labs/home-project_remote-client",
        description="GitHub repo (owner/name) с релизами remote_client",
    )
    remote_client_release_base_url: Optional[str] = Field(
        default=None,
        description="Если задан, используется напрямую для загрузки бинарей remote_client",
    )
    remote_client_install_dir: str = Field(
        default="/opt/remote-client",
    )
    remote_client_binary_name: str = Field(
        default="remote-client",
    )
    
    # Pydantic v2: настройка чтения .env и игнор лишних ключей
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra='ignore',
        case_sensitive=False,  # Игнорировать регистр при чтении переменных
    )


# Глобальный экземпляр настроек (ленивая инициализация)
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """Получение экземпляра настроек (singleton)"""
    global _settings_instance
    if _settings_instance is None:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"🔧 Инициализация настроек приложения из .env файла: {ENV_FILE}")
        _settings_instance = Settings()
        logger.debug(f"✅ Настройки загружены: host={_settings_instance.server_host}, port={_settings_instance.server_port}")
        # Валидация обязательных параметров
        if not _settings_instance.jwt_secret_key:
            raise RuntimeError("JWT_SECRET_KEY must be set via environment for client_manager")
        if _settings_instance.server_encryption_key is None:
            raise RuntimeError("SERVER_ENCRYPTION_KEY must be set via environment for client_manager")
        logger.info("✅ Все обязательные параметры конфигурации установлены")
    return _settings_instance


def init_settings() -> Settings:
    """Явная инициализация настроек при запуске приложения"""
    return get_settings()


# Для обратной совместимости - settings будет инициализирован при первом обращении
# или явно через init_settings()
def __getattr__(name: str):
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Инициализируем при импорте для обратной совместимости
settings = get_settings()
