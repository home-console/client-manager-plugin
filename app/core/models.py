"""
Модели данных для сервера
"""

from datetime import datetime
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel
from enum import Enum


class DeviceType(Enum):
    """Типы устройств в умном доме"""
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    PROXMOX = "proxmox"
    STORAGE_SERVER = "storage_server"
    IOT_DEVICE = "iot_device"
    GENERIC = "generic"


class DeviceCapabilities:
    """Возможности устройств по типам"""

    # Базовые возможности (все устройства)
    BASIC: Set[str] = {
        "system_info", "network_info", "file_operations",
        "process_management", "monitoring", "command_execution"
    }

    # Специфические возможности по типам
    CAPABILITIES: Dict[DeviceType, Set[str]] = {
        DeviceType.LINUX: BASIC | {
            "package_management", "systemd", "docker", "cron",
            "filesystem_tools", "network_tools", "bash_scripting"
        },

        DeviceType.WINDOWS: BASIC | {
            "powershell", "wmic", "net_commands", "task_scheduler",
            "windows_services", "registry_access", "batch_scripting"
        },

        DeviceType.MACOS: BASIC | {
            "homebrew", "launchd", "disk_utility", "system_profiler",
            "networksetup", "applescript", "zsh_scripting"
        },

        DeviceType.PROXMOX: {
            "vm_management", "container_management", "storage_pools",
            "network_management", "backup_restore", "resource_monitoring",
            "cluster_management", "api_access"
        },

        DeviceType.STORAGE_SERVER: {
            "disk_management", "raid_configuration", "filesystem_operations",
            "data_deduplication", "backup_solutions", "storage_monitoring",
            "nfs_shares", "iscsi_targets"
        },

        DeviceType.IOT_DEVICE: BASIC | {
            "sensor_reading", "actuator_control", "mqtt_communication",
            "low_power_mode", "firmware_update", "device_calibration"
        },

        DeviceType.GENERIC: BASIC
    }


class ClientInfo(BaseModel):
    """Информация о клиенте"""
    id: str
    hostname: str
    ip: str
    port: int
    connected_at: str
    last_heartbeat: str
    status: str
    device_type: DeviceType = DeviceType.GENERIC
    os_info: Optional[Dict[str, Any]] = None  # Информация об ОС
    capabilities: List[str] = []  # Динамически определяемые возможности
    tags: List[str] = []  # Теги для группировки устройств
    location: Optional[str] = None  # Физическое расположение
    metadata: Dict[str, Any] = {}  # Дополнительные метаданные


class CommandResult(BaseModel):
    """Результат выполнения команды"""
    command_id: str
    client_id: str
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    timestamp: str


class ChunkBuffer(BaseModel):
    """Буфер для сборки чанков"""
    chunks: Dict[int, str] = {}
    total: int = 0
    received: int = 0


class EncryptionState(BaseModel):
    """Состояние шифрования для клиента"""
    seq_out: int = 0
    seq_in: int = 0


class FileInfo(BaseModel):
    """Элемент файлового списка, возвращаемый агентом."""
    permissions: str
    type: str = "file"
    path: str
