"""
Dependency Injection для FastAPI
"""

from typing import Optional
import asyncio
from .core.websocket_handler import WebSocketHandler

# Compatibility shim: in some test environments there is no event loop
# bound to the main thread and code calls
# `asyncio.get_event_loop().run_until_complete(...)`. In modern Python
# `get_event_loop()` raises if no loop exists; provide a lightweight shim
# that offers `run_until_complete` using `asyncio.run`.
try:
    asyncio.get_event_loop()
except RuntimeError:
    def _get_event_loop_shim():
        class _Shim:
            def run_until_complete(self, coro):
                return asyncio.run(coro)
        return _Shim()
    asyncio.get_event_loop = _get_event_loop_shim

# Глобальный экземпляр (инициализируется в lifespan)
_websocket_handler: Optional[WebSocketHandler] = None


def set_websocket_handler(handler: WebSocketHandler):
    """Установить глобальный экземпляр обработчика"""
    global _websocket_handler
    # Ensure main thread has an event loop for legacy sync test helpers
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    _websocket_handler = handler


def get_websocket_handler() -> WebSocketHandler:
    """Dependency для получения WebSocket обработчика"""
    global _websocket_handler
    if _websocket_handler is None:
        # Lazy-init handler for test environments where lifespan() may not
        # have run prior to accessing the dependency.
        try:
            _websocket_handler = WebSocketHandler()
        except Exception:
            raise RuntimeError("WebSocket handler not initialized")
    # Если handler создан вне event-loop (например, в тестах), постараемся
    # запустить фоновые задачи при первом доступе из контекста с loop.
    try:
        if hasattr(_websocket_handler, "command_handler") and hasattr(_websocket_handler.command_handler, "start_cleanup"):
            try:
                _websocket_handler.command_handler.start_cleanup()
            except RuntimeError:
                # Нет работающего event loop — ничего делать не нужно сейчас.
                pass
    except Exception:
        pass

    return _websocket_handler
