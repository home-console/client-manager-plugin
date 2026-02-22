"""
Конфигурация приложения (без pydantic_settings — чтение из os.environ).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Путь к корню проекта (2 уровня вверх от этого файла)
BASE_DIR = Path(__file__).parent.parent
ENV_FILE = BASE_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE, override=False)
else:
    load_dotenv(dotenv_path=".env", override=False)


def _env(key: str, default: str, *, env_prefix: str = "") -> str:
    """Читает строку из окружения. Ключ в UPPER_SNAKE_CASE."""
    k = (env_prefix + key).replace(".", "_").upper()
    return os.environ.get(k, default)


def _env_int(key: str, default: int, *, env_prefix: str = "") -> int:
    raw = _env(key, str(default), env_prefix=env_prefix)
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool, *, env_prefix: str = "") -> bool:
    raw = _env(key, "true" if default else "false", env_prefix=env_prefix).lower()
    return raw in ("1", "true", "yes", "on")


def _env_list(key: str, default: List[str], *, env_prefix: str = "") -> List[str]:
    raw = _env(key, ",".join(default), env_prefix=env_prefix)
    if not raw.strip():
        return list(default)
    return [s.strip() for s in raw.split(",") if s.strip()]


def _env_optional(key: str, default: Optional[str] = None, *, env_prefix: str = "") -> Optional[str]:
    v = _env(key, "" if default is None else default, env_prefix=env_prefix)
    return v if v else default


def _env_optional_int(key: str, default: Optional[int] = None, *, env_prefix: str = "") -> Optional[int]:
    raw = _env(key, "", env_prefix=env_prefix)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """Настройки приложения (из os.environ)."""

    # Сервер
    server_host: str = "0.0.0.0"
    server_port: int = 10000
    server_reload: bool = True

    # SSL
    ssl_keyfile: str = "server.key"
    ssl_certfile: str = "server.crt"

    # Шифрование
    server_encryption_key: Optional[str] = None
    encryption_salt: bytes = field(default_factory=lambda: b"remote-client-salt")

    # JWT
    jwt_secret_key: Optional[str] = None
    jwt_expire_minutes: int = 60

    # Валидация команд
    command_validation_mode: str = "strict"
    max_command_length: int = 1000

    # Логирование
    log_level: str = "INFO"
    log_format: str = "text"

    # CORS
    cors_origins: List[str] = field(default_factory=lambda: ["http://localhost:3000"])

    # Ограничения
    max_connections: int = 1000
    max_command_timeout: int = 300
    max_output_size: int = 10 * 1024 * 1024

    # Файловые трансферы
    file_allowed_base_dir: Optional[str] = None
    file_max_transfer_size: Optional[int] = None
    file_per_client_quota_bytes: Optional[int] = None

    # Мониторинг
    enable_metrics: bool = True
    metrics_port: int = 9090

    # WebSocket лимиты
    websocket_max_message_bytes: int = 1 * 1024 * 1024
    websocket_max_messages_per_minute: int = 600

    # TLS downgrade
    allow_tls_downgrade: bool = False

    # S3 / MinIO
    s3_endpoint: Optional[str] = None
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = "us-east-1"
    s3_use_ssl: bool = True
    s3_presign_expiry_seconds: int = 3600
    recordings_retention_days: int = 30

    # Feature flags
    enable_cloud_services: bool = False
    enable_ssh_installer: bool = False
    enable_secrets_sync: bool = False

    # Remote client установки через SSH
    remote_client_repo: str = "remote-home-labs/home-project_remote-client"
    remote_client_release_base_url: Optional[str] = None
    remote_client_install_dir: str = "/opt/remote-client"
    remote_client_binary_name: str = "remote-client"


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Получение экземпляра настроек (singleton)."""
    global _settings_instance
    if _settings_instance is None:
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"🔧 Инициализация настроек приложения из .env: {ENV_FILE}")

        _settings_instance = Settings(
            server_host=_env("server_host", "0.0.0.0"),
            server_port=_env_int("server_port", 10000),
            server_reload=_env_bool("server_reload", True),
            ssl_keyfile=_env("ssl_keyfile", "server.key"),
            ssl_certfile=_env("ssl_certfile", "server.crt"),
            server_encryption_key=_env_optional("server_encryption_key"),
            jwt_secret_key=_env_optional("jwt_secret_key"),
            jwt_expire_minutes=_env_int("jwt_expire_minutes", 60),
            command_validation_mode=_env("command_validation_mode", "strict"),
            max_command_length=_env_int("max_command_length", 1000),
            log_level=_env("log_level", "INFO"),
            log_format=_env("log_format", "text"),
            cors_origins=_env_list("cors_origins", ["http://localhost:3000"]),
            max_connections=_env_int("max_connections", 1000),
            max_command_timeout=_env_int("max_command_timeout", 300),
            max_output_size=_env_int("max_output_size", 10 * 1024 * 1024),
            file_allowed_base_dir=_env_optional("file_allowed_base_dir"),
            file_max_transfer_size=_env_optional_int("file_max_transfer_size"),
            file_per_client_quota_bytes=_env_optional_int("file_per_client_quota_bytes"),
            enable_metrics=_env_bool("enable_metrics", True),
            metrics_port=_env_int("metrics_port", 9090),
            websocket_max_message_bytes=_env_int("websocket_max_message_bytes", 1024 * 1024),
            websocket_max_messages_per_minute=_env_int("websocket_max_messages_per_minute", 600),
            allow_tls_downgrade=_env_bool("allow_tls_downgrade", False),
            s3_endpoint=_env_optional("s3_endpoint"),
            s3_access_key_id=_env_optional("s3_access_key_id"),
            s3_secret_access_key=_env_optional("s3_secret_access_key"),
            s3_bucket=_env_optional("s3_bucket"),
            s3_region=_env_optional("s3_region", "us-east-1"),
            s3_use_ssl=_env_bool("s3_use_ssl", True),
            s3_presign_expiry_seconds=_env_int("s3_presign_expiry_seconds", 3600),
            recordings_retention_days=_env_int("recordings_retention_days", 30),
            enable_cloud_services=_env_bool("enable_cloud_services", False),
            enable_ssh_installer=_env_bool("enable_ssh_installer", False),
            enable_secrets_sync=_env_bool("enable_secrets_sync", False),
            remote_client_repo=_env(
                "remote_client_repo", "remote-home-labs/home-project_remote-client"
            ),
            remote_client_release_base_url=_env_optional("remote_client_release_base_url"),
            remote_client_install_dir=_env("remote_client_install_dir", "/opt/remote-client"),
            remote_client_binary_name=_env("remote_client_binary_name", "remote-client"),
        )

        logger.debug(
            f"✅ Настройки загружены: host={_settings_instance.server_host}, port={_settings_instance.server_port}"
        )
        if not _settings_instance.jwt_secret_key:
            raise RuntimeError("JWT_SECRET_KEY must be set via environment for client_manager")
        if _settings_instance.server_encryption_key is None:
            raise RuntimeError("SERVER_ENCRYPTION_KEY must be set via environment for client_manager")
        logger.info("✅ Все обязательные параметры конфигурации установлены")
    return _settings_instance


def init_settings() -> Settings:
    """Явная инициализация настроек при запуске приложения."""
    return get_settings()


def __getattr__(name: str):
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


settings = get_settings()
