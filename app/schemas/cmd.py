from typing import Any, Optional
from pydantic import BaseModel


class CommandRequest(BaseModel):
    # Простой режим: плоская строка команды
    command: Optional[str] = None
    client_id: Optional[str] = None
    # Модульный режим: имя и параметры
    name: Optional[str] = None
    params: Optional[dict] = None  # произвольные параметры для команды

class CommandResponse(BaseModel):
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    client_id: Optional[str] = None