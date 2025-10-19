"""
Модели данных для сервера
"""

from datetime import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel


class ClientInfo(BaseModel):
    """Информация о клиенте"""
    id: str
    hostname: str
    ip: str
    port: int
    connected_at: str
    last_heartbeat: str
    status: str
    capabilities: List[str]


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
