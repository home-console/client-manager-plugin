"""
Регистрация хендлеров и middleware для WebSocket.
Вынесено из websocket_handler.py, чтобы разгрузить основной файл.
"""

import logging

logger = logging.getLogger(__name__)


def register_handlers(handler: "WebSocketHandler"):
    """Регистрация обработчиков сообщений."""
    from ..messaging.message_router import RegistrationHandler, HeartbeatHandler

    # Обработчик регистрации
    registration_handler = RegistrationHandler(
        handler.client_manager,
        handler.encryption_service,
    )
    handler.message_router.register_handler("register", registration_handler.handle)

    # Обработчик heartbeat
    heartbeat_handler = HeartbeatHandler(handler.client_manager, handler.encryption_service)
    handler.message_router.register_handler("heartbeat", heartbeat_handler.handle)

    # Обработчики команд
    handler.message_router.register_handler("command_request", handler.command_handler.handle_command_request)
    handler.message_router.register_handler("command_result", handler.command_handler.handle_command_result)
    handler.message_router.register_handler("result_chunk", handler.command_handler.handle_result_chunk)
    handler.message_router.register_handler("result_eof", handler.command_handler.handle_result_eof)
    handler.message_router.register_handler("command_cancel", handler.command_handler.handle_command_cancel)
    handler.message_router.register_handler("command_cancel_ack", handler.command_handler.handle_command_cancel_ack)

    # Admin -> client actions
    handler.message_router.register_handler("admin.install_service", handler._handle_admin_install)
    handler.message_router.register_handler("admin.install_plugin", handler._handle_admin_install_plugin)

    # Файловые трансферы
    handler.message_router.register_handler("file_chunk", handler.file_handler.handle_file_chunk)
    handler.message_router.register_handler("file_eof", handler.file_handler.handle_file_eof)

    # Аутентификация
    handler.message_router.register_handler("auth", handler._handle_auth)
    handler.message_router.register_handler("auth_challenge_response", handler._handle_auth_challenge_response)

    # Key exchange / секреты
    handler.message_router.register_handler("key_exchange", handler._handle_key_exchange)
    handler.message_router.register_handler("request_secrets", handler._handle_request_secrets)

    # Терминал
    handler.message_router.register_handler("terminal.output", handler._handle_terminal_output)
    handler.message_router.register_handler("terminal.started", handler._handle_terminal_started)
    handler.message_router.register_handler("terminal.stopped", handler._handle_terminal_stopped)
    handler.message_router.register_handler("terminal.input.error", handler._handle_terminal_input_error)

    # TLS downgrade
    handler.message_router.register_handler("tls_downgrade_request", handler._handle_tls_downgrade_request)

    logger.info("✅ Все обработчики сообщений зарегистрированы")


def register_middleware(handler: "WebSocketHandler"):
    """Регистрация middleware."""
    handler.message_router.register_middleware(handler._stats_middleware)
    logger.info("✅ Все middleware зарегистрированы")


