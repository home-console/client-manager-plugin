"""
Менеджер клиентов для управления подключениями
"""

import logging
import time
from datetime import datetime
from typing import Dict, Optional
from fastapi import WebSocket

from .models import ClientInfo, EncryptionState

logger = logging.getLogger(__name__)


class ClientManager:
    """Менеджер для управления клиентами"""
    
    def __init__(self):
        self.clients: Dict[str, WebSocket] = {}
        self.client_info: Dict[str, ClientInfo] = {}
        self.encryption_state: Dict[str, EncryptionState] = {}
    
    async def register_client(self, websocket: WebSocket, client_data: dict) -> str:
        """Регистрация нового клиента"""
        client_id = client_data.get('client_id', f"client_{int(time.time())}")
        hostname = client_data.get('hostname', 'unknown')
        ip = websocket.client.host if websocket.client else 'unknown'
        port = websocket.client.port if websocket.client else 0
        
        # Создаем информацию о клиенте
        client_info = ClientInfo(
            id=client_id,
            hostname=hostname,
            ip=ip,
            port=port,
            connected_at=datetime.now().isoformat(),
            last_heartbeat=datetime.now().isoformat(),
            status='connected',
            capabilities=client_data.get('capabilities', [])
        )
        
        # Сохраняем клиента
        self.clients[client_id] = websocket
        self.client_info[client_id] = client_info
        
        logger.info(f"✅ Клиент зарегистрирован: {client_id} ({hostname})")
        logger.info(f"📊 Всего клиентов: {len(self.clients)}")
        
        # Инициализация E2E состояния
        self.encryption_state[client_id] = EncryptionState()
        
        return client_id
    
    async def unregister_client(self, client_id: str):
        """Отключение клиента"""
        if client_id in self.clients:
            del self.clients[client_id]
        if client_id in self.client_info:
            self.client_info[client_id].status = 'disconnected'
        if client_id in self.encryption_state:
            del self.encryption_state[client_id]
        
        logger.info(f"❌ Клиент отключен: {client_id}")
    
    def get_client(self, client_id: str) -> Optional[WebSocket]:
        """Получить WebSocket клиента"""
        return self.clients.get(client_id)
    
    def get_client_info(self, client_id: str) -> Optional[ClientInfo]:
        """Получить информацию о клиенте"""
        return self.client_info.get(client_id)
    
    def get_all_clients(self) -> Dict[str, ClientInfo]:
        """Получить всех клиентов"""
        return self.client_info.copy()
    
    def update_heartbeat(self, client_id: str):
        """Обновить время последнего heartbeat"""
        if client_id in self.client_info:
            self.client_info[client_id].last_heartbeat = datetime.now().isoformat()
    
    def get_encryption_state(self, client_id: str) -> Optional[EncryptionState]:
        """Получить состояние шифрования для клиента"""
        return self.encryption_state.get(client_id)
    
    def update_encryption_state(self, client_id: str, state: EncryptionState):
        """Обновить состояние шифрования для клиента"""
        self.encryption_state[client_id] = state
