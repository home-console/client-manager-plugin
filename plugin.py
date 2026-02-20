"""
Client Manager Plugin - управление удаленными клиентами.

Интеграция как in-process плагина Core Runtime с использованием:
- HttpRegistry для регистрации HTTP и WebSocket endpoints
- service_registry для бизнес-логики
- Без собственного uvicorn сервера
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional, Dict, Any

from core.base_plugin import BasePlugin, PluginMetadata
from core.http_registry import HttpEndpoint

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
    
    Архитектура:
    - Использует HttpRegistry для регистрации HTTP/WebSocket endpoints
    - Реализует обработчики как сервисы через service_registry
    - Не запускает собственный сервер (интегрируется с ApiModule)
    """
    
    def __init__(self, runtime: Optional["CoreRuntime"] = None):
        super().__init__(runtime)
        self.handler: Optional[object] = None  # WebSocketHandler instance
        self._handler_ready = asyncio.Event()  # Флаг готовности handler'а
        
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="client_manager",
            version="1.0.0",
            description="Управление удаленными клиентами через WebSocket и REST API",
            author="Home Console",
            dependencies=[],
            capabilities_provided=[
                "client.command.execute",
                "client.list",
                "client.delete"
            ]
        )
    
    async def on_load(self) -> None:
        """Загрузка плагина - регистрация сервисов и endpoints."""
        await super().on_load()
        
        try:
            # Импортируем здесь, чтобы избежать циклических зависимостей
            import plugin_services
            
            # Регистрируем сервисы в service_registry
            await plugin_services.register_services(self)
            
            # Регистрируем endpoints в HttpRegistry
            self._register_http_endpoints()

            # Register operation handlers (capability-first model)
            try:
                # Handlers expect signature: async def handler(params, context)
                # Register under capability names (new model)
                self.runtime.operations.register_handler(
                    "client.command.execute",
                    self._op_execute_command,
                )
                self.runtime.operations.register_handler(
                    "client.list",
                    self._op_list_clients,
                )
                self.runtime.operations.register_handler(
                    "client.delete",
                    self._op_delete_client,
                )
                
                # Also register under old plugin-namespaced names for backward compatibility
                self.runtime.operations.register_handler(
                    "client_manager.execute_command",
                    self._op_execute_command,
                )
                self.runtime.operations.register_handler(
                    "client_manager.delete_client",
                    self._op_delete_client,
                )
                self.runtime.operations.register_handler(
                    "client_manager.execute_universal_command",
                    self._op_execute_universal_command,
                )
            except Exception:
                # Best-effort: do not fail plugin load if operations subsystem missing
                pass
            
            # Логируем успешную загрузку через runtime
            try:
                await self.runtime.service_registry.call(
                    "logger.log",
                    level="info",
                    message="Client Manager endpoints и сервисы зарегистрированы через HttpRegistry",
                    plugin="client_manager"
                )
            except Exception:
                # Если logger не зарегистрирован, просто логируем в обычном виде
                logger.info("Client Manager endpoints и сервисы зарегистрированы")
            
        except Exception as e:
            logger.error(f"Ошибка регистрации Client Manager: {e}")
            # Пробуем логировать через runtime
            try:
                await self.runtime.service_registry.call(
                    "logger.log",
                    level="error",
                    message=f"Ошибка регистрации Client Manager: {e}",
                    plugin="client_manager"
                )
            except Exception:
                pass
            raise
    
    def _register_http_endpoints(self) -> None:
        """Регистрирует HTTP и WebSocket endpoints в HttpRegistry."""
        
        # WebSocket endpoints
        ws_endpoints = [
            HttpEndpoint(
                path="/client-manager/ws",
                service="client_manager.websocket",
                websocket=True,
                description="WebSocket для агентских соединений",
                tags=["client_manager", "websocket"]
            ),
            HttpEndpoint(
                path="/client-manager/admin/ws",
                service="client_manager.admin_websocket",
                websocket=True,
                description="Админский WebSocket (JWT защита)",
                tags=["client_manager", "websocket", "admin"]
            ),
        ]
        
        # REST endpoints для управления клиентами
        rest_endpoints = [
            # Клиенты
            HttpEndpoint(
                path="/client-manager/clients",
                method="GET",
                service="client_manager.list_clients",
                description="Получить список клиентов",
                tags=["client_manager", "clients"]
            ),
            HttpEndpoint(
                path="/client-manager/clients/{client_id}",
                method="GET",
                service="client_manager.get_client",
                description="Получить информацию о клиенте",
                tags=["client_manager", "clients"]
            ),
            HttpEndpoint(
                path="/client-manager/clients/{client_id}",
                method="DELETE",
                service="client_manager.delete_client",
                description="Удалить клиента",
                tags=["client_manager", "clients"]
            ),
            # Команды
            HttpEndpoint(
                path="/client-manager/commands/{client_id}",
                method="POST",
                service="client_manager.execute_command",
                description="Выполнить команду на клиенте",
                tags=["client_manager", "commands"]
            ),
            HttpEndpoint(
                path="/client-manager/commands/{client_id}/status",
                method="GET",
                service="client_manager.get_command_status",
                description="Получить статус команды",
                tags=["client_manager", "commands"]
            ),
            # Health
            HttpEndpoint(
                path="/client-manager/health",
                method="GET",
                service="client_manager.health_check",
                description="Health check",
                tags=["client_manager", "health"]
            ),
            # Files
            HttpEndpoint(
                path="/client-manager/files/transfers",
                method="GET",
                service="client_manager.list_transfers",
                description="Список передач файлов",
                tags=["client_manager", "files"]
            ),
            # Universal commands
            HttpEndpoint(
                path="/client-manager/universal/{client_id}/execute",
                method="POST",
                service="client_manager.execute_universal_command",
                description="Выполнить универсальную команду",
                tags=["client_manager", "commands"]
            ),
        ]
        
        # Регистрируем все endpoints
        for ep in ws_endpoints + rest_endpoints:
            self.runtime.http.register(ep)
    
    async def on_start(self) -> None:
        """Запуск плагина - инициализация WebSocketHandler."""
        await super().on_start()
        
        try:
            # Импортируем здесь для ленивой загрузки
            from app.core.websocket_handler import WebSocketHandler
            from app.core.security.auth_service import AuthService
            from app.config import settings
            
            # Инициализируем WebSocket handler
            self.handler = WebSocketHandler()
            
            # Инициализируем AuthService для админских соединений
            try:
                auth_service = AuthService()
                self.handler.auth_service = auth_service
                logger.info("AuthService инициализирован для админских WS")
            except Exception as e:
                logger.warning(f"AuthService не инициализирован: {e}")
            
            # Запускаем фоновые задачи обработчика (монитор core, флешер очереди аудита)
            try:
                await self.handler.start_background_tasks()
                logger.info("Background tasks started: core monitor and audit flusher")
            except Exception as e:
                logger.warning(f"Не удалось запустить фоновые задачи обработчика: {e}")
            
            # Помечаем что handler готов
            self._handler_ready.set()
            
            logger.info("Client Manager WebSocket handler инициализирован")
            
            # Пробуем логировать через runtime если доступно
            try:
                await self.runtime.service_registry.call(
                    "logger.log",
                    level="info",
                    message="Client Manager WebSocket handler инициализирован",
                    plugin="client_manager"
                )
            except Exception:
                pass
            
        except Exception as e:
            logger.error(f"Ошибка запуска Client Manager: {e}")
            # Пробуем логировать через runtime
            try:
                await self.runtime.service_registry.call(
                    "logger.log",
                    level="error",
                    message=f"Ошибка запуска Client Manager: {e}",
                    plugin="client_manager"
                )
            except Exception:
                pass
            raise
    
    async def on_stop(self) -> None:
        """Остановка плагина - graceful shutdown handler'а."""
        await super().on_stop()
        
        try:
            logger.info("Остановка Client Manager...")
            
            # Graceful cleanup WebSocket handler
            if self.handler:
                try:
                    await self.handler.cleanup()
                except Exception as e:
                    logger.warning(f"Ошибка при очистке handler'а: {e}")
            
            logger.info("Client Manager остановлен")
            
            # Пробуем логировать через runtime
            try:
                await self.runtime.service_registry.call(
                    "logger.log",
                    level="info",
                    message="Client Manager остановлен",
                    plugin="client_manager"
                )
            except Exception:
                pass
            
        except Exception as e:
            logger.error(f"Ошибка при остановке Client Manager: {e}")
            try:
                await self.runtime.service_registry.call(
                    "logger.log",
                    level="error",
                    message=f"Ошибка при остановке Client Manager: {e}",
                    plugin="client_manager"
                )
            except Exception:
                pass
    
    async def on_unload(self) -> None:
        """Выгрузка плагина - очистка ресурсов."""
        await super().on_unload()
        
        self.handler = None
        self._handler_ready.clear()
        
        logger.info("Client Manager выгружен")
        
        try:
            await self.runtime.service_registry.call(
                "logger.log",
                level="info",
                message="Client Manager выгружен",
                plugin="client_manager"
            )
        except Exception:
            pass

    # -----------------
    # Operation handlers
    # -----------------
    async def _op_execute_command(self, params: dict, context: dict) -> dict:
        """Operation handler: client.command.execute (capability-based) or client_manager.execute_command (legacy)"""
        client_id = params.get("client_id")
        body = params.get("body", {})
        if not client_id:
            raise ValueError("client_id is required")
        # Delegate to internal service implementation
        return await self.runtime.service_registry.call("client_manager._impl.execute_command", client_id, body)

    async def _op_list_clients(self, params: dict, context: dict) -> dict:
        """Operation handler: client.list (capability-based)"""
        # Delegate to internal service implementation
        return await self.runtime.service_registry.call("client_manager._impl.list_clients")

    async def _op_delete_client(self, params: dict, context: dict) -> dict:
        """Operation handler: client.delete (capability-based) or client_manager.delete_client (legacy)"""
        client_id = params.get("client_id")
        if not client_id:
            raise ValueError("client_id is required")
        return await self.runtime.service_registry.call("client_manager._impl.delete_client", client_id)

    async def _op_execute_universal_command(self, params: dict, context: dict) -> dict:
        """Operation handler: client_manager.execute_universal_command (legacy)"""
        client_id = params.get("client_id")
        body = params.get("body", {})
        if not client_id:
            raise ValueError("client_id is required")
        return await self.runtime.service_registry.call("client_manager._impl.execute_universal_command", client_id, body)

