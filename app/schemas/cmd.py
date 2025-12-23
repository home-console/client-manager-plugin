from typing import Any, Optional
from pydantic import BaseModel, Field


class CommandRequest(BaseModel):
    """Запрос на выполнение команды"""
    # Простой режим: плоская строка команды
    command: Optional[str] = Field(
        None,
        description="Команда для выполнения (простой режим)",
        json_schema_extra={"example": "ls -la"},
    )
    # Модульный режим: имя и параметры
    name: Optional[str] = Field(
        None,
        description="Имя команды (модульный режим)",
        json_schema_extra={"example": "file_operations"},
    )
    params: Optional[dict] = Field(
        None,
        description="Параметры команды (модульный режим)",
        json_schema_extra={"example": {"path": "/tmp"}},
    )
    
    def model_post_init(self, __context: Any) -> None:
        """Валидация: должен быть указан либо command, либо name+params"""
        if not self.command and not (self.name and self.params is not None):
            raise ValueError("Необходимо указать либо 'command' (простой режим), либо 'name' и 'params' (модульный режим)")


class CommandResponse(BaseModel):
    """Ответ на запрос команды (устаревший, используется только для внутренних целей)"""
    success: bool = Field(..., description="Успешность выполнения")
    result: Optional[Any] = Field(None, description="Результат выполнения")
    error: Optional[str] = Field(None, description="Ошибка выполнения")
    client_id: Optional[str] = Field(None, description="ID клиента")