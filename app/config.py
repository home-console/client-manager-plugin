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


def _env_float(key: str, default: float, *, env_prefix: str = "") -> float:
    raw = _env(key, str(default), env_prefix=env_prefix)
    try:
        return float(raw)
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
    encryption_salt_str: Optional[str] = None

    # JWT
    jwt_secret_key: Optional[str] = None
    jwt_expire_minutes: int = 60
    jwt_issuer: str = "remote-client-server"
    jwt_audience: str = "remote-client"

    # Валидация команд
    command_validation_mode: str = "strict"
    max_command_length: int = 1000

    # Логирование
    log_level: str = "INFO"
    log_format: str = "text"
    log_health_checks: bool = False
    log_api_clients: bool = False

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
    max_chunk_size: int = 4 * 1024 * 1024
    upload_tmp_dir: str = "/tmp"
    upload_task_chunk_size: int = 4 * 1024 * 1024

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

    # Audit queue
    audit_queue_file: str = "/var/lib/client_manager/audit_queue.jsonl"

    # Core admin
    core_admin_url: str = "http://127.0.0.1:11000"
    admin_token: str = ""
    admin_jwt_secret: str = ""
    admin_jwt_public_key: Optional[str] = None
    admin_jwt_public_key_file: Optional[str] = None

    # Cloud / auth service
    auth_service_url: str = "http://127.0.0.1:8000"
    internal_service_token: str = "internal-service-token"
    fetch_cloud_tokens: bool = False
    yandex_disk_token: Optional[str] = None
    icloud_username: Optional[str] = None
    icloud_password: str = ""

    # Database
    database_url: Optional[str] = None

    # Celery / background tasks
    celery_broker_url: str = "amqp://guest:guest@rabbitmq:5672//"
    send_bg_retries: int = 3
    send_bg_backoff: float = 2.0
    cm_base_url: str = "http://client_manager:10000"

    # Environment
    environment: str = "production"

    # Server pinned SPKIs
    server_pinned_spkis: str = ""

    # Enrollment / WS monitor
    auto_approve_enrollments: bool = False
    ws_monitor_disable: bool = True

    # Vault
    use_vault: bool = False
    vault_url: Optional[str] = None
    vault_token: Optional[str] = None


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Получение экземпляра настроек (singleton)."""
    global _settings_instance
    if _settings_instance is None:
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"🔧 Инициализация настроек приложения из .env: {ENV_FILE}")

        # database_url: DATABASE_URL → PGBOUNCER_URL → PG_URL
        _db_url = (
            os.environ.get("DATABASE_URL")
            or os.environ.get("PGBOUNCER_URL")
            or os.environ.get("PG_URL")
            or None
        )

        # environment: ENVIRONMENT → ENV → "production"
        _environment = (
            os.environ.get("ENVIRONMENT")
            or os.environ.get("ENV")
            or "production"
        )

        _settings_instance = Settings(
            server_host=_env("server_host", "0.0.0.0"),
            server_port=_env_int("server_port", 10000),
            server_reload=_env_bool("server_reload", True),
            ssl_keyfile=_env("ssl_keyfile", "server.key"),
            ssl_certfile=_env("ssl_certfile", "server.crt"),
            server_encryption_key=_env_optional("server_encryption_key"),
            encryption_salt_str=_env_optional("server_encryption_salt"),
            jwt_secret_key=_env_optional("jwt_secret_key"),
            jwt_expire_minutes=_env_int("jwt_expire_minutes", 60),
            jwt_issuer=_env("jwt_issuer", "remote-client-server"),
            jwt_audience=_env("jwt_audience", "remote-client"),
            command_validation_mode=_env("command_validation_mode", "strict"),
            max_command_length=_env_int("max_command_length", 1000),
            log_level=_env("log_level", "INFO"),
            log_format=_env("log_format", "text"),
            log_health_checks=_env_bool("log_health_checks", False),
            log_api_clients=_env_bool("log_api_clients", False),
            cors_origins=_env_list("cors_origins", ["http://localhost:3000"]),
            max_connections=_env_int("max_connections", 1000),
            max_command_timeout=_env_int("max_command_timeout", 300),
            max_output_size=_env_int("max_output_size", 10 * 1024 * 1024),
            file_allowed_base_dir=_env_optional("file_allowed_base_dir"),
            file_max_transfer_size=_env_optional_int("file_max_transfer_size"),
            file_per_client_quota_bytes=_env_optional_int("file_per_client_quota_bytes"),
            max_chunk_size=_env_int("max_chunk_size", 4 * 1024 * 1024),
            upload_tmp_dir=_env("upload_tmp_dir", "/tmp"),
            upload_task_chunk_size=_env_int("upload_task_chunk_size", 4 * 1024 * 1024),
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
            audit_queue_file=_env("audit_queue_file", "/var/lib/client_manager/audit_queue.jsonl"),
            core_admin_url=_env("core_admin_url", "http://127.0.0.1:11000"),
            admin_token=_env("admin_token", ""),
            admin_jwt_secret=_env("admin_jwt_secret", ""),
            admin_jwt_public_key=_env_optional("admin_jwt_public_key"),
            admin_jwt_public_key_file=_env_optional("admin_jwt_public_key_file"),
            auth_service_url=_env("auth_service_url", "http://127.0.0.1:8000"),
            internal_service_token=_env("internal_service_token", "internal-service-token"),
            fetch_cloud_tokens=_env_bool("client_manager_fetch_cloud_tokens", False),
            yandex_disk_token=_env_optional("yandex_disk_token"),
            icloud_username=_env_optional("icloud_username"),
            icloud_password=_env("icloud_password", ""),
            database_url=_db_url,
            celery_broker_url=_env("celery_broker_url", "amqp://guest:guest@rabbitmq:5672//"),
            send_bg_retries=_env_int("send_bg_retries", 3),
            send_bg_backoff=_env_float("send_bg_backoff", 2.0),
            cm_base_url=_env("cm_base_url", "http://client_manager:10000"),
            environment=_environment,
            server_pinned_spkis=_env("server_pinned_spkis", ""),
            auto_approve_enrollments=_env_bool("auto_approve_enrollments", False),
            ws_monitor_disable=_env_bool("ws_monitor_disable", True),
            use_vault=_env_bool("use_vault", False),
            vault_url=_env_optional("vault_url"),
            vault_token=_env_optional("vault_token"),
        )

        logger.debug(
            f"✅ Настройки загружены: host={_settings_instance.server_host}, port={_settings_instance.server_port}"
        )
        logger.info("✅ Настройки конфигурации загружены")
    return _settings_instance


def init_settings() -> Settings:
    """Явная инициализация настроек при запуске приложения."""
    return get_settings()


def __getattr__(name: str):
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


settings = get_settings()
