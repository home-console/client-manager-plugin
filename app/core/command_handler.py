"""
Обработчик команд для клиентов
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from fastapi import WebSocket

from .models import CommandResult, ChunkBuffer

logger = logging.getLogger(__name__)


class CommandHandler:
    """Обработчик команд"""
    
    def __init__(self):
        self.command_history: List[CommandResult] = []
        self.command_results: Dict[str, CommandResult] = {}
        self.chunk_buffers: Dict[str, ChunkBuffer] = {}
    
    async def handle_client_message(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка сообщений от клиентов"""
        msg_type = message.get('type')
        payload = message.get('data') if isinstance(message.get('data'), dict) else message
        
        if msg_type == 'key_exchange':
            # PSK режим: игнорируем key_exchange, клиент уйдёт в fallback
            logger.info(f"key_exchange получен, PSK режим — игнорируем")
            return
        
        elif msg_type == 'heartbeat':
            # Heartbeat обрабатывается в ClientManager
            return
            
        elif msg_type == 'command_result':
            # Результат выполнения команды (поддержка чанков)
            await self._handle_command_result(websocket, payload, client_id)
            
        elif msg_type == 'result_chunk':
            # Чанк результата команды
            await self._handle_result_chunk(websocket, payload, client_id)
            
        elif msg_type == 'result_eof':
            # Конец передачи результата
            await self._handle_result_eof(websocket, payload, client_id)
            
        elif msg_type == 'auth':
            # Аутентификация клиента
            await self._handle_auth(websocket, payload, client_id)
    
    async def _handle_command_result(self, websocket: WebSocket, payload: dict, client_id: str):
        """Обработка результата команды"""
        command_id = payload.get('command_id')
        success = payload.get('success', False)
        result = payload.get('result', '')
        error = payload.get('error')
        
        if command_id:
            # Сохраняем результат
            command_result = CommandResult(
                command_id=command_id,
                client_id=client_id,
                success=success,
                result=result,
                error=error,
                timestamp=datetime.now().isoformat()
            )
            
            self.command_results[command_id] = command_result
            self.command_history.append(command_result)
            
            logger.info(f"📥 Результат команды {command_id}: success={success}")
            if error:
                logger.error(f"❌ Ошибка команды {command_id}: {error}")
    
    async def _handle_result_chunk(self, websocket: WebSocket, payload: dict, client_id: str):
        """Обработка чанка результата"""
        command_id = payload.get('command_id')
        chunk_index = payload.get('chunk_index', 0)
        chunks_total = payload.get('chunks_total', 1)
        chunk_data = payload.get('chunk', '')
        
        if command_id not in self.chunk_buffers:
            self.chunk_buffers[command_id] = ChunkBuffer(
                total=chunks_total
            )
        
        buffer = self.chunk_buffers[command_id]
        buffer.chunks[chunk_index] = chunk_data
        buffer.received += 1
        
        logger.debug(f"📦 Чанк {chunk_index+1}/{chunks_total} для команды {command_id}")
    
    async def _handle_result_eof(self, websocket: WebSocket, payload: dict, client_id: str):
        """Обработка конца передачи результата"""
        command_id = payload.get('command_id')
        
        if command_id in self.chunk_buffers:
            buffer = self.chunk_buffers[command_id]
            # Собираем все чанки
            result_parts = []
            for i in range(buffer.total):
                if i in buffer.chunks:
                    result_parts.append(buffer.chunks[i])
            
            result = ''.join(result_parts)
            
            # Сохраняем полный результат
            command_result = CommandResult(
                command_id=command_id,
                client_id=client_id,
                success=True,
                result=result,
                timestamp=datetime.now().isoformat()
            )
            
            self.command_results[command_id] = command_result
            self.command_history.append(command_result)
            
            # Очищаем буфер
            del self.chunk_buffers[command_id]
            
            logger.info(f"📥 Полный результат команды {command_id} собран из {buffer.total} чанков")
    
    async def _handle_auth(self, websocket: WebSocket, payload: dict, client_id: str):
        """Обработка аутентификации"""
        auth_token = payload.get('auth_token', '')
        
        # Простая проверка токена (в реальном приложении нужна более сложная логика)
        if auth_token:
            logger.info(f"🔐 Аутентификация клиента {client_id}")
            # Отправляем подтверждение
            response = {"type": "auth_ok"}
            await websocket.send_text(str(response))
        else:
            logger.warning(f"⚠️ Пустой токен аутентификации от клиента {client_id}")
    
    def get_command_result(self, command_id: str) -> Optional[CommandResult]:
        """Получить результат команды"""
        return self.command_results.get(command_id)
    
    def get_command_history(self) -> List[CommandResult]:
        """Получить историю команд"""
        return self.command_history.copy()
    
    def clear_command_history(self):
        """Очистить историю команд"""
        self.command_history.clear()
        self.command_results.clear()
        self.chunk_buffers.clear()
