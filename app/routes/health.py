"""
Health check и метрики для мониторинга
"""

import logging
import time
from typing import Dict, Any
from fastapi import APIRouter, Depends
from datetime import datetime, timezone

from ..dependencies import get_websocket_handler

logger = logging.getLogger(__name__)

router = APIRouter(tags=["monitoring"])


@router.get("/health")
async def health_check():
    """
    Простой health check endpoint
    Возвращает 200 если сервер работает
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "client-manager"
    }


@router.get("/health/ready")
async def readiness_check(handler = Depends(get_websocket_handler)):
    """
    Readiness check - проверяет готовность сервера принимать запросы
    Для Kubernetes readiness probe
    """
    try:
        # Проверяем, что обработчик инициализирован
        if handler is None:
            return {
                "status": "not_ready",
                "reason": "Handler not initialized"
            }, 503
        
        # Проверяем базовую функциональность
        clients = handler.get_all_clients()
        
        return {
            "status": "ready",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "clients_connected": len(clients)
        }
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return {
            "status": "not_ready",
            "reason": str(e)
        }, 503


@router.get("/health/live")
async def liveness_check():
    """
    Liveness check - проверяет что сервер жив
    Для Kubernetes liveness probe
    """
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/metrics")
async def get_metrics(handler = Depends(get_websocket_handler)):
    """
    Endpoint с метриками в формате для мониторинга
    """
    try:
        server_stats = handler.get_server_stats()
        
        # Базовые метрики
        metrics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": server_stats.get("uptime", 0),
            
            # WebSocket метрики
            "websocket_connections_active": server_stats.get("client_count", 0),
            "websocket_connections_total": server_stats["server_stats"].get("total_connections", 0),
            "websocket_messages_total": server_stats["server_stats"].get("total_messages", 0),
            
            # Команды
            "commands_total": server_stats["command_stats"]["stats"].get("total_commands", 0),
            "commands_successful": server_stats["command_stats"]["stats"].get("successful_commands", 0),
            "commands_failed": server_stats["command_stats"]["stats"].get("failed_commands", 0),
            "commands_blocked": server_stats["command_stats"]["stats"].get("blocked_commands", 0),
            "commands_cancelled": server_stats["command_stats"]["stats"].get("cancelled_commands", 0),
            "commands_active": server_stats["command_stats"].get("active_commands", 0),
            
            # Rate Limiter
            "rate_limiter_clients": server_stats["command_stats"]["rate_limiter"].get("total_clients", 0),
            "rate_limiter_blocked": server_stats["command_stats"]["rate_limiter"].get("total_blocked", 0),

            # Transfers summary
            "transfers_total": server_stats["transfer_stats"].get("total", 0),
            "transfers_active": server_stats["transfer_stats"].get("active", 0),
            "transfers_paused": server_stats["transfer_stats"].get("paused", 0),
            "transfers_completed": server_stats["transfer_stats"].get("completed", 0),
            "transfers_failed": server_stats["transfer_stats"].get("failed", 0),
            "transfers_cancelled": server_stats["transfer_stats"].get("cancelled", 0),
            "transfers_bytes_received": server_stats["transfer_stats"].get("bytes_received", 0),
        }
        
        return metrics
        
    except Exception as e:
        logger.error(f"Metrics endpoint error: {e}")
        return {"error": str(e)}, 500


@router.get("/metrics/prometheus")
async def get_prometheus_metrics(handler = Depends(get_websocket_handler)):
    """
    Метрики в формате Prometheus
    """
    try:
        server_stats = handler.get_server_stats()
        
        # Генерируем Prometheus формат
        lines = [
            "# HELP websocket_connections_active Текущее количество активных WebSocket соединений",
            "# TYPE websocket_connections_active gauge",
            f"websocket_connections_active {server_stats.get('client_count', 0)}",
            "",
            "# HELP websocket_connections_total Общее количество WebSocket соединений",
            "# TYPE websocket_connections_total counter",
            f"websocket_connections_total {server_stats['server_stats'].get('total_connections', 0)}",
            "",
            "# HELP commands_total Общее количество команд",
            "# TYPE commands_total counter",
            f"commands_total {server_stats['command_stats']['stats'].get('total_commands', 0)}",
            "",
            "# HELP commands_successful Успешно выполненные команды",
            "# TYPE commands_successful counter",
            f"commands_successful {server_stats['command_stats']['stats'].get('successful_commands', 0)}",
            "",
            "# HELP commands_failed Неудачные команды",
            "# TYPE commands_failed counter",
            f"commands_failed {server_stats['command_stats']['stats'].get('failed_commands', 0)}",
            "",
            "# HELP commands_blocked Заблокированные команды",
            "# TYPE commands_blocked counter",
            f"commands_blocked {server_stats['command_stats']['stats'].get('blocked_commands', 0)}",
            "",
            "# HELP rate_limiter_blocked Заблокировано rate limiter",
            "# TYPE rate_limiter_blocked counter",
            f"rate_limiter_blocked {server_stats['command_stats']['rate_limiter'].get('total_blocked', 0)}",
            "",
            "# HELP transfers_total Всего трансферов",
            "# TYPE transfers_total counter",
            f"transfers_total {server_stats['transfer_stats'].get('total', 0)}",
            "",
            "# HELP transfers_active Активные трансферы",
            "# TYPE transfers_active gauge",
            f"transfers_active {server_stats['transfer_stats'].get('active', 0)}",
            "",
            "# HELP transfers_paused Поставленные на паузу трансферы",
            "# TYPE transfers_paused gauge",
            f"transfers_paused {server_stats['transfer_stats'].get('paused', 0)}",
            "",
            "# HELP transfers_completed Завершенные трансферы",
            "# TYPE transfers_completed counter",
            f"transfers_completed {server_stats['transfer_stats'].get('completed', 0)}",
            "",
            "# HELP transfers_failed Неуспешные трансферы",
            "# TYPE transfers_failed counter",
            f"transfers_failed {server_stats['transfer_stats'].get('failed', 0)}",
            "",
            "# HELP transfers_cancelled Отмененные трансферы",
            "# TYPE transfers_cancelled counter",
            f"transfers_cancelled {server_stats['transfer_stats'].get('cancelled', 0)}",
            "",
            "# HELP transfers_bytes_received Принятые байты по всем трансферам",
            "# TYPE transfers_bytes_received counter",
            f"transfers_bytes_received {server_stats['transfer_stats'].get('bytes_received', 0)}",
            "",
            "# HELP server_uptime_seconds Время работы сервера в секундах",
            "# TYPE server_uptime_seconds gauge",
            f"server_uptime_seconds {server_stats.get('uptime', 0)}",
        ]
        
        return "\n".join(lines), {"Content-Type": "text/plain; version=0.0.4"}
        
    except Exception as e:
        logger.error(f"Prometheus metrics error: {e}")
        return f"# ERROR: {e}\n", {"Content-Type": "text/plain"}
