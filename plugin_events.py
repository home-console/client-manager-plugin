"""
Event Bus Integration - интеграция Client Manager с event_bus.

Публикует события о подключении/отключении клиентов, выполнении команд и т.д.
"""

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from .plugin import ClientManagerPlugin


async def setup_event_integration(plugin: "ClientManagerPlugin", handler: Any) -> None:
    """
    Настраивает интеграцию WebSocketHandler с event_bus.
    
    Подключает hooks для публикации событий:
    - client.connected - клиент подключился
    - client.disconnected - клиент отключился
    - command.started - команда начала выполнение
    - command.completed - команда завершена
    - command.failed - команда завершилась с ошибкой
    - file.upload_started - начата загрузка файла
    - file.upload_completed - загрузка файла завершена
    - file.download_started - начато скачивание файла
    - file.download_completed - скачивание файла завершено
    
    Args:
        plugin: экземпляр ClientManagerPlugin
        handler: WebSocketHandler instance
    """
    event_bus = plugin.runtime.event_bus
    
    # Hook для подключения клиента
    original_connect = handler.websocket_manager.connect
    
    async def connect_with_event(websocket, client_id: str, metadata: Dict[str, Any] = None):
        """Wrapper для websocket_manager.connect с публикацией события."""
        result = await original_connect(websocket, client_id, metadata)
        
        # Публикуем событие о подключении
        try:
            await event_bus.publish("client.connected", {
                "client_id": client_id,
                "metadata": metadata or {},
                "source": "client_manager"
            })
        except Exception as e:
            # Не ломаем подключение из-за ошибки публикации события
            try:
                await plugin.runtime.service_registry.call(
                    "logger.log",
                    level="warning",
                    message=f"Ошибка публикации события client.connected: {e}",
                    plugin="client_manager"
                )
            except Exception:
                pass
        
        return result
    
    handler.websocket_manager.connect = connect_with_event
    
    # Hook для отключения клиента
    original_disconnect = handler.websocket_manager.disconnect
    
    async def disconnect_with_event(client_id: str):
        """Wrapper для websocket_manager.disconnect с публикацией события."""
        # Получаем информацию о клиенте до отключения
        client_info = None
        try:
            clients = handler.get_all_clients()
            for c in clients:
                if c.get("client_id") == client_id:
                    client_info = c
                    break
        except Exception:
            pass
        
        result = await original_disconnect(client_id)
        
        # Публикуем событие о отключении
        try:
            await event_bus.publish("client.disconnected", {
                "client_id": client_id,
                "client_info": client_info,
                "source": "client_manager"
            })
        except Exception as e:
            try:
                await plugin.runtime.service_registry.call(
                    "logger.log",
                    level="warning",
                    message=f"Ошибка публикации события client.disconnected: {e}",
                    plugin="client_manager"
                )
            except Exception:
                pass
        
        return result
    
    handler.websocket_manager.disconnect = disconnect_with_event
    
    # Hook для выполнения команд
    if hasattr(handler, 'command_handler') and handler.command_handler:
        original_execute = handler.command_handler.execute_command
        
        async def execute_with_events(client_id: str, command: str, timeout: int = 300, **kwargs):
            """Wrapper для command_handler.execute_command с публикацией событий."""
            # Публикуем событие о начале выполнения
            command_id = f"{client_id}_{command[:50]}_{id(command)}"
            
            try:
                await event_bus.publish("command.started", {
                    "command_id": command_id,
                    "client_id": client_id,
                    "command": command,
                    "source": "client_manager"
                })
            except Exception:
                pass
            
            try:
                result = await original_execute(client_id, command, timeout, **kwargs)
                
                # Публикуем событие о завершении
                try:
                    await event_bus.publish("command.completed", {
                        "command_id": command_id,
                        "client_id": client_id,
                        "command": command,
                        "result": result,
                        "source": "client_manager"
                    })
                except Exception:
                    pass
                
                return result
                
            except Exception as e:
                # Публикуем событие об ошибке
                try:
                    await event_bus.publish("command.failed", {
                        "command_id": command_id,
                        "client_id": client_id,
                        "command": command,
                        "error": str(e),
                        "source": "client_manager"
                    })
                except Exception:
                    pass
                
                raise
        
        handler.command_handler.execute_command = execute_with_events
    
    # Hook для загрузки файлов
    if hasattr(handler, 'file_handler') and handler.file_handler:
        original_upload = handler.file_handler.upload_file
        
        async def upload_with_events(client_id: str, file_path: str, content: bytes, **kwargs):
            """Wrapper для file_handler.upload_file с публикацией событий."""
            file_id = f"{client_id}_{file_path}_{len(content)}"
            
            try:
                await event_bus.publish("file.upload_started", {
                    "file_id": file_id,
                    "client_id": client_id,
                    "file_path": file_path,
                    "size": len(content),
                    "source": "client_manager"
                })
            except Exception:
                pass
            
            try:
                result = await original_upload(client_id, file_path, content, **kwargs)
                
                try:
                    await event_bus.publish("file.upload_completed", {
                        "file_id": file_id,
                        "client_id": client_id,
                        "file_path": file_path,
                        "size": len(content),
                        "result": result,
                        "source": "client_manager"
                    })
                except Exception:
                    pass
                
                return result
                
            except Exception as e:
                try:
                    await event_bus.publish("file.upload_failed", {
                        "file_id": file_id,
                        "client_id": client_id,
                        "file_path": file_path,
                        "error": str(e),
                        "source": "client_manager"
                    })
                except Exception:
                    pass
                raise
        
        handler.file_handler.upload_file = upload_with_events
        
        # Hook для скачивания файлов
        original_download = handler.file_handler.download_file
        
        async def download_with_events(client_id: str, file_path: str, **kwargs):
            """Wrapper для file_handler.download_file с публикацией событий."""
            file_id = f"{client_id}_{file_path}_download"
            
            try:
                await event_bus.publish("file.download_started", {
                    "file_id": file_id,
                    "client_id": client_id,
                    "file_path": file_path,
                    "source": "client_manager"
                })
            except Exception:
                pass
            
            try:
                content = await original_download(client_id, file_path, **kwargs)
                
                try:
                    await event_bus.publish("file.download_completed", {
                        "file_id": file_id,
                        "client_id": client_id,
                        "file_path": file_path,
                        "size": len(content) if content else 0,
                        "source": "client_manager"
                    })
                except Exception:
                    pass
                
                return content
                
            except Exception as e:
                try:
                    await event_bus.publish("file.download_failed", {
                        "file_id": file_id,
                        "client_id": client_id,
                        "file_path": file_path,
                        "error": str(e),
                        "source": "client_manager"
                    })
                except Exception:
                    pass
                raise
        
        handler.file_handler.download_file = download_with_events


async def publish_heartbeat_event(plugin: "ClientManagerPlugin", client_id: str) -> None:
    """
    Публикует событие heartbeat для клиента.
    
    Args:
        plugin: экземпляр ClientManagerPlugin
        client_id: ID клиента
    """
    try:
        await plugin.runtime.event_bus.publish("client.heartbeat", {
            "client_id": client_id,
            "source": "client_manager"
        })
    except Exception:
        pass  # Игнорируем ошибки heartbeat
