"""
Роуты для управления командами
"""

import logging
import time
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from enum import Enum

from ..core.models import CommandResult
from ..dependencies import get_websocket_handler
from ..schemas.cmd import CommandRequest, CommandResponse

logger = logging.getLogger(__name__)

router = APIRouter()


class CommandRequestInternal(BaseModel):
    """Внутренний запрос команды"""
    command: str
    client_id: str


class CommandAccepted(BaseModel):
    """Ответ для асинхронного запуска команды"""
    command_id: str
    client_id: str
    status: str = "queued"


class CommandNameEnum(str, Enum):
    """Имя базовой команды (выпадающий список в query)"""
    ls = "ls"
    cat = "cat"
    head = "head"
    tail = "tail"
    pwd = "pwd"
    whoami = "whoami"
    hostname = "hostname"
    uname = "uname"
    date = "date"
    uptime = "uptime"
    env = "env"
    echo = "echo"
    grep = "grep"
    find = "find"
    awk = "awk"
    sed = "sed"
    sort = "sort"
    uniq = "uniq"
    wc = "wc"
    ps = "ps"
    df = "df"
    du = "du"
    ping = "ping"
    ip = "ip"
    curl = "curl"
    wget = "wget"
    touch = "touch"
    mkdir = "mkdir"
    cp = "cp"
    mv = "mv"
    tar = "tar"
    zip = "zip"


def _build_command_text(name: Optional[str], body: Optional[CommandRequest]) -> str:
    """Построение текста команды из query name или тела запроса."""
    if body and body.command:
        return body.command
    if name:
        # Собираем из name и params (если есть)
        params: Dict[str, Any] = body.params if body and body.params else {}
        # params может быть как dict опций/аргументов; формируем строку предсказуемо
        parts: List[str] = [name]
        for k, v in params.items():
            if v is None:
                parts.append(str(k))
            else:
                parts.append(str(k))
                parts.append(str(v))
        return " ".join(parts)
    raise HTTPException(status_code=400, detail="Необходимо указать либо command в теле, либо name в query")


@router.post("/api/commands/{client_id}", response_model=CommandResult)
async def execute_command(
    client_id: str,
    request: Optional[CommandRequest] = None,
    timeout: int = 30,
    name: Optional[CommandNameEnum] = Query(
        None,
        description="Имя команды (query param)",
        examples={"default": {"value": "ls"}},
    ),
    handler = Depends(get_websocket_handler),
):
    """Выполнить команду и получить результат
    - Либо передайте `command` в теле
    - Либо укажите `name` в query и опциональные `params` в теле
    """
    websocket = handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    command_id = f"cmd_{int(time.time())}"

    command_text = _build_command_text(name=name, body=request)

    success = await handler.send_command_to_client(
        client_id,
        command_text,
        command_id
    )
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки команды")

    result = await handler.command_handler.wait_for_command_result(command_id, timeout)
    if not result:
        raise HTTPException(status_code=408, detail=f"Таймаут ожидания результата команды ({timeout}с)")

    return result


@router.post("/api/commands/{client_id}/async", response_model=CommandAccepted)
async def execute_command_async(
    client_id: str,
    request: Optional[CommandRequest] = None,
    name: Optional[CommandNameEnum] = Query(
        None,
        description="Имя команды (query param)",
        examples={"default": {"value": "ls"}},
    ),
    handler = Depends(get_websocket_handler),
):
    """Асинхронный запуск команды: сразу возвращает command_id без ожидания результата
    - Либо `command` в теле
    - Либо `name` в query и опциональные `params` в теле
    """
    websocket = handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    command_id = f"cmd_{int(time.time())}"

    command_text = _build_command_text(name=name, body=request)

    success = await handler.send_command_to_client(client_id, command_text, command_id)
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки команды")

    return CommandAccepted(command_id=command_id, client_id=client_id, status="queued")


@router.post("/api/commands/{client_id}/cancel")
async def cancel_command(client_id: str, command_id: str, timeout: int = 30, handler = Depends(get_websocket_handler)):
    """Отменить команду клиенту и дождаться результата отмены"""
    # Проверяем, что клиент зарегистрирован (даже если мы не имеем реального
    # WebSocket объекта в тестах, ключ в `clients` служит признаком регистрации).
    if client_id not in handler.client_manager.clients:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    # Отправляем отмену
    success = await handler.send_cancel_to_client(client_id, command_id)
    
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка отправки отмены")

    # Ждём подтверждение отмены от клиента через общий механизм результатов
    result = await handler.command_handler.wait_for_command_result(command_id, timeout)
    if not result:
        raise HTTPException(status_code=408, detail=f"Таймаут ожидания отмены команды ({timeout}с)")
    return result


@router.get("/api/commands/history", response_model=List[CommandResult])
async def get_command_history(handler = Depends(get_websocket_handler)):
    """Получить историю команд"""
    return handler.command_handler.get_command_history()


@router.get("/api/commands/{command_id}/status")
async def get_command_status(command_id: str, handler = Depends(get_websocket_handler)):
    """Получить статус команды: active (с деталями) или финальный результат"""
    # Сначала проверим активные команды
    active = handler.command_handler.get_active_commands().get(command_id)
    if active:
        status = active.get("status")
        # Если статус — Enum, отдадим его значение (например 'pending'),
        # иначе строковое представление.
        try:
            status_value = status.value if hasattr(status, "value") else str(status)
        except Exception:
            status_value = str(status)
        return {
            "command_id": command_id,
            "client_id": active.get("client_id"),
            "status": status_value,
            "started_at": active.get("started_at"),
            "timeout": active.get("timeout")
        }

    # Если не активна — отдадим сохранённый результат (если есть)
    result = handler.command_handler.get_command_result(command_id)
    if result:
        return result

    raise HTTPException(status_code=404, detail="Команда не найдена")


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

