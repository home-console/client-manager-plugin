"""
Конфигурация приложения
"""

import os
from pathlib import Path
from typing import List
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
    server_host: str = Field(default="0.0.0.0", env="SERVER_HOST")
    server_port: int = Field(default=10000, env="SERVER_PORT")
    server_reload: bool = Field(default=True, env="SERVER_RELOAD")
    
    # SSL
    ssl_keyfile: str = Field(default="server.key", env="SSL_KEYFILE")
    ssl_certfile: str = Field(default="server.crt", env="SSL_CERTFILE")
    
    # Шифрование
    server_encryption_key: str = Field(
        default=None,
        env="SERVER_ENCRYPTION_KEY"
    )
    encryption_salt: bytes = Field(default=b"remote-client-salt", env="ENCRYPTION_SALT")
    
    # JWT
    jwt_secret_key: str = Field(
        default=None,
        env="JWT_SECRET_KEY"
    )
    jwt_expire_minutes: int = Field(default=60, env="JWT_EXPIRE_MINUTES")
    
    # Валидация команд
    command_validation_mode: str = Field(default="strict", env="COMMAND_VALIDATION_MODE")
    max_command_length: int = Field(default=1000, env="MAX_COMMAND_LENGTH")
    
    # Логирование
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = Field(default="json", env="LOG_FORMAT")
    
    # CORS
    cors_origins: List[str] = Field(
        default=["http://localhost:*", "https://localhost:*"],
        env="CORS_ORIGINS"
    )
    
    # Ограничения
    max_connections: int = Field(default=1000, env="MAX_CONNECTIONS")
    max_command_timeout: int = Field(default=300, env="MAX_COMMAND_TIMEOUT")
    max_output_size: int = Field(default=10 * 1024 * 1024, env="MAX_OUTPUT_SIZE")
    
    # Файловые трансферы
    file_allowed_base_dir: str | None = Field(default=None, env="FILE_ALLOWED_BASE_DIR")
    file_max_transfer_size: int | None = Field(default=None, env="FILE_MAX_TRANSFER_SIZE")
    file_per_client_quota_bytes: int | None = Field(default=None, env="FILE_PER_CLIENT_QUOTA_BYTES")
    
    # Мониторинг
    enable_metrics: bool = Field(default=True, env="ENABLE_METRICS")
    metrics_port: int = Field(default=9090, env="METRICS_PORT")
    
    # WebSocket лимиты
    websocket_max_message_bytes: int = Field(default=1 * 1024 * 1024, env="WS_MAX_MESSAGE_BYTES")
    websocket_max_messages_per_minute: int = Field(default=600, env="WS_MAX_MESSAGES_PER_MINUTE")
    
    # TLS downgrade (⚠️ НЕ ВКЛЮЧАЙТЕ В ПРОДАКШЕНЕ!)
    allow_tls_downgrade: bool = Field(default=False, env="ALLOW_TLS_DOWNGRADE")
    
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
