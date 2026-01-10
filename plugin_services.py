"""
Service Registry Integration - регистрация сервисов Client Manager.

Предоставляет API для взаимодействия с Client Manager через service_registry.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .plugin import ClientManagerPlugin


async def register_services(plugin: "ClientManagerPlugin") -> None:
    """
    Регистрирует все сервисы Client Manager в service_registry.
    
    Args:
        plugin: экземпляр ClientManagerPlugin
    """
    registry = plugin.runtime.service_registry
    
    # Сервис: получить список всех клиентов
    async def list_clients() -> List[Dict[str, Any]]:
        """Получить список всех подключенных клиентов."""
        if not plugin.handler:
            return []
        return plugin.handler.get_all_clients()
    
    await registry.register("client_manager.list_clients", list_clients)
    
    # Сервис: получить информацию о конкретном клиенте
    async def get_client(client_id: str) -> Optional[Dict[str, Any]]:
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
            if client.get("client_id") == client_id:
                return client
        return None
    
    await registry.register("client_manager.get_client", get_client)
    
    # Сервис: отправить команду клиенту
    async def send_command(
        client_id: str,
        command: str,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Отправить команду на удаленный агент.
        
        Args:
            client_id: ID клиента
            command: команда для выполнения
            timeout: таймаут выполнения (секунды)
            
        Returns:
            Результат выполнения команды
            
        Raises:
            ValueError: если клиент не найден
            RuntimeError: если ошибка выполнения
        """
        if not plugin.handler:
            raise RuntimeError("Client Manager handler не инициализирован")
        
        # Проверяем, что клиент подключен
        client = await get_client(client_id)
        if not client:
            raise ValueError(f"Клиент '{client_id}' не найден")
        
        # Отправляем команду через command_handler
        try:
            result = await plugin.handler.command_handler.execute_command(
                client_id=client_id,
                command=command,
                timeout=timeout or 300
            )
            return result
        except Exception as e:
            raise RuntimeError(f"Ошибка выполнения команды: {e}")
    
    await registry.register("client_manager.send_command", send_command)
    
    # Сервис: получить статус клиента
    async def get_client_status(client_id: str) -> Dict[str, Any]:
        """
        Получить статус клиента (online/offline, last_seen, etc).
        
        Args:
            client_id: ID клиента
            
        Returns:
            Статус клиента
        """
        if not plugin.handler:
            return {"status": "unavailable", "reason": "handler not initialized"}
        
        client = await get_client(client_id)
        if not client:
            return {"status": "offline", "client_id": client_id}
        
        return {
            "status": "online",
            "client_id": client_id,
            "connected_at": client.get("connected_at"),
            "last_heartbeat": client.get("last_heartbeat"),
            "metadata": client.get("metadata", {})
        }
    
    await registry.register("client_manager.get_client_status", get_client_status)
    
    # Сервис: загрузить файл на клиент
    async def upload_file(
        client_id: str,
        file_path: str,
        content: bytes,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Загрузить файл на удаленный агент.
        
        Args:
            client_id: ID клиента
            file_path: путь к файлу на агенте
            content: содержимое файла
            overwrite: перезаписать существующий файл
            
        Returns:
            Результат загрузки
            
        Raises:
            ValueError: если клиент не найден
            RuntimeError: если ошибка загрузки
        """
        if not plugin.handler:
            raise RuntimeError("Client Manager handler не инициализирован")
        
        client = await get_client(client_id)
        if not client:
            raise ValueError(f"Клиент '{client_id}' не найден")
        
        try:
            # Используем file_handler для загрузки
            result = await plugin.handler.file_handler.upload_file(
                client_id=client_id,
                file_path=file_path,
                content=content,
                overwrite=overwrite
            )
            return result
        except Exception as e:
            raise RuntimeError(f"Ошибка загрузки файла: {e}")
    
    await registry.register("client_manager.upload_file", upload_file)
    
    # Сервис: скачать файл с клиента
    async def download_file(client_id: str, file_path: str) -> bytes:
        """
        Скачать файл с удаленного агента.
        
        Args:
            client_id: ID клиента
            file_path: путь к файлу на агенте
            
        Returns:
            Содержимое файла
            
        Raises:
            ValueError: если клиент не найден
            RuntimeError: если ошибка скачивания
        """
        if not plugin.handler:
            raise RuntimeError("Client Manager handler не инициализирован")
        
        client = await get_client(client_id)
        if not client:
            raise ValueError(f"Клиент '{client_id}' не найден")
        
        try:
            content = await plugin.handler.file_handler.download_file(
                client_id=client_id,
                file_path=file_path
            )
            return content
        except Exception as e:
            raise RuntimeError(f"Ошибка скачивания файла: {e}")
    
    await registry.register("client_manager.download_file", download_file)
    
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
