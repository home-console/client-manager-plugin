"""
Единый WebSocket обработчик с улучшенной архитектурой
Объединяет лучшие практики из всех предыдущих версий
"""

import logging
import time
from typing import Optional, Dict, Any

from fastapi import WebSocket

from .ws.context import WebSocketContext
from .ws.registry import register_handlers, register_middleware
from .ws.connection_loop import handle_connection
from .models import ClientInfo

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """WebSocket обработчик для управления клиентами"""
    
    def __init__(self):
        # Сессии терминалов: session_id -> {"agent_id": str, "frontend_ws": WebSocket | None}
        self.terminal_sessions: Dict[str, dict] = {}

        # Собираем контекст (зависимости)
        ctx = WebSocketContext(self)
        self.ctx = ctx
        
        # Runtime (инициализируется позже через set_runtime())
        self.runtime = None

        # Пробрасываем основные зависимости для совместимости
        self.websocket_manager = ctx.websocket_manager
        self.encryption_service = ctx.encryption_service
        self.auth_service = ctx.auth_service
        self.message_router = ctx.message_router
        self.client_manager = ctx.client_manager
        self.command_handler = ctx.command_handler
        self.transfers = ctx.transfers
        self.file_handler = ctx.file_handler
        self.enrollments = ctx.enrollments
        self._trusted_clients = ctx._trusted_clients
        self.audit_service = ctx.audit_service
        self.stats_service = ctx.stats_service
        self.file_ops = ctx.file_ops
        self.secrets_sync = ctx.secrets_sync
        self.key_exchange = ctx.key_exchange
        self.registration_handlers = ctx.registration_handlers
        self.auth_handlers = ctx.auth_handlers
        self.terminal_handlers = ctx.terminal_handlers
        self.admin_handlers = ctx.admin_handlers
        
        # Регистрация обработчиков сообщений и middleware вынесена в registry.py
        register_handlers(self)
        register_middleware(self)
        # Статистика хранится в stats_service
    
    async def handle_websocket(self, websocket: WebSocket):
        """Обработка WebSocket соединения"""
        await handle_connection(self, websocket)
    
    async def _handle_registration(self, websocket: WebSocket, message: dict, skip_secrets_send: bool = False, pre_validated_agent_name: str | None = None) -> str:
        return await self.registration_handlers.handle_registration(websocket, message, skip_secrets_send=skip_secrets_send, pre_validated_agent_name=pre_validated_agent_name)

    async def send_enrollment_result(self, client_id: str, status: str = "approved") -> None:
        """Отправить клиенту результат утверждения (approved/rejected)."""
        await self.registration_handlers.send_enrollment_result(client_id, status=status)
    
    async def _handle_request_secrets(self, websocket: WebSocket, message: dict, client_id: str):
        return await self.registration_handlers.handle_request_secrets(websocket, message, client_id)

    async def _send_ws_challenge(self, websocket: WebSocket, client_id: str):
        """Отправка однократного WS challenge с nonce и timestamp (мягкий режим)."""
        await self.auth_handlers.send_ws_challenge(websocket, client_id)

    async def _handle_auth_challenge_response(self, websocket: WebSocket, message: dict, client_id: str):
        """Проверка ответа на WS challenge. Мягкий режим: при неуспехе не разрываем соединение."""
        await self.auth_handlers.handle_auth_challenge_response(websocket, message, client_id)
    
    async def _handle_auth(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка аутентификации с JWT токенами"""
        await self.auth_handlers.handle_auth(websocket, message, client_id)
    
    async def _handle_key_exchange(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка обмена ключами (игнорируем в PSK режиме)"""
        await self.auth_handlers.handle_key_exchange(websocket, message, client_id)
    
    async def _handle_tls_downgrade_request(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка запроса на TLS downgrade от клиента"""
        await self.auth_handlers.handle_tls_downgrade_request(websocket, message, client_id)

    # --- Terminal helpers (PoC) ---
    async def register_terminal_session(self, session_id: str, agent_id: str, initiator: dict = None):
        """Register a new terminal session mapping to an agent."""
        await self.terminal_handlers.register_terminal_session(session_id, agent_id, initiator=initiator)

    async def attach_frontend_to_session(self, session_id: str, websocket: WebSocket):
        return await self.terminal_handlers.attach_frontend_to_session(session_id, websocket)

    async def detach_session(self, session_id: str):
        await self.terminal_handlers.detach_session(session_id)

    async def send_input_to_agent(self, session_id: str, b64_payload: str) -> bool:
        """Send terminal input to agent (encrypted message)."""
        return await self.terminal_handlers.send_input_to_agent(session_id, b64_payload)


    async def _handle_terminal_output(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle terminal output coming from agent and forward to frontend websocket."""
        await self.terminal_handlers.handle_terminal_output(websocket, message, client_id)

    async def _handle_terminal_started(self, websocket: WebSocket, message: dict, client_id: str):
        await self.terminal_handlers.handle_terminal_started(websocket, message, client_id)

    async def _handle_terminal_stopped(self, websocket: WebSocket, message: dict, client_id: str):
        await self.terminal_handlers.handle_terminal_stopped(websocket, message, client_id)

    async def _handle_terminal_input_error(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle error reports from agent about terminal input (e.g. unknown session).

        Expects message.data = {"session_id": "...", "error": "reason"}
        Forwards a control message to the frontend attached to that session (if any) and detaches the session mapping.
        """
        await self.terminal_handlers.handle_terminal_input_error(websocket, message, client_id)

    async def _handle_admin_install(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle admin.install_service messages forwarded from core.

        Expected message.data: { install_token, dry_run, socket, sessions_dir, token_file }
        This method will call existing installers (SSH installer) if appropriate.
        """
        await self.admin_handlers.handle_admin_install(websocket, message, client_id)

    async def _handle_admin_install_plugin(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle admin.install_plugin messages.

        Expects message.data: { plugin_name, version, manifest, artifact_url, type, options, install_job_id }
        For type == 'docker' we will attempt to `docker pull` the image and `docker run` it.
        This runs in background and will POST progress callbacks to core admin callback endpoint.
        """
        await self.admin_handlers.handle_admin_install_plugin(websocket, message, client_id)
    
    async def _stats_middleware(self, websocket: WebSocket, message: dict, client_id: str) -> dict:
        """Middleware для обновления статистики"""
        return self.stats_service.record_message(message)

    async def _maybe_upload_recording(self, record_path: str, session_id: str):
        """Upload recording file to S3/MinIO if configured. Runs in background."""
        await self.terminal_handlers._maybe_upload_recording(record_path, session_id)
    
    def set_runtime(self, runtime):
        """Установить runtime для доступа к deployment_tracker и другим компонентам."""
        self.runtime = runtime
        logger.info("✅ Runtime установлен в WebSocketHandler")
    
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
        return self.stats_service.snapshot(
            self.command_handler.get_command_stats(),
            self.websocket_manager.get_connection_count(),
            self._collect_transfer_stats(),
        )

    def _collect_transfer_stats(self) -> Dict[str, Any]:
        transfers = getattr(self, "transfers", None)
        summary = {
            "active": 0,
            "paused": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "bytes_received": 0,
            "total": 0
        }
        if not transfers:
            return summary
        for tid, t in transfers.transfers.items():
            state = str(t.get("state"))
            summary["total"] += 1
            if state == "in_progress":
                summary["active"] += 1
            elif state == "paused":
                summary["paused"] += 1
            elif state == "completed":
                summary["completed"] += 1
            elif state == "failed":
                summary["failed"] += 1
            elif state == "cancelled":
                summary["cancelled"] += 1
            summary["bytes_received"] += int(t.get("bytes_received", 0))
        return summary

    async def start_background_tasks(self):
        """Запуск фоновых задач: монитор доступности core и флешер очереди аудита."""
        await self.audit_service.start_background_tasks()
    
    async def download_file_from_device(self, device_id: str, remote_path: str) -> bytes:
        """Скачивание файла с устройства через агента через WebSocket"""
        return await self.file_ops.download_file_from_device(device_id, remote_path)

    async def upload_file_to_device(self, device_id: str, local_path: str, file_data: bytes):
        """Загрузка файла на устройство через агента через WebSocket"""
        return await self.file_ops.upload_file_to_device(device_id, local_path, file_data)

    async def delete_file_on_device(self, device_id: str, remote_path: str):
        """Удаление файла на устройстве"""
        return await self.file_ops.delete_file_on_device(device_id, remote_path)

    async def list_files_on_device(self, device_id: str, remote_path: str, recursive: bool = False) -> list:
        """Получение списка файлов на устройстве"""
        return await self.file_ops.list_files_on_device(device_id, remote_path, recursive)

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
