"""
Обработчик команд с поддержкой всех типов команд
"""

import asyncio
import logging
import time
import json
import subprocess
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from collections import deque
from fastapi import WebSocket
from enum import Enum

from .models import CommandResult, ChunkBuffer
from .client_manager import ClientManager
from .connection.websocket_manager import WebSocketManager
from .security.command_validator import CommandValidator, CommandSecurityLevel
from .security.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class CommandType(Enum):
    """Типы команд"""
    SYSTEM = "system"           # Системные команды (ls, ps, df, etc.)
    SHELL = "shell"            # Shell команды
    FILE = "file"              # Файловые операции
    NETWORK = "network"        # Сетевые команды
    MONITORING = "monitoring"   # Мониторинг системы
    CUSTOM = "custom"          # Пользовательские команды
    SCRIPT = "script"          # Выполнение скриптов
    SERVICE = "service"        # Управление сервисами


class CommandStatus(Enum):
    """Статусы выполнения команд"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    CANCELLING = "cancelling"


class CommandHandler:
    """Обработчик команд с поддержкой всех типов"""
    
    def __init__(self, client_manager: ClientManager, websocket_manager: WebSocketManager):
        self.client_manager = client_manager
        self.websocket_manager = websocket_manager
        
        # Инициализируем валидатор команд
        self.command_validator = CommandValidator()
        
        # Инициализируем rate limiter
        self.rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
        
        # Хранилище команд и результатов с ограничением размера
        self.max_history_size = 1000  # Максимум 1000 команд в истории
        self.command_history: deque = deque(maxlen=self.max_history_size)
        self.active_commands: Dict[str, Dict[str, Any]] = {}
        self.command_results: Dict[str, CommandResult] = {}
        self.chunk_buffers: Dict[str, ChunkBuffer] = {}
        
        # Events для ожидания результатов команд
        self.command_events: Dict[str, asyncio.Event] = {}
        
        # TTL для результатов (в секундах)
        self.result_ttl = 3600  # 1 час
        self.result_timestamps: Dict[str, float] = {}
        
        # Настройки безопасности (теперь в command_validator)
        self.max_command_timeout = 300  # 5 минут
        self.max_output_size = 10 * 1024 * 1024  # 10MB
        
        # Статистика
        self.stats = {
            "total_commands": 0,
            "successful_commands": 0,
            "failed_commands": 0,
            "cancelled_commands": 0,
            "blocked_commands": 0
        }
        
        # Попытка запустить фоновую задачу очистки, если есть активный event loop.
        # В средах без запущенного loop (например, unit-тесты, где dependency
        # создаётся вне lifespan) создание задачи невозможно — отложим старт.
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task_handle = loop.create_task(self._cleanup_task())
        except RuntimeError:
            self._cleanup_task_handle = None

    def start_cleanup(self):
        """Запустить фоновую задачу очистки, если она ещё не запущена.

        Вызывать из контекста с работающим event loop (например, в lifespan
        или при обработке первого запроса).
        """
        if getattr(self, "_cleanup_task_handle", None) is None:
            loop = asyncio.get_running_loop()
            self._cleanup_task_handle = loop.create_task(self._cleanup_task())

    def stop_cleanup(self):
        """Остановить фоновую задачу очистки, если она запущена."""
        handle = getattr(self, "_cleanup_task_handle", None)
        if handle is not None:
            handle.cancel()
            self._cleanup_task_handle = None
    
    async def handle_command_request(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка запроса на выполнение команды"""
        try:
            command_data = message.get('data', {})
            command = command_data.get('command', '')
            command_id = command_data.get('command_id', f"cmd_{int(time.time())}")
            timeout = command_data.get('timeout', self.max_command_timeout)
            
            # Проверка rate limit
            allowed, error_message = self.rate_limiter.check_rate_limit(client_id)
            if not allowed:
                self.stats["blocked_commands"] += 1
                await self._send_error_response(websocket, command_id, error_message)
                logger.warning(f"🚫 Rate limit для клиента {client_id}: {error_message}")
                return
            
            # Санитизация команды
            command = self.command_validator.sanitize_command(command)
            
            # Валидация команды с использованием нового валидатора
            is_valid, error_message = self.command_validator.validate_command(
                command, 
                client_id, 
                allowed_level=CommandSecurityLevel.MODERATE
            )
            
            if not is_valid:
                self.stats["blocked_commands"] += 1
                await self._send_error_response(websocket, command_id, error_message)
                logger.warning(f"🚫 Команда заблокирована от {client_id}: {command} - {error_message}")
                return
            
            # Создаем запись о команде
            self.active_commands[command_id] = {
                "client_id": client_id,
                "command": command,
                "status": CommandStatus.PENDING,
                "started_at": datetime.now(),
                "timeout": timeout,
                "websocket": websocket
            }
            
            # Создаем event для ожидания результата
            self.command_events[command_id] = asyncio.Event()
            
            # Отправляем команду клиенту
            await self._send_command_to_client(client_id, command, command_id, timeout)
            
            # Запускаем таймер для команды
            asyncio.create_task(self._command_timeout_handler(command_id))
            
            logger.info(f"📤 Команда отправлена клиенту {client_id}: {command} (ID: {command_id})")
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки команды: {e}")
            await self._send_error_response(websocket, command_id, str(e))
    
    async def handle_command_result(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка результата выполнения команды"""
        try:
            result_data = message.get('data', {})
            command_id = result_data.get('command_id', '')
            success = result_data.get('success', False)
            result = result_data.get('result', '')
            error = result_data.get('error', '')
            
            if command_id not in self.active_commands:
                # Если результат уже сохранён ранее (например, после отмены), не шумим
                if command_id in self.command_results:
                    return
                logger.debug(f"⚠️ Получен результат для неизвестной команды: {command_id}")
                return
            
            # Обновляем статус команды
            if success:
                self.active_commands[command_id]['status'] = CommandStatus.COMPLETED
            else:
                if str(error).lower() == "cancelled":
                    self.active_commands[command_id]['status'] = CommandStatus.CANCELLED
                else:
                    self.active_commands[command_id]['status'] = CommandStatus.FAILED
            
            # Создаем результат
            command_result = CommandResult(
                command_id=command_id,
                client_id=client_id,
                success=success,
                result=result,
                error=error,
                timestamp=datetime.now().isoformat()
            )
            
            # Сохраняем результат
            self.command_results[command_id] = command_result
            self.command_history.append(command_result)
            self.result_timestamps[command_id] = time.time()
            
            # Обновляем статистику
            self.stats["total_commands"] += 1
            if success:
                self.stats["successful_commands"] += 1
            else:
                if str(error).lower() == "cancelled":
                    self.stats["cancelled_commands"] += 1
                else:
                    self.stats["failed_commands"] += 1
            
            # Удаляем из активных команд
            del self.active_commands[command_id]
            
            # Уведомляем ожидающих через Event
            if command_id in self.command_events:
                self.command_events[command_id].set()
            
            logger.info(f"✅ Результат команды {command_id} получен от {client_id}: {'SUCCESS' if success else 'FAILED'}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки результата команды: {e}")
    
    async def handle_result_chunk(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка чанка результата команды"""
        try:
            chunk_data = message.get('data', {})
            command_id = chunk_data.get('command_id', '')
            chunk_index = chunk_data.get('chunk_index', 0)
            chunk_data_content = chunk_data.get('data', '')
            total_chunks = chunk_data.get('total_chunks', 1)
            
            if command_id not in self.chunk_buffers:
                self.chunk_buffers[command_id] = ChunkBuffer(
                    chunks={},
                    total=total_chunks,
                    received=0
                )
            
            buffer = self.chunk_buffers[command_id]
            buffer.chunks[chunk_index] = chunk_data_content
            buffer.received += 1
            
            logger.debug(f"📦 Получен чанк {chunk_index}/{total_chunks} для команды {command_id}")
            
            # Если все чанки получены, собираем результат
            if buffer.received >= buffer.total:
                await self._assemble_chunked_result(command_id, client_id)
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки чанка: {e}")
    
    async def handle_result_eof(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка сигнала окончания передачи результата"""
        try:
            eof_data = message.get('data', {})
            command_id = eof_data.get('command_id', '')
            
            if command_id in self.chunk_buffers:
                await self._assemble_chunked_result(command_id, client_id)
            
            logger.info(f"🏁 Получен EOF для команды {command_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки EOF: {e}")
    
    async def handle_command_cancel(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка отмены команды"""
        try:
            cancel_data = message.get('data', {})
            command_id = cancel_data.get('command_id', '')
            
            if command_id in self.active_commands:
                # Отправляем сигнал отмены клиенту
                await self._send_cancel_to_client(client_id, command_id)
                
                # Обновляем статус на "cancelling" — ждём ack/результат
                self.active_commands[command_id]['status'] = CommandStatus.CANCELLING
                
                
                logger.info(f"🚫 Команда {command_id} отменена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка отмены команды: {e}")

    async def handle_command_cancel_ack(self, websocket: WebSocket, message: dict, client_id: str):
        """ACK от клиента: отмена принята, команда в процессе завершения"""
        try:
            data = message.get('data', {})
            command_id = data.get('command_id', '')
            if command_id in self.active_commands:
                self.active_commands[command_id]['status'] = CommandStatus.CANCELLING
                logger.info(f"🛈 Получен ACK отмены для команды {command_id} от {client_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки command_cancel_ack: {e}")
    
    async def _send_command_to_client(self, client_id: str, command: str, command_id: str, timeout: int):
        """Отправка команды клиенту"""
        websocket = self.client_manager.get_client(client_id)
        if not websocket:
            raise Exception(f"Клиент {client_id} не найден")
        
        command_msg = {
            "type": "command",
            "data": {
                "command": command,
                "command_id": command_id,
                "timeout": timeout
            }
        }
        
        await self.websocket_manager.send_message(client_id, json.dumps(command_msg))
    
    async def _send_cancel_to_client(self, client_id: str, command_id: str):
        """Отправка сигнала отмены клиенту"""
        websocket = self.client_manager.get_client(client_id)
        if not websocket:
            return
        
        cancel_msg = {
            "type": "command_cancel",
            "data": {
                "command_id": command_id
            }
        }
        
        await self.websocket_manager.send_message(client_id, json.dumps(cancel_msg))
    
    async def _send_error_response(self, websocket: WebSocket, command_id: str, error: str):
        """Отправка ошибки клиенту"""
        error_msg = {
            "type": "command_error",
            "data": {
                "command_id": command_id,
                "error": error
            }
        }
        
        await websocket.send_text(json.dumps(error_msg))
    
    async def _assemble_chunked_result(self, command_id: str, client_id: str):
        """Сборка результата из чанков"""
        try:
            buffer = self.chunk_buffers[command_id]
            
            # Собираем чанки в правильном порядке
            result_parts = []
            for i in range(buffer.total):
                if i in buffer.chunks:
                    result_parts.append(buffer.chunks[i])
            
            assembled_result = ''.join(result_parts)
            
            # Создаем финальный результат
            command_result = CommandResult(
                command_id=command_id,
                client_id=client_id,
                success=True,
                result=assembled_result,
                timestamp=datetime.now().isoformat()
            )
            
            self.command_results[command_id] = command_result
            self.command_history.append(command_result)
            
            # Очищаем буфер
            del self.chunk_buffers[command_id]
            
            # Уведомляем ожидающих через Event
            if command_id in self.command_events:
                self.command_events[command_id].set()
            
            logger.info(f"🔧 Результат команды {command_id} собран из {buffer.total} чанков")
            
        except Exception as e:
            logger.error(f"❌ Ошибка сборки результата: {e}")
    
    async def _command_timeout_handler(self, command_id: str):
        """Обработчик таймаута команды"""
        try:
            if command_id not in self.active_commands:
                return
            
            command_info = self.active_commands[command_id]
            timeout = command_info.get('timeout', self.max_command_timeout)
            
            await asyncio.sleep(timeout)
            
            # Если команда все еще активна, отменяем ее
            if command_id in self.active_commands:
                command_info['status'] = CommandStatus.TIMEOUT
                
                # Отправляем сигнал отмены клиенту
                await self._send_cancel_to_client(command_info['client_id'], command_id)
                
                logger.warning(f"⏰ Команда {command_id} превысила таймаут ({timeout}s)")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработчика таймаута: {e}")
    
    def get_command_stats(self) -> Dict[str, Any]:
        """Получить статистику команд"""
        return {
            "stats": self.stats,
            "active_commands": len(self.active_commands),
            "total_history": len(self.command_history),
            "chunk_buffers": len(self.chunk_buffers),
            "rate_limiter": self.rate_limiter.get_global_stats()
        }
    
    def get_command_history(self, limit: int = 100) -> List[CommandResult]:
        """Получить историю команд"""
        # deque не поддерживает срезы — конвертируем в список и отдаем хвост
        history_list = list(self.command_history)
        return history_list[-limit:]
    
    def get_active_commands(self) -> Dict[str, Dict[str, Any]]:
        """Получить активные команды"""
        return self.active_commands.copy()
    
    def get_command_result(self, command_id: str) -> Optional[CommandResult]:
        """Получить результат команды"""
        return self.command_results.get(command_id)
    
    async def add_command(self, client_id: str, command_id: str, command: str, timeout: int = 300):
        """Добавить команду в активные команды"""
        self.active_commands[command_id] = {
            'client_id': client_id,
            'command': command,
            'status': CommandStatus.PENDING,
            'start_time': time.time(),
            'timeout': timeout
        }
        
        # Создаем event для ожидания результата
        self.command_events[command_id] = asyncio.Event()
        
        logger.debug(f"📝 Команда {command_id} добавлена в активные команды")
    
    async def remove_command(self, command_id: str):
        """Удалить команду из активных команд"""
        if command_id in self.active_commands:
            del self.active_commands[command_id]
        
        if command_id in self.command_events:
            del self.command_events[command_id]
        
        logger.debug(f"🗑️ Команда {command_id} удалена из активных команд")
    
    async def wait_for_command_result(self, command_id: str, timeout: int = 30) -> Optional[CommandResult]:
        """
        Ожидание результата команды через Event (эффективный подход)
        
        Args:
            command_id: ID команды
            timeout: Таймаут в секундах
            
        Returns:
            CommandResult или None при таймауте
        """
        # Проверяем, есть ли уже результат
        result = self.command_results.get(command_id)
        if result:
            return result
        
        # Получаем или создаем event для команды
        if command_id not in self.command_events:
            self.command_events[command_id] = asyncio.Event()
        
        event = self.command_events[command_id]
        
        try:
            # Ждем с таймаутом
            await asyncio.wait_for(event.wait(), timeout=timeout)
            
            # Получаем результат после уведомления
            result = self.command_results.get(command_id)
            return result
            
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут ожидания результата команды {command_id} ({timeout}s)")
            return None
        finally:
            # Очищаем event после использования
            if command_id in self.command_events:
                del self.command_events[command_id]
    
    async def _cleanup_task(self):
        """Фоновая задача для очистки устаревших данных"""
        while True:
            try:
                await asyncio.sleep(300)  # Каждые 5 минут
                await self._cleanup_old_results()
                await self._cleanup_stale_chunks()
            except Exception as e:
                logger.error(f"❌ Ошибка в задаче очистки: {e}")
    
    async def _cleanup_old_results(self):
        """Очистка устаревших результатов команд"""
        current_time = time.time()
        expired_commands = []
        
        for command_id, timestamp in self.result_timestamps.items():
            if current_time - timestamp > self.result_ttl:
                expired_commands.append(command_id)
        
        for command_id in expired_commands:
            if command_id in self.command_results:
                del self.command_results[command_id]
            if command_id in self.result_timestamps:
                del self.result_timestamps[command_id]
        
        if expired_commands:
            logger.info(f"🧹 Очищено {len(expired_commands)} устаревших результатов команд")
    
    async def _cleanup_stale_chunks(self):
        """Очистка зависших chunk буферов"""
        stale_buffers = []
        
        for command_id in self.chunk_buffers.keys():
            if command_id not in self.active_commands:
                stale_buffers.append(command_id)
        
        for command_id in stale_buffers:
            del self.chunk_buffers[command_id]
        
        if stale_buffers:
            logger.info(f"🧹 Очищено {len(stale_buffers)} зависших chunk буферов")
