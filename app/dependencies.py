"""
Dependency Injection для FastAPI
"""

from typing import Optional
from .core.websocket_handler import WebSocketHandler

# Глобальный экземпляр (инициализируется в lifespan)
_websocket_handler: Optional[WebSocketHandler] = None


def set_websocket_handler(handler: WebSocketHandler):
    """Установить глобальный экземпляр обработчика"""
    global _websocket_handler
    _websocket_handler = handler


def get_websocket_handler() -> WebSocketHandler:
    """Dependency для получения WebSocket обработчика"""
    if _websocket_handler is None:
        raise RuntimeError("WebSocket handler not initialized")
    return _websocket_handler
