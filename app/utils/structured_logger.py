"""
Structured logging с JSON форматом и correlation IDs
"""

import logging
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from contextvars import ContextVar


# Context variable для correlation ID
correlation_id_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)

# Имя плагина для текстового формата логов: [LEVEL] [plugin_name] message
plugin_name_var: ContextVar[str] = ContextVar("plugin_name", default="client_manager")


def set_plugin_name(name: str) -> None:
    """Установить имя плагина для логов (например при загрузке из Core Runtime)."""
    plugin_name_var.set(name)


def get_plugin_name() -> str:
    """Текущее имя плагина из контекста."""
    return plugin_name_var.get()


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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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


# Стандартный формат текстовых логов: [тип] [плагин] сообщение
PLUGIN_LOG_FMT = "[%(levelname)s] [%(plugin)s] %(message)s"


class PluginTextFormatter(logging.Formatter):
    """
    Форматтер для текстовых логов с подстановкой имени плагина из контекста.
    Если в record передан extra['plugin'] — используется он, иначе get_plugin_name().
    """
    def __init__(self, fmt: str = PLUGIN_LOG_FMT, *args, **kwargs):
        super().__init__(fmt, *args, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        if not getattr(record, "plugin", None):
            record.plugin = get_plugin_name()
        return super().format(record)


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


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    plugin_name: Optional[str] = None,
):
    """
    Настройка structured logging для всего приложения.

    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Использовать JSON формат (True) или текст (False)
        plugin_name: Имя плагина для текстового формата ([LEVEL] [plugin_name] message).
                     Если не задано, используется контекст (set_plugin_name) или "client_manager".
    """
    if plugin_name is not None:
        set_plugin_name(plugin_name)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)

    if json_format:
        formatter = StructuredFormatter()
    else:
        formatter = PluginTextFormatter(PLUGIN_LOG_FMT)
    
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # Устанавливаем уровень
    root_logger.setLevel(getattr(logging, level.upper()))
    # Подавляем детальные access-логи от uvicorn в INFO, чтобы избежать флуда
    try:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
    except Exception:
        pass
    
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
        # Теперь учитываем запросы и к конкретным клиентам, т.е. /api/clients/*
        path = request.url.path or ""
        is_auto_refresh = (
            path.startswith("/api/clients") and
            request.method == "GET" and
            not log_api_clients
        )

        # Частые polling-запросы статуса трансферов (например /api/files/transfers/{id}/status)
        is_transfer_status = False
        try:
            # Простая проверка: путь начинается с /api/files/transfers и содержит /status
            if path.startswith("/api/files/transfers") and request.method == "GET":
                # Любые GET к /api/files/transfers/* считаем частыми polling-запросами
                is_transfer_status = True
        except Exception:
            is_transfer_status = False

        # Пропускаем логирование если это автоматический или частый polling запрос
        skip_logging = is_health_check or is_auto_refresh or is_transfer_status

        if not skip_logging:
            # Логируем входящий запрос
            logger = get_logger(__name__)
            # Для автоматических/частых запросов используем debug, чтобы не засорять INFO
            if is_auto_refresh:
                logger.debug(
                    "Incoming auto request",
                    method=request.method,
                    path=request.url.path,
                    client_ip=request.client.host if request.client else None,
                    correlation_id=correlation_id
                )
            else:
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
            if is_auto_refresh:
                logger.debug(
                    "Auto request completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    correlation_id=correlation_id
                )
            else:
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
