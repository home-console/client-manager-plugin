"""
Контекст WebSocket: сборка зависимостей и хендлеров.
Вынесено из websocket_handler.py, чтобы уменьшить размер файла и упростить инстанцирование.
"""

import logging
import time
from typing import Dict, Optional

from ..connection.websocket_manager import WebSocketManager
from ..security.encryption_service import EncryptionService
from ..security.auth_service import AuthService
from ..messaging.message_router import MessageRouter
from ..client_manager import ClientManager
from ..command_handler import CommandHandler
from ..models import ClientInfo
from ..transfers_manager import TransfersManager
from ..file_transfer_handler import FileTransferHandler
from ..websocket_handlers import AuthHandlers, RegistrationHandlers, TerminalHandlers, AdminHandlers
from ..services import AuditService, StatsService, FileOpsService
from ...config import settings
from ..enrollment_store import EnrollmentStore

logger = logging.getLogger(__name__)


class WebSocketContext:
    """Хранит все зависимости WebSocketHandler."""

    def __init__(self, handler: "WebSocketHandler"):
        # Базовые сервисы
        self.websocket_manager = WebSocketManager()
        self.encryption_service = EncryptionService()
        self.auth_service = AuthService()
        self.message_router = MessageRouter()
        self.client_manager = ClientManager()
        self.command_handler = CommandHandler(self.client_manager, self.websocket_manager)
        self.transfers = TransfersManager()
        self.file_handler = FileTransferHandler(self.transfers, self.websocket_manager, self.encryption_service)
        self.enrollments = EnrollmentStore()
        self._trusted_clients = set()

        # Фоновые/служебные сервисы
        self.audit_service = AuditService()
        self.stats_service = StatsService()

        # Файловые операции
        self.file_ops = FileOpsService(
            self.transfers,
            self.file_handler,
            self.websocket_manager,
            self.encryption_service,
            handler.send_command_to_client,
        )

        # Синхронизация секретов (handler пробрасывается после инициализации)
        from ..secrets_sync import SecretsSyncService

        self.secrets_sync = SecretsSyncService(self.encryption_service)
        self.secrets_sync.set_websocket_handler(handler)

        # Безопасный обмен ключами
        from ..security.key_exchange import KeyExchangeService

        self.key_exchange = KeyExchangeService()

        # Группы обработчиков WS
        self.registration_handlers = RegistrationHandlers(handler)
        self.auth_handlers = AuthHandlers(handler)
        self.terminal_handlers = TerminalHandlers(handler)
        self.admin_handlers = AdminHandlers(handler)

        # Инициализация политик файловых трансферов из конфига
        try:
            if getattr(settings, "file_allowed_base_dir", None):
                self.file_handler.set_allowed_base_dir(settings.file_allowed_base_dir)
            self.file_handler.set_limits(
                max_transfer_size=getattr(settings, "file_max_transfer_size", None),
                per_client_quota_bytes=getattr(settings, "file_per_client_quota_bytes", None),
            )
        except Exception as e:  # pragma: no cover - защитный лог
            logger.warning(f"Не удалось применить политики файловых трансферов из конфига: {e}")

    # Прокси полезных методов (чтобы при необходимости использовать контекст отдельно)
    def get_client_info(self, client_id: str) -> Optional[ClientInfo]:
        return self.client_manager.get_client_info(client_id)

    def get_all_clients(self) -> Dict[str, ClientInfo]:
        return self.client_manager.get_all_clients()


