"""
Единый WebSocket обработчик с улучшенной архитектурой
Объединяет лучшие практики из всех предыдущих версий
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from .connection.websocket_manager import WebSocketManager
from .security.encryption_service import EncryptionService
from .security.auth_service import AuthService
from .messaging.message_router import MessageRouter
from .client_manager import ClientManager
from .command_handler import CommandHandler
from .models import ClientInfo

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """WebSocket обработчик для управления клиентами"""
    
    def __init__(self):
        # Инициализация модулей
        self.websocket_manager = WebSocketManager()
        self.encryption_service = EncryptionService()
        self.auth_service = AuthService()
        self.message_router = MessageRouter()
        self.client_manager = ClientManager()
        self.command_handler = CommandHandler(self.client_manager, self.websocket_manager)
        
        # Регистрация обработчиков сообщений
        self._register_handlers()
        
        # Регистрация middleware
        self._register_middleware()
        
        # Статистика
        self.stats = {
            "total_connections": 0,
            "active_connections": 0,
            "total_messages": 0,
            "total_commands": 0,
            "uptime_start": time.time()
        }
    
    def _register_handlers(self):
        """Регистрация обработчиков сообщений"""
        from .messaging.message_router import RegistrationHandler, HeartbeatHandler
        
        # Обработчик регистрации
        registration_handler = RegistrationHandler(
            self.client_manager, 
            self.encryption_service
        )
        self.message_router.register_handler('register', registration_handler.handle)
        
        # Обработчик heartbeat
        heartbeat_handler = HeartbeatHandler(self.client_manager)
        self.message_router.register_handler('heartbeat', heartbeat_handler.handle)
        
        # Обработчики команд
        self.message_router.register_handler('command_request', self.command_handler.handle_command_request)
        self.message_router.register_handler('command_result', self.command_handler.handle_command_result)
        self.message_router.register_handler('result_chunk', self.command_handler.handle_result_chunk)
        self.message_router.register_handler('result_eof', self.command_handler.handle_result_eof)
        self.message_router.register_handler('command_cancel', self.command_handler.handle_command_cancel)
        
        # Обработчик аутентификации
        self.message_router.register_handler('auth', self._handle_auth)
        
        # Обработчик key_exchange (игнорируем в PSK режиме)
        self.message_router.register_handler('key_exchange', self._handle_key_exchange)
        
        logger.info("✅ Все обработчики сообщений зарегистрированы")
    
    def _register_middleware(self):
        """Регистрация middleware"""
        # Middleware для обновления статистики
        self.message_router.register_middleware(self._stats_middleware)
        
        logger.info("✅ Все middleware зарегистрированы")
    
    async def handle_websocket(self, websocket: WebSocket):
        """Обработка WebSocket соединения"""
        client_id = "unknown"
        
        try:
            # Подключаем клиента
            await self.websocket_manager.connect(websocket, client_id)
            self.stats["total_connections"] += 1
            self.stats["active_connections"] += 1
            
            # Основной цикл обработки сообщений
            while True:
                # Получение сообщения
                data = await websocket.receive_text()
                logger.debug(f"📥 Получено сообщение от {client_id}: {len(data)} байт")
                
                # Дешифрование и маршрутизация
                message = await self.encryption_service.decrypt_message(data, client_id)
                logger.debug(f"📥 Обработанное сообщение: {message}")
                
                # Обработка регистрации (особый случай)
                if message.get('type') == 'register':
                    # Обновляем client_id после регистрации
                    new_client_id = await self._handle_registration(websocket, message)
                    if new_client_id != "unknown":
                        # Обновляем client_id в WebSocketManager
                        await self.websocket_manager.update_client_id(client_id, new_client_id)
                        client_id = new_client_id
                        
                        # Обновляем метаданные соединения
                        self.websocket_manager.update_metadata(client_id, {
                            'registered': True,
                            'registered_at': time.time()
                        })
                        
                        # Запускаем мониторинг соединения после регистрации
                        await self.websocket_manager.start_monitoring(client_id)
                else:
                    # Маршрутизация остальных сообщений
                    await self.message_router.route_message(websocket, message, client_id)
                
        except WebSocketDisconnect:
            logger.info(f"🔌 WebSocket отключен: {client_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка WebSocket: {e}", exc_info=True)
        finally:
            if client_id != "unknown":
                await self._cleanup_client(client_id)
            self.stats["active_connections"] -= 1
    
    async def _handle_registration(self, websocket: WebSocket, message: dict) -> str:
        """Обработка регистрации клиента"""
        # Регистрируем клиента
        client_id = await self.client_manager.register_client(websocket, message.get('data', {}))
        
        # Сбрасываем состояние шифрования при переподключении
        if client_id in self.encryption_service.encryption_states:
            self.encryption_service.reset_encryption_state(client_id)
        
        # Отправляем подтверждение
        response = {
            "type": "registration_success",
            "client_id": client_id,
            "message": "Клиент успешно зарегистрирован",
            "server_info": {
                "version": "2.0.0",
                "features": ["commands", "file_ops", "monitoring", "encryption"],
                "max_command_timeout": 300,
                "max_output_size": "10MB"
            }
        }
        
        # Шифруем и отправляем ответ
        encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
        
        # Проверяем, что соединение еще активно
        try:
            await websocket.send_text(encrypted_response)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки ответа регистрации: {e}")
            return client_id  # Возвращаем client_id даже если не удалось отправить ответ
        
        logger.info(f"✅ Регистрация завершена для клиента {client_id}")
        return client_id
    
    async def _handle_auth(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка аутентификации с JWT токенами"""
        auth_data = message.get('data', {})
        auth_token = auth_data.get('auth_token', '')
        
        if not auth_token:
            response = {
                "type": "auth_failed",
                "message": "Токен аутентификации не предоставлен"
            }
            encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
            await websocket.send_text(encrypted_response)
            logger.warning(f"⚠️ Пустой токен аутентификации от клиента {client_id}")
            return
        
        # Проверяем JWT токен
        payload = self.auth_service.verify_token(auth_token)
        
        if payload:
            # Токен валиден
            token_client_id = payload.get('client_id')
            permissions = payload.get('permissions', [])
            
            # Сохраняем разрешения для клиента
            for permission in permissions:
                self.auth_service.add_permission(client_id, permission)
            
            logger.info(f"🔐 Аутентификация успешна для клиента {client_id} (разрешения: {permissions})")
            
            response = {
                "type": "auth_success",
                "message": "Аутентификация успешна",
                "permissions": permissions
            }
        else:
            # Токен невалиден
            logger.warning(f"⚠️ Невалидный токен от клиента {client_id}")
            response = {
                "type": "auth_failed",
                "message": "Невалидный или истекший токен аутентификации"
            }
        
        encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
        await websocket.send_text(encrypted_response)
    
    async def _handle_key_exchange(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка обмена ключами (игнорируем в PSK режиме)"""
        logger.info(f"🔑 Key exchange получен от {client_id}, PSK режим — игнорируем")
        
        response = {
            "type": "key_exchange_response",
            "message": "PSK режим активен, key exchange не требуется"
        }
        
        encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
        await websocket.send_text(encrypted_response)
    
    async def _stats_middleware(self, websocket: WebSocket, message: dict, client_id: str) -> dict:
        """Middleware для обновления статистики"""
        self.stats["total_messages"] += 1
        
        if message.get('type') in ['command_request', 'command_result']:
            self.stats["total_commands"] += 1
        
        return message
    
    async def _cleanup_client(self, client_id: str):
        """Очистка при отключении клиента"""
        try:
            # Отменяем все активные команды клиента
            active_commands = self.command_handler.get_active_commands()
            for cmd_id, cmd_info in active_commands.items():
                if cmd_info.get('client_id') == client_id:
                    await self.command_handler.handle_command_cancel(
                        None, 
                        {"data": {"command_id": cmd_id}}, 
                        client_id
                    )
            
            # Сбрасываем rate limit для клиента
            self.command_handler.rate_limiter.reset_client(client_id)
            
            # Отключаем клиента
            await self.client_manager.unregister_client(client_id)
            await self.websocket_manager.disconnect(client_id)
            
            # Очищаем данные шифрования
            self.encryption_service.cleanup_client(client_id)
            
            logger.info(f"🧹 Очистка завершена для клиента {client_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка очистки клиента {client_id}: {e}")
    
    # Публичные методы для внешнего использования
    
    async def send_command_to_client(self, client_id: str, command: str, command_id: str = None, timeout: int = 300) -> bool:
        """Отправка команды клиенту"""
        try:
            if not command_id:
                command_id = f"cmd_{int(time.time())}"
            
            command_msg = {
                "type": "command_request",
                "data": {
                    "command": command,
                    "command_id": command_id,
                    "timeout": timeout
                }
            }
            
            # Регистрируем команду в command_handler перед отправкой
            await self.command_handler.add_command(client_id, command_id, command, timeout)
            
            encrypted_msg = await self.encryption_service.encrypt_message(command_msg, client_id)
            success = await self.websocket_manager.send_message(client_id, encrypted_msg)
            
            if success:
                logger.info(f"📤 Команда отправлена клиенту {client_id}: {command}")
            else:
                # Если отправка не удалась, удаляем команду из активных
                await self.command_handler.remove_command(command_id)
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки команды: {e}")
            return False
    
    async def send_cancel_to_client(self, client_id: str, command_id: str) -> bool:
        """Отмена команды"""
        try:
            cancel_msg = {
                "type": "command_cancel",
                "data": {
                    "command_id": command_id
                }
            }
            
            encrypted_msg = await self.encryption_service.encrypt_message(cancel_msg, client_id)
            success = await self.websocket_manager.send_message(client_id, encrypted_msg)
            
            if success:
                logger.info(f"🚫 Команда {command_id} отменена для клиента {client_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Ошибка отмены команды: {e}")
            return False
    
    def get_client_info(self, client_id: str) -> Optional[ClientInfo]:
        """Получить информацию о клиенте"""
        return self.client_manager.get_client_info(client_id)
    
    def get_all_clients(self) -> Dict[str, ClientInfo]:
        """Получить всех клиентов"""
        return self.client_manager.get_all_clients()
    
    def get_server_stats(self) -> Dict[str, Any]:
        """Получить статистику сервера"""
        return {
            "server_stats": self.stats,
            "command_stats": self.command_handler.get_command_stats(),
            "client_count": self.websocket_manager.get_connection_count(),
            "uptime": time.time() - self.stats["uptime_start"]
        }
    
    async def broadcast_message(self, message: dict, exclude_clients: set = None):
        """Широковещательная отправка сообщения"""
        exclude_clients = exclude_clients or set()
        
        for client_id in self.client_manager.get_all_clients().keys():
            if client_id not in exclude_clients:
                try:
                    encrypted_msg = await self.encryption_service.encrypt_message(message, client_id)
                    await self.websocket_manager.send_message(client_id, encrypted_msg)
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки сообщения клиенту {client_id}: {e}")
    
    async def cleanup(self):
        """Очистка всех соединений"""
        await self.websocket_manager.cleanup()
        logger.info("🧹 Очистка сервера завершена")
