"""
Client Manager Plugin - управление удаленными клиентами.

Интеграция FastAPI приложения как in-process плагина Core Runtime.
Запускает uvicorn сервер внутри плагина для обслуживания WebSocket и REST API.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional
from pathlib import Path

from core.base_plugin import BasePlugin, PluginMetadata

if TYPE_CHECKING:
    from core.runtime import CoreRuntime

logger = logging.getLogger(__name__)


class ClientManagerPlugin(BasePlugin):
    """
    Плагин для управления удаленными клиентами.
    
    Возможности:
    - WebSocket соединения с агентами
    - REST API для управления клиентами
    - Отправка команд на агенты
    - Файловые трансферы
    - Terminal sessions
    - JWT аутентификация
    
    Интеграция с Runtime:
    - Регистрирует сервисы в service_registry
    - Публикует события в event_bus
    - Использует storage для персистентности
    """
    
    def __init__(self, runtime: Optional["CoreRuntime"] = None):
        super().__init__(runtime)
        self.server_task: Optional[asyncio.Task] = None
        self.server: Optional[object] = None  # uvicorn.Server instance
        self.handler: Optional[object] = None  # WebSocketHandler instance
        
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="client_manager",
            version="1.0.0",
            description="Управление удаленными клиентами через WebSocket и REST API",
            author="Home Console",
            dependencies=[]
        )
    
    async def on_load(self) -> None:
        """Загрузка плагина - регистрация сервисов."""
        await super().on_load()
        
        # Импортируем здесь, чтобы избежать циклических зависимостей
        from .plugin_services import register_services
        
        # Регистрируем сервисы в service_registry
        await register_services(self)
        
        await self.runtime.service_registry.call(
            "logger.log",
            level="info",
            message="Client Manager сервисы зарегистрированы",
            plugin="client_manager"
        )
    
    async def on_start(self) -> None:
        """Запуск плагина - старт FastAPI сервера."""
        await super().on_start()
        
        try:
            # Импортируем здесь для ленивой загрузки
            import uvicorn
            from .app.main import create_app
            from .app.config import settings
            from .app.dependencies import set_websocket_handler, get_websocket_handler
            
            # Создаем FastAPI приложение
            app = create_app()
            
            # Получаем WebSocket handler из приложения (он создается в lifespan)
            # Мы не можем получить его сразу, так как lifespan еще не выполнился
            # Поэтому мы сохраним ссылку позже через callback
            
            # Интегрируем event_bus
            from .plugin_events import setup_event_integration
            # setup_event_integration будет вызван после старта uvicorn
            
            # Конфигурация uvicorn
            config = uvicorn.Config(
                app,
                host=settings.server_host,
                port=settings.server_port,
                log_level=settings.log_level.lower(),
                access_log=False,  # Используем structured logging
                ssl_keyfile=settings.ssl_keyfile if Path(settings.ssl_keyfile).exists() else None,
                ssl_certfile=settings.ssl_certfile if Path(settings.ssl_certfile).exists() else None,
            )
            
            # Создаем uvicorn server
            self.server = uvicorn.Server(config)
            
            # Запускаем сервер в фоновой задаче
            self.server_task = asyncio.create_task(self.server.serve())
            
            await self.runtime.service_registry.call(
                "logger.log",
                level="info",
                message=f"Client Manager сервер запущен на {settings.server_host}:{settings.server_port}",
                plugin="client_manager"
            )
            
            # Ждем немного, чтобы сервер стартовал и lifespan выполнился
            await asyncio.sleep(1)
            
            # Получаем handler после старта
            self.handler = get_websocket_handler()
            
            # Настраиваем интеграцию с event_bus
            if self.handler:
                await setup_event_integration(self, self.handler)
                await self.runtime.service_registry.call(
                    "logger.log",
                    level="info",
                    message="Event bus интеграция настроена",
                    plugin="client_manager"
                )
            
        except Exception as e:
            await self.runtime.service_registry.call(
                "logger.log",
                level="error",
                message=f"Ошибка запуска Client Manager: {e}",
                plugin="client_manager"
            )
            raise
    
    async def on_stop(self) -> None:
        """Остановка плагина - graceful shutdown сервера."""
        await super().on_stop()
        
        try:
            await self.runtime.service_registry.call(
                "logger.log",
                level="info",
                message="Остановка Client Manager сервера...",
                plugin="client_manager"
            )
            
            # Graceful shutdown uvicorn сервера
            if self.server:
                self.server.should_exit = True
                # Ждем завершения сервера
                if self.server_task:
                    try:
                        await asyncio.wait_for(self.server_task, timeout=30)
                    except asyncio.TimeoutError:
                        await self.runtime.service_registry.call(
                            "logger.log",
                            level="warning",
                            message="Таймаут при остановке сервера, принудительная остановка",
                            plugin="client_manager"
                        )
                        if not self.server_task.cancelled():
                            self.server_task.cancel()
                            try:
                                await self.server_task
                            except asyncio.CancelledError:
                                pass
            
            await self.runtime.service_registry.call(
                "logger.log",
                level="info",
                message="Client Manager остановлен",
                plugin="client_manager"
            )
            
        except Exception as e:
            await self.runtime.service_registry.call(
                "logger.log",
                level="error",
                message=f"Ошибка при остановке Client Manager: {e}",
                plugin="client_manager"
            )
    
    async def on_unload(self) -> None:
        """Выгрузка плагина - очистка ресурсов."""
        await super().on_unload()
        
        self.server = None
        self.server_task = None
        self.handler = None
        
        await self.runtime.service_registry.call(
            "logger.log",
            level="info",
            message="Client Manager выгружен",
            plugin="client_manager"
        )
