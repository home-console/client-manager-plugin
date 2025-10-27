"""
Роуты для управления командами
"""

import logging
import time
import asyncio
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from ..core.models import CommandResult
from ..dependencies import get_websocket_handler
from ..schemas.cmd import CommandRequest, CommandResponse

logger = logging.getLogger(__name__)

router = APIRouter()


class CommandRequestInternal(BaseModel):
    """Внутренний запрос команды"""
    command: str
    client_id: str


@router.post("/api/commands/{client_id}", response_model=CommandResult)
async def execute_command(client_id: str, request: CommandRequest, timeout: int = 30, 
                         handler = Depends(get_websocket_handler)):
    """Выполнить команду и получить результат"""
    # Проверяем, что клиент существует
    websocket = handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    # Генерируем ID команды
    command_id = f"cmd_{int(time.time())}"
    
    # Определяем текст команды в зависимости от режима
    if request.command:
        command_text = request.command
    elif request.name and request.params is not None:
        command_text = f"{request.name} {request.params}"
    else:
        raise HTTPException(status_code=400, detail="Необходимо указать либо command, либо name+params")
    
    # Отправляем команду
    success = await handler.send_command_to_client(
        client_id, 
        command_text, 
        command_id
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки команды")
    
    # Ждем результат выполнения команды (Event-driven подход)
    result = await handler.command_handler.wait_for_command_result(command_id, timeout)
    if not result:
        raise HTTPException(status_code=408, detail=f"Таймаут ожидания результата команды ({timeout}с)")
    
    return result


@router.post("/api/commands/{client_id}/cancel")
async def cancel_command(client_id: str, command_id: str, handler = Depends(get_websocket_handler)):
    """Отменить команду клиенту"""
    # Проверяем, что клиент существует
    websocket = handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    # Отправляем отмену
    success = await handler.send_cancel_to_client(client_id, command_id)
    
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки отмены")
    
    return {"message": f"Отмена команды {command_id} отправлена клиенту {client_id}"}


@router.get("/api/commands/history", response_model=List[CommandResult])
async def get_command_history(handler = Depends(get_websocket_handler)):
    """Получить историю команд"""
    return handler.command_handler.get_command_history()


@router.get("/api/commands/{command_id}", response_model=CommandResult)
async def get_command_result(command_id: str, handler = Depends(get_websocket_handler)):
    """Получить результат команды"""
    result = handler.command_handler.get_command_result(command_id)
    if not result:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    return result


@router.delete("/api/commands/history")
async def clear_command_history(handler = Depends(get_websocket_handler)):
    """Очистить историю команд"""
    handler.command_handler.clear_command_history()
    return {"message": "История команд очищена"}


@router.get("/api/commands/rate-limit/{client_id}")
async def get_rate_limit_status(client_id: str, handler = Depends(get_websocket_handler)):
    """Получить статус rate limit для клиента"""
    stats = handler.command_handler.rate_limiter.get_client_stats(client_id)
    return stats


@router.post("/api/commands/rate-limit/{client_id}/reset")
async def reset_rate_limit(client_id: str, handler = Depends(get_websocket_handler)):
    """Сбросить rate limit для клиента"""
    handler.command_handler.rate_limiter.reset_client(client_id)
    return {"message": f"Rate limit сброшен для клиента {client_id}"}

