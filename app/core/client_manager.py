"""
Менеджер клиентов для управления подключениями
"""

import logging
import time
import platform
from datetime import datetime
from typing import Dict, Optional, Any
from fastapi import WebSocket

from .models import ClientInfo, EncryptionState, DeviceType, DeviceCapabilities
from .commands import command_adapter_factory

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

        # Определяем тип устройства
        device_type = self._determine_device_type(client_data)

        # Определяем возможности устройства
        capabilities = self._determine_capabilities(device_type, client_data)

        # Создаем информацию о клиенте
        client_info = ClientInfo(
            id=client_id,
            hostname=hostname,
            ip=ip,
            port=port,
            connected_at=datetime.now().isoformat(),
            last_heartbeat=datetime.now().isoformat(),
            status='connected',
            device_type=device_type,
            os_info=client_data.get('os_info', {}),
            capabilities=capabilities,
            tags=client_data.get('tags', []),
            location=client_data.get('location'),
            metadata=client_data.get('metadata', {})
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

    def _determine_device_type(self, client_data: dict) -> DeviceType:
        """Определить тип устройства на основе данных клиента"""
        # Проверяем явное указание типа
        device_type_str = client_data.get('device_type')
        if device_type_str:
            try:
                return DeviceType(device_type_str.lower())
            except ValueError:
                pass

        # Определяем по OS информации
        os_info = client_data.get('os_info', {})
        os_name = os_info.get('name', '').lower()
        platform_info = os_info.get('platform', '').lower()

        # Определение по имени ОС
        if 'linux' in os_name:
            # Проверяем, является ли это Proxmox
            if 'proxmox' in os_name.lower() or client_data.get('is_proxmox'):
                return DeviceType.PROXMOX
            # Проверяем, является ли это storage сервером
            elif client_data.get('is_storage_server') or 'storage' in client_data.get('tags', []):
                return DeviceType.STORAGE_SERVER
            else:
                return DeviceType.LINUX

        elif 'windows' in os_name or 'win' in platform_info:
            return DeviceType.WINDOWS

        elif 'darwin' in platform_info or 'macos' in os_name or 'mac' in platform_info:
            return DeviceType.MACOS

        # Проверяем по специальным маркерам
        if client_data.get('is_proxmox') or 'proxmox' in client_data.get('tags', []):
            return DeviceType.PROXMOX

        if client_data.get('is_iot') or 'iot' in client_data.get('tags', []):
            return DeviceType.IOT_DEVICE

        # По умолчанию - generic
        return DeviceType.GENERIC

    def _determine_capabilities(self, device_type: DeviceType, client_data: dict) -> list[str]:
        """Определить возможности устройства"""
        # Базовые возможности из конфигурации
        capabilities = list(DeviceCapabilities.CAPABILITIES.get(device_type, DeviceCapabilities.BASIC))

        # Дополнительные возможности из данных клиента
        extra_caps = client_data.get('capabilities', [])
        capabilities.extend(extra_caps)

        # Убираем дубликаты
        return list(set(capabilities))

    def get_clients_by_type(self, device_type: DeviceType) -> Dict[str, ClientInfo]:
        """Получить клиентов определенного типа"""
        return {
            client_id: info
            for client_id, info in self.client_info.items()
            if info.device_type == device_type
        }

    def get_clients_by_tag(self, tag: str) -> Dict[str, ClientInfo]:
        """Получить клиентов с определенным тегом"""
        return {
            client_id: info
            for client_id, info in self.client_info.items()
            if tag in info.tags
        }

    def get_clients_by_capability(self, capability: str) -> Dict[str, ClientInfo]:
        """Получить клиентов с определенной возможностью"""
        return {
            client_id: info
            for client_id, info in self.client_info.items()
            if capability in info.capabilities
        }
