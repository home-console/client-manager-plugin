"""
Роуты для управления командами
"""

import logging
import time
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.models import CommandResult
from ..core.websocket_handler import WebSocketHandler
from ..schemas.cmd import CommandRequest, CommandResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Получаем экземпляр WebSocket обработчика (синглтон)
websocket_handler = WebSocketHandler()


class CommandRequestInternal(BaseModel):
    """Внутренний запрос команды"""
    command: str
    client_id: str


@router.post("/api/commands/{client_id}", response_model=CommandResponse)
async def send_command(client_id: str, request: CommandRequest):
    """Отправить команду клиенту"""
    # Проверяем, что клиент существует
    websocket = websocket_handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    # Генерируем ID команды
    command_id = f"cmd_{int(time.time())}"
    
    # Отправляем команду
    success = await websocket_handler.send_command_to_client(
        client_id, 
        request.command, 
        command_id
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки команды")
    
    return CommandResponse(
        success=True,
        result=f"Команда отправлена клиенту {client_id}",
        client_id=client_id
    )


@router.post("/api/commands/{client_id}/cancel")
async def cancel_command(client_id: str, command_id: str):
    """Отменить команду клиенту"""
    # Проверяем, что клиент существует
    websocket = websocket_handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    # Отправляем отмену
    success = await websocket_handler.send_cancel_to_client(client_id, command_id)
    
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки отмены")
    
    return {"message": f"Отмена команды {command_id} отправлена клиенту {client_id}"}


@router.get("/api/commands/history", response_model=List[CommandResult])
async def get_command_history():
    """Получить историю команд"""
    return websocket_handler.command_handler.get_command_history()


@router.get("/api/commands/{command_id}", response_model=CommandResult)
async def get_command_result(command_id: str):
    """Получить результат команды"""
    result = websocket_handler.command_handler.get_command_result(command_id)
    if not result:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    return result


@router.delete("/api/commands/history")
async def clear_command_history():
    """Очистить историю команд"""
    websocket_handler.command_handler.clear_command_history()
    return {"message": "История команд очищена"}
