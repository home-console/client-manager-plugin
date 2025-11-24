"""
Универсальная система команд для разных типов устройств
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from dataclasses import dataclass
from .models import DeviceType


class CommandType(Enum):
    """Типы универсальных команд"""
    # Системные команды
    SYSTEM_INFO = "system.info"
    NETWORK_INFO = "network.info"
    PROCESS_LIST = "process.list"
    SERVICE_STATUS = "service.status"

    # Файловые операции
    FILE_LIST = "file.list"
    FILE_COPY = "file.copy"
    FILE_MOVE = "file.move"
    FILE_DELETE = "file.delete"
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    DIRECTORY_CREATE = "directory.create"

    # Дисковые операции
    DISK_LIST = "disk.list"
    DISK_USAGE = "disk.usage"
    DISK_MOUNT = "disk.mount"
    DISK_UNMOUNT = "disk.unmount"

    # Сетевые операции
    PING = "network.ping"
    PORT_CHECK = "network.port_check"
    DNS_RESOLVE = "network.dns_resolve"

    # Специфические для Proxmox
    VM_LIST = "proxmox.vm.list"
    VM_START = "proxmox.vm.start"
    VM_STOP = "proxmox.vm.stop"
    VM_CREATE = "proxmox.vm.create"
    CONTAINER_LIST = "proxmox.container.list"

    # Специфические для хранилищ
    STORAGE_POOLS = "storage.pools"
    STORAGE_VOLUMES = "storage.volumes"
    BACKUP_CREATE = "storage.backup.create"
    BACKUP_RESTORE = "storage.backup.restore"


@dataclass
class UniversalCommand:
    """Универсальная команда"""
    command_type: CommandType
    params: Dict[str, Any] = None
    timeout: int = 30
    async_execution: bool = False

    def __post_init__(self):
        if self.params is None:
            self.params = {}


class CommandAdapter(ABC):
    """Абстрактный адаптер команд для разных ОС"""

    @property
    @abstractmethod
    def supported_device_types(self) -> List[DeviceType]:
        """Поддерживаемые типы устройств"""
        pass

    @abstractmethod
    def adapt_command(self, command: UniversalCommand) -> str:
        """Преобразовать универсальную команду в нативную"""
        pass

    @abstractmethod
    def parse_result(self, command: UniversalCommand, result: Any) -> Dict[str, Any]:
        """Распарсить результат выполнения команды"""
        pass


class LinuxCommandAdapter(CommandAdapter):
    """Адаптер команд для Linux"""

    @property
    def supported_device_types(self) -> List[DeviceType]:
        return [DeviceType.LINUX, DeviceType.STORAGE_SERVER]

    def adapt_command(self, command: UniversalCommand) -> str:
        """Преобразовать команду для Linux"""
        cmd_map = {
            CommandType.SYSTEM_INFO: "uname -a && cat /etc/os-release",
            CommandType.NETWORK_INFO: "ip addr show && ip route show",
            CommandType.PROCESS_LIST: "ps aux",
            CommandType.FILE_LIST: f"ls -la {command.params.get('path', '.')}",
            CommandType.FILE_COPY: f"cp -r {command.params['source']} {command.params['dest']}",
            CommandType.FILE_MOVE: f"mv {command.params['source']} {command.params['dest']}",
            CommandType.FILE_DELETE: f"rm -rf {command.params['path']}",
            CommandType.DIRECTORY_CREATE: f"mkdir -p {command.params['path']}",
            CommandType.DISK_LIST: "lsblk -a",
            CommandType.DISK_USAGE: "df -h",
            CommandType.PING: f"ping -c 4 {command.params['host']}",
        }

        return cmd_map.get(command.command_type, f"echo 'Unsupported command: {command.command_type}'")

    def parse_result(self, command: UniversalCommand, result: Any) -> Dict[str, Any]:
        """Парсинг результатов для Linux команд"""
        # Здесь будет логика парсинга специфичных для Linux результатов
        return {"raw_result": result, "parsed": True}


class WindowsCommandAdapter(CommandAdapter):
    """Адаптер команд для Windows"""

    @property
    def supported_device_types(self) -> List[DeviceType]:
        return [DeviceType.WINDOWS]

    def adapt_command(self, command: UniversalCommand) -> str:
        """Преобразовать команду для Windows"""
        cmd_map = {
            CommandType.SYSTEM_INFO: "systeminfo",
            CommandType.NETWORK_INFO: "ipconfig /all",
            CommandType.PROCESS_LIST: "tasklist",
            CommandType.FILE_LIST: f"dir {command.params.get('path', '.')}",
            CommandType.FILE_COPY: f"copy {command.params['source']} {command.params['dest']}",
            CommandType.FILE_MOVE: f"move {command.params['source']} {command.params['dest']}",
            CommandType.FILE_DELETE: f"del /Q /F {command.params['path']}",
            CommandType.DIRECTORY_CREATE: f"mkdir {command.params['path']}",
            CommandType.DISK_LIST: "wmic diskdrive list brief",
            CommandType.DISK_USAGE: "wmic logicaldisk get size,freespace,caption",
            CommandType.PING: f"ping {command.params['host']}",
        }

        return cmd_map.get(command.command_type, f"echo Unsupported command: {command.command_type}")

    def parse_result(self, command: UniversalCommand, result: Any) -> Dict[str, Any]:
        """Парсинг результатов для Windows команд"""
        return {"raw_result": result, "parsed": True}


class macOSCommandAdapter(CommandAdapter):
    """Адаптер команд для macOS"""

    @property
    def supported_device_types(self) -> List[DeviceType]:
        return [DeviceType.MACOS]

    def adapt_command(self, command: UniversalCommand) -> str:
        """Преобразовать команду для macOS"""
        cmd_map = {
            CommandType.SYSTEM_INFO: "sw_vers && uname -a",
            CommandType.NETWORK_INFO: "ifconfig -a && netstat -rn",
            CommandType.PROCESS_LIST: "ps aux",
            CommandType.FILE_LIST: f"ls -la {command.params.get('path', '.')}",
            CommandType.FILE_COPY: f"cp -r {command.params['source']} {command.params['dest']}",
            CommandType.FILE_MOVE: f"mv {command.params['source']} {command.params['dest']}",
            CommandType.FILE_DELETE: f"rm -rf {command.params['path']}",
            CommandType.DIRECTORY_CREATE: f"mkdir -p {command.params['path']}",
            CommandType.DISK_LIST: "diskutil list",
            CommandType.DISK_USAGE: "df -h",
            CommandType.PING: f"ping -c 4 {command.params['host']}",
        }

        return cmd_map.get(command.command_type, f"echo 'Unsupported command: {command.command_type}'")

    def parse_result(self, command: UniversalCommand, result: Any) -> Dict[str, Any]:
        """Парсинг результатов для macOS команд"""
        return {"raw_result": result, "parsed": True}


class CommandAdapterFactory:
    """Фабрика адаптеров команд"""

    def __init__(self):
        self.adapters: Dict[DeviceType, CommandAdapter] = {}
        self._register_adapters()

    def _register_adapters(self):
        """Регистрация всех адаптеров"""
        adapters = [
            LinuxCommandAdapter(),
            WindowsCommandAdapter(),
            macOSCommandAdapter(),
        ]

        for adapter in adapters:
            for device_type in adapter.supported_device_types:
                self.adapters[device_type] = adapter

    def get_adapter(self, device_type: DeviceType) -> Optional[CommandAdapter]:
        """Получить адаптер для типа устройства"""
        return self.adapters.get(device_type)


# Глобальная фабрика адаптеров
command_adapter_factory = CommandAdapterFactory()
