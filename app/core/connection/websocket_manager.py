"""
Менеджер WebSocket соединений
"""

import asyncio
import logging
from typing import Dict, Optional, Set
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Менеджер WebSocket соединений"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_metadata: Dict[str, dict] = {}
        self.connection_tasks: Dict[str, asyncio.Task] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str, metadata: dict = None) -> bool:
        """Подключение клиента"""
        try:
            await websocket.accept()
            self.active_connections[client_id] = websocket
            self.connection_metadata[client_id] = metadata or {}
            
            # Запускаем задачу мониторинга соединения только для зарегистрированных клиентов
            if client_id != "unknown":
                self.connection_tasks[client_id] = asyncio.create_task(
                    self._monitor_connection(client_id, websocket)
                )
            
            logger.info(f"✅ WebSocket подключен: {client_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения WebSocket {client_id}: {e}")
            return False
    
    async def disconnect(self, client_id: str):
        """Отключение клиента"""
        if client_id in self.active_connections:
            try:
                websocket = self.active_connections[client_id]
                await websocket.close()
            except Exception as e:
                logger.warning(f"Ошибка закрытия WebSocket {client_id}: {e}")
            finally:
                self.active_connections.pop(client_id, None)
                self.connection_metadata.pop(client_id, None)
                
                # Отменяем задачу мониторинга
                if client_id in self.connection_tasks:
                    self.connection_tasks[client_id].cancel()
                    del self.connection_tasks[client_id]
                
                logger.info(f"❌ WebSocket отключен: {client_id}")
    
    async def send_message(self, client_id: str, message: str) -> bool:
        """Отправка сообщения клиенту"""
        if client_id not in self.active_connections:
            logger.error(f"❌ Клиент {client_id} не найден")
            return False
        
        try:
            websocket = self.active_connections[client_id]
            await websocket.send_text(message)
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сообщения {client_id}: {e}")
            await self.disconnect(client_id)
            return False
    
    def get_connection(self, client_id: str) -> Optional[WebSocket]:
        """Получить соединение клиента"""
        return self.active_connections.get(client_id)
    
    def get_metadata(self, client_id: str) -> dict:
        """Получить метаданные соединения"""
        return self.connection_metadata.get(client_id, {})
    
    def update_metadata(self, client_id: str, metadata: dict):
        """Обновить метаданные соединения"""
        if client_id in self.connection_metadata:
            self.connection_metadata[client_id].update(metadata)
    
    def get_all_connections(self) -> Dict[str, WebSocket]:
        """Получить все активные соединения"""
        return self.active_connections.copy()
    
    def get_connection_count(self) -> int:
        """Получить количество активных соединений"""
        return len(self.active_connections)
    
    async def start_monitoring(self, client_id: str):
        """Запустить мониторинг для зарегистрированного клиента"""
        if client_id in self.active_connections and client_id not in self.connection_tasks:
            websocket = self.active_connections[client_id]
            self.connection_tasks[client_id] = asyncio.create_task(
                self._monitor_connection(client_id, websocket)
            )
            logger.info(f"🔍 Мониторинг запущен для клиента {client_id}")
    
    async def update_client_id(self, old_client_id: str, new_client_id: str):
        """Обновить client_id для существующего соединения"""
        if old_client_id in self.active_connections:
            websocket = self.active_connections.pop(old_client_id)
            metadata = self.connection_metadata.pop(old_client_id, {})
            
            self.active_connections[new_client_id] = websocket
            self.connection_metadata[new_client_id] = metadata
            
            # Отменяем старую задачу мониторинга если есть
            if old_client_id in self.connection_tasks:
                self.connection_tasks[old_client_id].cancel()
                del self.connection_tasks[old_client_id]
            
            logger.info(f"🔄 Client ID обновлен: {old_client_id} -> {new_client_id}")
            return True
        return False
    
    async def _monitor_connection(self, client_id: str, websocket: WebSocket):
        """Мониторинг соединения"""
        try:
            while True:
                # Проверяем, что соединение еще активно
                if client_id not in self.active_connections:
                    break
                
                # Отправляем ping сообщение для проверки соединения
                try:
                    await websocket.send_text('{"type": "ping"}')
                except Exception as ping_error:
                    logger.debug(f"Ping failed for {client_id}: {ping_error}")
                    break
                
                await asyncio.sleep(30)  # Проверяем каждые 30 секунд
                
        except WebSocketDisconnect:
            logger.info(f"🔌 WebSocket отключен: {client_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга соединения {client_id}: {e}")
        finally:
            await self.disconnect(client_id)
    
    async def broadcast(self, message: str, exclude_clients: Set[str] = None):
        """Широковещательная отправка сообщения"""
        exclude_clients = exclude_clients or set()
        
        for client_id, websocket in self.active_connections.items():
            if client_id not in exclude_clients:
                await self.send_message(client_id, message)
    
    async def cleanup(self):
        """Очистка всех соединений"""
        for client_id in list(self.active_connections.keys()):
            await self.disconnect(client_id)

