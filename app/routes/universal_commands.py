"""
Роуты для универсальных команд умного дома
"""

import logging
import time
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from ..core.models import DeviceType, DeviceCapabilities
from ..core.commands import UniversalCommand, CommandType, command_adapter_factory
from ..dependencies import get_websocket_handler

logger = logging.getLogger(__name__)

router = APIRouter()


class UniversalCommandRequest(BaseModel):
    """Запрос универсальной команды"""
    command_type: CommandType
    params: Optional[Dict[str, Any]] = None
    timeout: int = 30
    async_execution: bool = False


class DeviceGroupCommandRequest(BaseModel):
    """Запрос команды для группы устройств"""
    device_type: Optional[DeviceType] = None
    tags: Optional[List[str]] = None
    capabilities: Optional[List[str]] = None
    command: UniversalCommandRequest


@router.post("/universal/{client_id}/execute")
async def execute_universal_command(
    client_id: str,
    request: UniversalCommandRequest,
    handler = Depends(get_websocket_handler)
):
    """
    Выполнить универсальную команду на устройстве

    Универсальные команды автоматически адаптируются под тип устройства:
    - file.copy -> cp (Linux), copy (Windows), cp (macOS)
    - system.info -> uname + os-release (Linux), systeminfo (Windows)
    """
    # Получаем информацию о клиенте
    client_info = handler.client_manager.get_client_info(client_id)
    if not client_info:
        raise HTTPException(status_code=404, detail="Устройство не найдено")

    # Создаем универсальную команду
    command = UniversalCommand(
        command_type=request.command_type,
        params=request.params or {},
        timeout=request.timeout,
        async_execution=request.async_execution
    )

    # Получаем адаптер для типа устройства
    adapter = command_adapter_factory.get_adapter(client_info.device_type)
    if not adapter:
        raise HTTPException(
            status_code=400,
            detail=f"Адаптер команд не найден для типа устройства: {client_info.device_type}"
        )

    # Адаптируем команду под устройство
    native_command = adapter.adapt_command(command)
    logger.info(f"Универсальная команда {request.command_type} адаптирована для {client_info.device_type}: {native_command}")

    # Выполняем команду
    websocket = handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Устройство не подключено")

    command_id = f"universal_{int(time.time())}"

    success = await handler.send_command_to_client(
        client_id,
        native_command,
        command_id
    )

    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки команды")

    if request.async_execution:
        return {
            "command_id": command_id,
            "status": "queued",
            "adapted_command": native_command
        }

    # Ждем результат
    result = await handler.command_handler.wait_for_command_result(command_id, request.timeout)
    if not result:
        raise HTTPException(status_code=408, detail=f"Таймаут выполнения команды ({request.timeout}с)")

    # Парсим результат через адаптер
    parsed_result = adapter.parse_result(command, result.result)

    return {
        "command_id": command_id,
        "success": result.success,
        "result": parsed_result,
        "error": result.error,
        "adapted_command": native_command,
        "execution_time": result.timestamp
    }


@router.post("/universal/group/execute")
async def execute_group_command(
    request: DeviceGroupCommandRequest,
    handler = Depends(get_websocket_handler)
):
    """
    Выполнить команду на группе устройств

    Можно фильтровать устройства по:
    - device_type: тип устройства (linux, windows, macos, etc.)
    - tags: список тегов
    - capabilities: требуемые возможности
    """
    # Находим подходящие устройства
    target_clients = {}

    # Фильтр по типу устройства
    if request.device_type:
        target_clients.update(handler.client_manager.get_clients_by_type(request.device_type))

    # Фильтр по тегам
    if request.tags:
        for tag in request.tags:
            tag_clients = handler.client_manager.get_clients_by_tag(tag)
            target_clients.update(tag_clients)

    # Фильтр по возможностям
    if request.capabilities:
        for capability in request.capabilities:
            cap_clients = handler.client_manager.get_clients_by_capability(capability)
            target_clients.update(cap_clients)

    if not target_clients:
        # Если нет фильтров, берем все устройства
        if not any([request.device_type, request.tags, request.capabilities]):
            target_clients = handler.client_manager.get_all_clients()
        else:
            raise HTTPException(status_code=404, detail="Устройства с указанными критериями не найдены")

    # Выполняем команду на каждом устройстве
    results = {}
    for client_id, client_info in target_clients.items():
        try:
            # Создаем под-запрос для каждого устройства
            device_request = UniversalCommandRequest(
                command_type=request.command.command_type,
                params=request.command.params,
                timeout=request.command.timeout,
                async_execution=True  # Групповые команды всегда асинхронные
            )

            result = await execute_universal_command(client_id, device_request, handler)
            results[client_id] = {
                "status": "success",
                "command_id": result["command_id"],
                "hostname": client_info.hostname,
                "device_type": client_info.device_type.value
            }

        except Exception as e:
            results[client_id] = {
                "status": "error",
                "error": str(e),
                "hostname": client_info.hostname,
                "device_type": client_info.device_type.value
            }

    return {
        "total_devices": len(target_clients),
        "results": results,
        "group_criteria": {
            "device_type": request.device_type.value if request.device_type else None,
            "tags": request.tags,
            "capabilities": request.capabilities
        }
    }


@router.get("/universal/capabilities")
async def get_device_capabilities():
    """
    Получить список всех возможных возможностей клиентов
    """
    return {
        device_type.value: list(capabilities)
        for device_type, capabilities in DeviceCapabilities.CAPABILITIES.items()
    }


@router.get("/universal/commands")
async def get_supported_commands():
    """
    Получить список всех поддерживаемых универсальных команд
    """
    return {
        "commands": [cmd.value for cmd in CommandType],
        "categories": {
            "system": [cmd.value for cmd in CommandType if cmd.value.startswith("system.")],
            "file": [cmd.value for cmd in CommandType if cmd.value.startswith("file.")],
            "disk": [cmd.value for cmd in CommandType if cmd.value.startswith("disk.")],
            "network": [cmd.value for cmd in CommandType if cmd.value.startswith("network.")],
            "proxmox": [cmd.value for cmd in CommandType if cmd.value.startswith("proxmox.")],
            "storage": [cmd.value for cmd in CommandType if cmd.value.startswith("storage.")]
        }
    }


@router.get("/clients/types", tags=["Clients"])
async def get_device_types(handler = Depends(get_websocket_handler)):
    """
    Получить статистику клиентов по типам
    """
    all_clients = handler.client_manager.get_all_clients()
    stats = {}

    for client_info in all_clients.values():
        device_type = client_info.device_type.value
        if device_type not in stats:
            stats[device_type] = {
                "count": 0,
                "clients": [],
                "capabilities": list(DeviceCapabilities.CAPABILITIES.get(client_info.device_type, set()))
            }
        stats[device_type]["count"] += 1
        stats[device_type]["clients"].append({
            "id": client_info.id,
            "hostname": client_info.hostname,
            "status": client_info.status,
            "connected_at": client_info.connected_at
        })

    return stats
