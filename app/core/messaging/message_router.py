"""
Маршрутизатор сообщений
"""

import json
import logging
from typing import Dict, Callable, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class MessageRouter:
    """Маршрутизатор сообщений для обработки различных типов сообщений"""
    
    def __init__(self):
        self.handlers: Dict[str, Callable] = {}
        self.middleware: list[Callable] = []
    
    def register_handler(self, message_type: str, handler: Callable):
        """Регистрация обработчика для типа сообщения"""
        self.handlers[message_type] = handler
        logger.debug(f"Зарегистрирован обработчик для типа: {message_type}")
    
    def register_middleware(self, middleware: Callable):
        """Регистрация middleware для обработки сообщений"""
        self.middleware.append(middleware)
        logger.debug(f"Зарегистрирован middleware: {middleware.__name__}")
    
    async def route_message(self, websocket: WebSocket, message: dict, client_id: str) -> bool:
        """Маршрутизация сообщения к соответствующему обработчику"""
        try:
            # Применяем middleware
            for middleware in self.middleware:
                message = await middleware(websocket, message, client_id)
                if message is None:
                    logger.debug(f"Сообщение отфильтровано middleware: {middleware.__name__}")
                    return True
            
            # Определяем тип сообщения
            message_type = message.get('type', 'unknown')
            
            # Ищем обработчик
            if message_type in self.handlers:
                handler = self.handlers[message_type]
                await handler(websocket, message, client_id)
                logger.debug(f"Сообщение {message_type} обработано")
                return True
            else:
                logger.debug(f"Неизвестный тип сообщения: {message_type}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка маршрутизации сообщения: {e}")
            return False
    
    def get_registered_types(self) -> list[str]:
        """Получить список зарегистрированных типов сообщений"""
        return list(self.handlers.keys())
    
    def get_middleware_count(self) -> int:
        """Получить количество зарегистрированных middleware"""
        return len(self.middleware)


class MessageHandler:
    """Базовый класс для обработчиков сообщений"""
    
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"{__name__}.{name}")
    
    async def handle(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка сообщения (должен быть переопределен в наследниках)"""
        raise NotImplementedError("Метод handle должен быть переопределен")
    
    def log_message(self, message_type: str, client_id: str, action: str):
        """Логирование сообщения"""
        # Уменьшаем шум: heartbeat пишем на debug
        if message_type == "heartbeat":
            self.logger.debug(f"📨 {action} {message_type} от клиента {client_id}")
        else:
            self.logger.info(f"📨 {action} {message_type} от клиента {client_id}")


class RegistrationHandler(MessageHandler):
    """Обработчик регистрации клиентов"""
    
    def __init__(self, client_manager, encryption_service, runtime=None):
        super().__init__("RegistrationHandler")
        self.client_manager = client_manager
        self.encryption_service = encryption_service
        self.runtime = runtime  # Для валидации enrollment_token
    
    async def handle(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка регистрации клиента"""
        self.log_message("register", client_id, "Обработка регистрации")
        
        # Получаем данные регистрации
        data = message.get('data', {})
        enrollment_token = data.get('enrollment_token')
        
        # Валидируем enrollment_token если требуется
        agent_name = None
        if enrollment_token:
            try:
                # Получаем agent_manager из runtime
                if not self.runtime or not hasattr(self.runtime, 'agent_manager'):
                    self.logger.error("❌ Runtime or agent_manager not available for enrollment validation")
                    response = {
                        "type": "registration_error",
                        "error": "Server configuration error: enrollment validation unavailable"
                    }
                    encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
                    await websocket.send_text(encrypted_response)
                    return
                
                # Валидируем token и получаем agent_name
                agent_name = await self.runtime.agent_manager.validate_enrollment_token(enrollment_token)
                self.logger.info(f"✅ Enrollment token validated for agent: {agent_name}")
                
            except ValueError as e:
                self.logger.warning(f"❌ Enrollment token validation failed: {e}")
                response = {
                    "type": "registration_error",
                    "error": f"Invalid enrollment token: {str(e)}"
                }
                encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
                await websocket.send_text(encrypted_response)
                return
            except Exception as e:
                self.logger.error(f"❌ Enrollment token validation error: {e}")
                response = {
                    "type": "registration_error",
                    "error": "Enrollment validation error"
                }
                encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
                await websocket.send_text(encrypted_response)
                return
        
        # Регистрируем клиента
        new_client_id = await self.client_manager.register_client(websocket, data)
        
        # Сбрасываем состояние шифрования при переподключении
        if new_client_id in self.encryption_service.encryption_states:
            self.encryption_service.reset_encryption_state(new_client_id)
        
        # Отправляем подтверждение
        response = {
            "type": "registration_success",
            "client_id": new_client_id,
            "message": "Клиент успешно зарегистрирован"
        }
        
        # Шифруем и отправляем ответ
        encrypted_response = await self.encryption_service.encrypt_message(response, new_client_id)
        await websocket.send_text(encrypted_response)
        
        self.logger.info(f"✅ Регистрация завершена для клиента {new_client_id}" + 
                       (f" (enrolled as {agent_name})" if agent_name else ""))


class HeartbeatHandler(MessageHandler):
    """Обработчик heartbeat сообщений"""

    def __init__(self, client_manager, encryption_service, runtime=None):
        super().__init__("HeartbeatHandler")
        self.client_manager = client_manager
        self.encryption_service = encryption_service
        self.runtime = runtime  # Для интеграции с DeploymentTracker

    async def handle(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка heartbeat"""
        self.log_message("heartbeat", client_id, "Обработка heartbeat")

        # Обновляем время последнего heartbeat
        self.client_manager.update_heartbeat(client_id)

        # НОВОЕ: Интегрируем с DeploymentTracker для завершения deployment
        if self.runtime and hasattr(self.runtime, 'deployment_tracker') and self.runtime.deployment_tracker:
            try:
                from datetime import datetime, timezone
                # Получаем pending deployments в статусе "registering"
                pending = await self.runtime.deployment_tracker.list_deployments(
                    status="registering",
                    limit=100
                )
                
                # Ищем deployment для этого agent_id
                for deployment in pending:
                    if deployment.agent_id == client_id:
                        # Отмечаем deployment как READY
                        await self.runtime.deployment_tracker.update_status(
                            deployment.deployment_id,
                            status="ready",
                            progress=100,
                            completed_at=datetime.now(timezone.utc).isoformat()
                        )
                        self.log_message("heartbeat", client_id, 
                            f"✅ Deployment {deployment.deployment_id} отмечен как READY")
            except Exception as e:
                self.logger.debug(f"[HeartbeatHandler] Error updating deployment: {e}")

        # Отправляем подтверждение
        response = {
            "type": "heartbeat_ack",
            "timestamp": message.get('data', {}).get('timestamp')
        }

        # Шифруем ответ
        encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
        await websocket.send_text(encrypted_response)


class CommandResultHandler(MessageHandler):
    """Обработчик результатов команд"""
    
    def __init__(self, command_handler):
        super().__init__("CommandResultHandler")
        self.command_handler = command_handler
    
    async def handle(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка результата команды"""
        self.log_message("command_result", client_id, "Обработка результата команды")
        
        # Делегируем обработку в command_handler
        await self.command_handler.handle_client_message(websocket, message, client_id)

