"""
Structured logging с JSON форматом и correlation IDs
"""

import logging
import json
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from contextvars import ContextVar


# Context variable для correlation ID
correlation_id_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


def set_correlation_id(correlation_id: str):
    """Установить correlation ID для текущего контекста"""
    correlation_id_var.set(correlation_id)


def get_correlation_id() -> str:
    """Получить correlation ID из контекста или создать новый"""
    correlation_id = correlation_id_var.get()
    if not correlation_id:
        correlation_id = str(uuid.uuid4())
        correlation_id_var.set(correlation_id)
    return correlation_id


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter для логов
    """
    
    def format(self, record: logging.LogRecord) -> str:
        # Базовые поля
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }
        
        # Добавляем информацию о файле и строке
        if record.pathname:
            log_data["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }
        
        # Добавляем exception info если есть
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info)
            }
        
        # Добавляем дополнительные поля из extra
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        
        return json.dumps(log_data, ensure_ascii=False)


class StructuredLogger:
    """
    Wrapper для удобного structured logging
    """
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
    
    def _log(self, level: int, message: str, **kwargs):
        """Внутренний метод для логирования с extra полями"""
        extra_fields = {k: v for k, v in kwargs.items() if k not in ['exc_info', 'stack_info']}
        
        # Создаем LogRecord с extra полями
        record = self.logger.makeRecord(
            self.logger.name,
            level,
            "(unknown file)",
            0,
            message,
            (),
            kwargs.get('exc_info'),
            None,
            kwargs.get('stack_info')
        )
        record.extra_fields = extra_fields
        
        self.logger.handle(record)
    
    def debug(self, message: str, **kwargs):
        """Debug log с structured fields"""
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs):
        """Info log с structured fields"""
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Warning log с structured fields"""
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Error log с structured fields"""
        self._log(logging.ERROR, message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """Critical log с structured fields"""
        self._log(logging.CRITICAL, message, **kwargs)


def setup_logging(level: str = "INFO", json_format: bool = True):
    """
    Настройка structured logging для всего приложения
    
    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Использовать JSON формат (True) или обычный текст (False)
    """
    root_logger = logging.getLogger()
    
    # Очищаем существующие handlers
    root_logger.handlers.clear()
    
    # Создаем handler для stdout
    handler = logging.StreamHandler(sys.stdout)
    
    # Устанавливаем formatter
    if json_format:
        formatter = StructuredFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(correlation_id)s] - %(message)s'
        )
    
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # Устанавливаем уровень
    root_logger.setLevel(getattr(logging, level.upper()))
    
    return root_logger


def get_logger(name: str) -> StructuredLogger:
    """
    Получить structured logger по имени
    
    Usage:
        logger = get_logger(__name__)
        logger.info("User logged in", user_id=123, ip="1.2.3.4")
    """
    return StructuredLogger(name)


# Middleware для FastAPI
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware для логирования HTTP запросов с correlation ID
    """
    
    async def dispatch(self, request: Request, call_next):
        # Генерируем или извлекаем correlation ID
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        set_correlation_id(correlation_id)
        
        # Пропускаем логирование автоматических запросов для уменьшения шума
        import os
        log_health_checks = os.getenv("LOG_HEALTH_CHECKS", "false").lower() == "true"
        log_api_clients = os.getenv("LOG_API_CLIENTS", "false").lower() == "true"

        # Health-чек запросы
        is_health_check = request.url.path in ["/health", "/api/health"] and request.method == "GET" and not log_health_checks

        # Автоматические запросы веб-интерфейса (обновление списка клиентов каждые 5 секунд)
        is_auto_refresh = (
            request.url.path in ["/api/clients"] and
            request.method == "GET" and
            not log_api_clients
        )

        # Пропускаем логирование если это автоматический запрос
        skip_logging = is_health_check or is_auto_refresh

        if not skip_logging:
            # Логируем входящий запрос
            logger = get_logger(__name__)
            logger.info(
                "Incoming request",
                method=request.method,
                path=request.url.path,
                client_ip=request.client.host if request.client else None,
                correlation_id=correlation_id
            )

        # Обрабатываем запрос
        response = await call_next(request)

        # Добавляем correlation ID в response headers
        response.headers["X-Correlation-ID"] = correlation_id

        if not skip_logging:
            # Логируем ответ
            logger = get_logger(__name__)
            logger.info(
                "Request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                correlation_id=correlation_id
            )
        elif is_health_check and response.status_code >= 400:
            # Логируем только проблемные health-чек запросы
            logger = get_logger(__name__)
            logger.warning(
                "Health check failed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                correlation_id=correlation_id
            )
        elif is_auto_refresh and response.status_code >= 400:
            # Логируем только проблемные автоматические запросы
            logger = get_logger(__name__)
            logger.warning(
                "Auto refresh failed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                correlation_id=correlation_id
            )
        
        return response
