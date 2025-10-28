"""
Конфигурация приложения
"""

import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field


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
    
    # Мониторинг
    enable_metrics: bool = Field(default=True, env="ENABLE_METRICS")
    metrics_port: int = Field(default=9090, env="METRICS_PORT")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Глобальный экземпляр настроек
settings = Settings()
if not settings.jwt_secret_key:
    raise RuntimeError("JWT_SECRET_KEY must be set via environment for client_manager")
if settings.server_encryption_key is None:
    raise RuntimeError("SERVER_ENCRYPTION_KEY must be set via environment for client_manager")
