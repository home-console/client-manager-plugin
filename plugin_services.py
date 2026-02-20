"""
Service Registry Integration - регистрация сервисов Client Manager.

Предоставляет API для взаимодействия с Client Manager через service_registry.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .plugin import ClientManagerPlugin

logger = logging.getLogger(__name__)

async def register_services(plugin: "ClientManagerPlugin") -> None:
    """
    Регистрирует все сервисы Client Manager в service_registry.
    
    Args:
        plugin: экземпляр ClientManagerPlugin
    """
    registry = plugin.runtime.service_registry
    
    # ============================================================================
    # REST handlers - обёртки для HTTP endpoints
    # ============================================================================
    
    # GET /client-manager/clients
    async def list_clients(body: Any = None, **kwargs) -> List[Dict[str, Any]]:
        """Получить список всех подключенных клиентов."""
        if not plugin.handler:
            return []
        return plugin.handler.get_all_clients()
    
    await registry.register("client_manager.list_clients", list_clients)
    
    # GET /client-manager/clients/{client_id}
    async def get_client(client_id: str, body: Any = None, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о клиенте.
        
        Args:
            client_id: ID клиента
            
        Returns:
            Информация о клиенте или None
        """
        if not plugin.handler:
            return None
        
        clients = plugin.handler.get_all_clients()
        for client in clients:
            if client.get("client_id") == client_id or client.get("id") == client_id:
                return client
        return None
    
    await registry.register("client_manager.get_client", get_client)
    
    # DELETE /client-manager/clients/{client_id}
    async def delete_client(client_id: str, body: Any = None, **kwargs) -> Dict[str, Any]:
        """
        Удалить клиента.
        
        Args:
            client_id: ID клиента
            
        Returns:
            Результат удаления
        """
        if not plugin.handler:
            return {"error": "Handler not initialized"}
        
        try:
            await plugin.handler.websocket_manager.disconnect(client_id)
            return {"ok": True, "client_id": client_id}
        except Exception as e:
            return {"error": str(e)}
    
    # internal implementation (used by operation handlers)
    await registry.register("client_manager._impl.delete_client", delete_client)

    async def delete_client_wrapper(client_id: str, body: Any = None, **kwargs) -> Dict[str, Any]:
        try:
            op_body = {
                "type": "client_manager.delete_client",
                "params": {"client_id": client_id},
            }
            return await plugin.runtime.service_registry.call("admin.operations.create", op_body)
        except Exception as e:
            return {"error": str(e)}

    await registry.register("client_manager.delete_client", delete_client_wrapper)
    
    # POST /client-manager/commands/{client_id}
    async def execute_command(
        client_id: str,
        body: Any = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Выполнить команду на клиенте.
        
        Args:
            client_id: ID клиента
            body: {"command": "CMD"}
            
        Returns:
            Результат выполнения
        """
        if not plugin.handler:
            return {"error": "Handler not initialized"}
        
        try:
            # Проверяем что клиент подключен
            clients = plugin.handler.get_all_clients()
            if not any(c.get("client_id") == client_id or c.get("id") == client_id for c in clients):
                return {"error": f"Client '{client_id}' not found"}
            
            # Получаем команду из body
            if isinstance(body, dict):
                command = body.get("command", "")
            else:
                command = ""
            
            if not command:
                return {"error": "Command is required"}
            
            # Выполняем команду через command_handler
            if hasattr(plugin.handler, 'command_handler'):
                result = await plugin.handler.command_handler.execute_command(
                    client_id=client_id,
                    command=command,
                    timeout=body.get("timeout", 300) if isinstance(body, dict) else 300
                )
                return result
            else:
                return {"error": "Command handler not available"}
        
        except Exception as e:
            return {"error": str(e)}
    
    # internal implementation (used by operation handlers)
    await registry.register("client_manager._impl.execute_command", execute_command)

    async def execute_command_wrapper(client_id: str, body: Any = None, **kwargs) -> Dict[str, Any]:
        try:
            op_body = {
                "type": "client_manager.execute_command",
                "params": {"client_id": client_id, "body": body or {}},
            }
            return await plugin.runtime.service_registry.call("admin.operations.create", op_body)
        except Exception as e:
            return {"error": str(e)}

    await registry.register("client_manager.execute_command", execute_command_wrapper)
    
    # GET /client-manager/commands/{client_id}/status
    async def get_command_status(client_id: str = None, command_id: str = None, body: Any = None, **kwargs) -> Dict[str, Any]:
        """
        Получить статус команды.
        
        Args:
            client_id или command_id для поиска
            
        Returns:
            Статус команды
        """
        if not plugin.handler:
            return {"error": "Handler not initialized"}
        
        try:
            if hasattr(plugin.handler, 'command_handler'):
                if command_id:
                    status = await plugin.handler.command_handler.get_command_status(command_id)
                    return status
                elif client_id:
                    return {"client_id": client_id, "status": "pending"}
            
            return {"error": "Command handler not available"}
        except Exception as e:
            return {"error": str(e)}
    
    await registry.register("client_manager.get_command_status", get_command_status)
    
    # GET /client-manager/health
    async def health_check(body: Any = None, **kwargs) -> Dict[str, Any]:
        """Health check для client manager."""
        return {
            "status": "ok" if plugin.handler else "initializing",
            "handler": "ready" if plugin.handler else "not ready",
            "timestamp": __import__('datetime').datetime.utcnow().isoformat()
        }
    
    await registry.register("client_manager.health_check", health_check)
    
    # GET /client-manager/files/transfers
    async def list_transfers(body: Any = None, **kwargs) -> List[Dict[str, Any]]:
        """Получить список передач файлов."""
        if not plugin.handler:
            return []
        
        try:
            if hasattr(plugin.handler, 'file_handler'):
                return plugin.handler.file_handler.get_active_transfers()
            return []
        except Exception:
            return []
    
    await registry.register("client_manager.list_transfers", list_transfers)
    
    # POST /client-manager/universal/{client_id}/execute
    async def execute_universal_command(
        client_id: str,
        body: Any = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Выполнить универсальную команду.
        
        Args:
            client_id: ID клиента
            body: {"command_type": "system.info", "path": "..."}
            
        Returns:
            Результат выполнения
        """
        if not plugin.handler:
            return {"error": "Handler not initialized"}
        
        try:
            # Проверяем что клиент подключен
            clients = plugin.handler.get_all_clients()
            if not any(c.get("client_id") == client_id or c.get("id") == client_id for c in clients):
                return {"error": f"Client '{client_id}' not found"}
            
            # Получаем команду из body
            if not isinstance(body, dict):
                return {"error": "Body must be JSON"}
            
            command_type = body.get("command_type", "")
            if not command_type:
                return {"error": "command_type is required"}
            
            # Выполняем универсальную команду
            if hasattr(plugin.handler, 'universal_handler'):
                result = await plugin.handler.universal_handler.execute(
                    client_id=client_id,
                    command_type=command_type,
                    path=body.get("path")
                )
                return result
            else:
                return {"error": "Universal command handler not available"}
        
        except Exception as e:
            return {"error": str(e)}
    
    # internal implementation (used by operation handlers)
    await registry.register("client_manager._impl.execute_universal_command", execute_universal_command)

    async def execute_universal_command_wrapper(client_id: str, body: Any = None, **kwargs) -> Dict[str, Any]:
        try:
            op_body = {
                "type": "client_manager.execute_universal_command",
                "params": {"client_id": client_id, "body": body or {}},
            }
            return await plugin.runtime.service_registry.call("admin.operations.create", op_body)
        except Exception as e:
            return {"error": str(e)}

    await registry.register("client_manager.execute_universal_command", execute_universal_command_wrapper)
    
    # Сервис: получить статистику
    async def get_stats() -> Dict[str, Any]:
        """
        Получить статистику Client Manager.
        
        Returns:
            Статистика: количество клиентов, команд, трансферов и т.д.
        """
        if not plugin.handler:
            return {
                "total_clients": 0,
                "online_clients": 0,
                "total_commands": 0,
                "active_transfers": 0
            }
        
        clients = plugin.handler.get_all_clients()
        stats = {
            "total_clients": len(clients),
            "online_clients": len([c for c in clients if c.get("status") == "online"]),
        }
        
        # Добавляем статистику из stats_service если доступна
        if hasattr(plugin.handler, 'stats_service'):
            try:
                service_stats = plugin.handler.stats_service.get_stats()
                stats.update(service_stats)
            except Exception:
                pass
        
        return stats
    
    await registry.register("client_manager.get_stats", get_stats)
    
    # ============================================================================
    # WebSocket handlers
    # ============================================================================
    
    # Сервис: обработка WebSocket для агентов
    async def websocket_handler(websocket: Any) -> None:
        """
        WebSocket handler для агентов.
        
        Управляет подключениями удаленных агентов и маршрутизацией команд.
        """
        if not plugin.handler:
            try:
                await websocket.close(code=1011, reason="Handler not initialized")
            except Exception:
                pass
            return
        
        try:
            await plugin.handler.handle_websocket(websocket)
        except Exception as e:
            logger.error(f"WebSocket handler error: {e}")
            import traceback
            traceback.print_exc()
            try:
                await websocket.close(code=1011, reason=str(e))
            except Exception:
                pass
    
    await registry.register("client_manager.websocket", websocket_handler)
    
    # Сервис: обработка админского WebSocket
    async def admin_websocket_handler(websocket: Any) -> None:
        """
        WebSocket handler для админского интерфейса.
        
        Требует JWT токен для авторизации.
        Предоставляет доступ к управлению клиентами и получению событий.
        """
        if not plugin.handler:
            try:
                await websocket.close(code=1011, reason="Handler not initialized")
            except Exception:
                pass
            return
        
        try:
            import json
            from fastapi import WebSocketDisconnect
            
            # Получаем токен из query params или заголовков
            token = websocket.query_params.get('token') if hasattr(websocket, 'query_params') else None
            if not token and hasattr(websocket, 'headers'):
                sp = websocket.headers.get('sec-websocket-protocol')
                if sp:
                    token_candidate = sp.split(',')[0].strip()
                    if token_candidate.lower().startswith('bearer '):
                        token = token_candidate[7:]
                    else:
                        token = token_candidate
            
            # Принимаем соединение
            await websocket.accept()
            
            # Проверяем токен
            if not token:
                await websocket.send_text('{"type":"auth_required","message":"Token required"}')
                await websocket.close(code=1008, reason="Auth required")
                return
            
            # Проверяем авторизацию
            auth_svc = getattr(plugin.handler, 'auth_service', None)
            if not auth_svc:
                await websocket.send_text('{"type":"auth_unavailable","message":"Auth service unavailable"}')
                await websocket.close(code=1011, reason="Auth service unavailable")
                return
            
            payload = auth_svc.verify_token(token)
            if not payload:
                await websocket.send_text('{"type":"auth_failed","message":"Invalid token"}')
                await websocket.close(code=1008, reason="Invalid token")
                return
            
            # Успешная авторизация
            admin_id = f"admin:{payload.get('client_id', 'unknown')}"
            await plugin.handler.websocket_manager.connect(
                websocket,
                admin_id,
                metadata={"admin": True, "permissions": payload.get('permissions', [])}
            )
            
            # Отправляем начальный список клиентов
            try:
                clients = plugin.handler.get_all_clients()
                await websocket.send_text(json.dumps({"type": "client_list", "data": clients}))
            except Exception as e:
                logger.warning(f"Ошибка при отправке списка клиентов: {e}")
            
            # Периодический refresh и обработка команд от админа
            import asyncio
            
            async def periodic_refresh():
                prev_snapshot = None
                while True:
                    await asyncio.sleep(5)
                    try:
                        clients = plugin.handler.get_all_clients()
                        if json.dumps(clients) != prev_snapshot:
                            prev_snapshot = json.dumps(clients)
                            await websocket.send_text(json.dumps({"type": "client_list_refresh", "data": clients}))
                    except Exception:
                        break
            
            refresh_task = asyncio.create_task(periodic_refresh())
            
            try:
                while True:
                    text = await websocket.receive_text()
                    try:
                        msg = json.loads(text)
                        if msg.get('type') == 'get_clients':
                            await websocket.send_text(json.dumps({"type": "client_list", "data": plugin.handler.get_all_clients()}))
                        elif msg.get('type') == 'ping':
                            await websocket.send_text('{"type":"pong"}')
                        else:
                            await websocket.send_text(json.dumps({"type": "unknown_command", "received": msg.get('type')}))
                    except Exception:
                        await websocket.send_text('{"type":"error","message":"Invalid JSON"}')
            
            except WebSocketDisconnect:
                logger.info(f"Admin WS disconnected: {admin_id}")
            except Exception as e:
                logger.error(f"Admin websocket error: {e}")
            finally:
                try:
                    refresh_task.cancel()
                except Exception:
                    pass
                await plugin.handler.websocket_manager.disconnect(admin_id)
        
        except Exception as e:
            logger.error(f"Admin WebSocket handler error: {e}")
            import traceback
            traceback.print_exc()
    
    await registry.register("client_manager.admin_websocket", admin_websocket_handler)
