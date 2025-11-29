"""
Единый WebSocket обработчик с улучшенной архитектурой
Объединяет лучшие практики из всех предыдущих версий
"""

import asyncio
import logging
import time
# import os
import secrets
import base64
import hmac as _hmac
# import hashlib
from typing import Optional, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

from .connection.websocket_manager import WebSocketManager
from .security.encryption_service import EncryptionService
# from .security.auth_service import AuthService
from .messaging.message_router import MessageRouter
from .client_manager import ClientManager
from .command_handler import CommandHandler
from .models import ClientInfo
from .transfers_manager import TransfersManager
from .file_transfer_handler import FileTransferHandler
from ..utils.encryption import compute_hmac
from ..config import settings
from .enrollment_store import EnrollmentStore

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """WebSocket обработчик для управления клиентами"""
    
    def __init__(self):
        # Инициализация модулей
        self.websocket_manager = WebSocketManager()
        self.encryption_service = EncryptionService()
        # self.auth_service = AuthService()
        self.message_router = MessageRouter()
        self.client_manager = ClientManager()
        self.command_handler = CommandHandler(self.client_manager, self.websocket_manager)
        self.transfers = TransfersManager()
        self.file_handler = FileTransferHandler(self.transfers, self.websocket_manager, self.encryption_service)
        # TOFU enrollment store
        self.enrollments = EnrollmentStore()
        self._trusted_clients = set()
        # Сессии терминалов: session_id -> {"agent_id": str, "frontend_ws": WebSocket | None}
        self.terminal_sessions: Dict[str, dict] = {}
        
        # Синхронизация секретов (websocket_handler передается после инициализации)
        from .secrets_sync import SecretsSyncService
        self.secrets_sync = SecretsSyncService(self.encryption_service)
        self.secrets_sync.set_websocket_handler(self)
        
        # Безопасный обмен ключами для первоначальной синхронизации
        from .security.key_exchange import KeyExchangeService
        self.key_exchange = KeyExchangeService()
        # Лимиты WS
        self.ws_max_message_bytes = getattr(settings, "websocket_max_message_bytes", 1024 * 1024)
        self.ws_max_messages_per_minute = getattr(settings, "websocket_max_messages_per_minute", 600)
        self._msg_counters: Dict[str, Dict[str, float]] = {}
        # Инициализация политик файловых трансферов из конфига
        try:
            if getattr(settings, "file_allowed_base_dir", None):
                self.file_handler.set_allowed_base_dir(settings.file_allowed_base_dir)
            self.file_handler.set_limits(
                max_transfer_size=getattr(settings, "file_max_transfer_size", None),
                per_client_quota_bytes=getattr(settings, "file_per_client_quota_bytes", None),
            )
        except Exception as e:
            logger.warning(f"Не удалось применить политики файловых трансферов из конфига: {e}")
        
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
        heartbeat_handler = HeartbeatHandler(self.client_manager, self.encryption_service)
        self.message_router.register_handler('heartbeat', heartbeat_handler.handle)
        
        # Обработчики команд
        self.message_router.register_handler('command_request', self.command_handler.handle_command_request)
        self.message_router.register_handler('command_result', self.command_handler.handle_command_result)
        self.message_router.register_handler('result_chunk', self.command_handler.handle_result_chunk)
        self.message_router.register_handler('result_eof', self.command_handler.handle_result_eof)
        self.message_router.register_handler('command_cancel', self.command_handler.handle_command_cancel)
        self.message_router.register_handler('command_cancel_ack', self.command_handler.handle_command_cancel_ack)

        # Обработчики файловых трансферов (WS)
        self.message_router.register_handler('file_chunk', self.file_handler.handle_file_chunk)
        self.message_router.register_handler('file_eof', self.file_handler.handle_file_eof)
        
        # Обработчик аутентификации
        self.message_router.register_handler('auth', self._handle_auth)

        # Обработчик ответа на WS challenge (мягкий режим)
        self.message_router.register_handler('auth_challenge_response', self._handle_auth_challenge_response)
        
        # Обработчик key_exchange для безопасной передачи секретов
        self.message_router.register_handler('key_exchange', self._handle_key_exchange)
        # Обработчик запроса секретов с публичным ключом
        self.message_router.register_handler('request_secrets', self._handle_request_secrets)
        # Terminal handlers (PoC)
        self.message_router.register_handler('terminal.output', self._handle_terminal_output)
        self.message_router.register_handler('terminal.started', self._handle_terminal_started)
        self.message_router.register_handler('terminal.stopped', self._handle_terminal_stopped)
        
        # Обработчик запроса TLS downgrade
        self.message_router.register_handler('tls_downgrade_request', self._handle_tls_downgrade_request)
        
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
            
            # НЕ отправляем WS challenge сразу - сначала ждем регистрацию
            # Если регистрация не удастся из-за HMAC mismatch, отправим secrets_config вместо challenge
            
            # Основной цикл обработки сообщений
            while True:
                # Получение сообщения
                data = await websocket.receive_text()
                logger.debug(f"📥 Получено сообщение от {client_id}: {len(data)} байт")
                # Проверка размера сообщения
                if self.ws_max_message_bytes and len(data.encode('utf-8')) > self.ws_max_message_bytes:
                    logger.warning(f"🔒 Закрытие {client_id}: превышен размер сообщения")
                    try:
                        await websocket.close(code=1009, reason="Message too big")
                    finally:
                        break
                # Проверка частоты сообщений (скользящее окно 60с)
                now = time.time()
                ctr = self._msg_counters.get(client_id) or {"count": 0.0, "window_start": now}
                if now - ctr["window_start"] >= 60:
                    ctr = {"count": 0.0, "window_start": now}
                ctr["count"] += 1
                self._msg_counters[client_id] = ctr
                if self.ws_max_messages_per_minute and ctr["count"] > float(self.ws_max_messages_per_minute):
                    logger.warning(f"🔒 Закрытие {client_id}: превышен лимит сообщений в минуту")
                    try:
                        await websocket.close(code=1011, reason="Rate limit exceeded")
                    finally:
                        break
                
                # Дешифрование и маршрутизация
                # ПЕРВОЕ: проверяем незашифрованные сообщения для безопасного обмена ключами
                try:
                    # Пробуем распарсить как JSON (возможно незашифрованное сообщение)
                    import json
                    try_json = json.loads(data)
                    
                    # Обработка незашифрованного request_secrets
                    if isinstance(try_json, dict) and try_json.get("type") == "request_secrets":
                        # Это безопасный запрос секретов - обрабатываем без дешифрования
                        logger.info(f"🔐 Получен безопасный запрос секретов от {client_id}")
                        await self._handle_request_secrets(websocket, try_json, client_id)
                        break  # Соединение закроется после отправки секретов
                    
                    # Обработка незашифрованного register (для первоначальной синхронизации)
                    elif isinstance(try_json, dict) and try_json.get("type") == "register":
                        # Незашифрованная регистрация - это нормально для первоначальной синхронизации
                        logger.info(f"📝 Получена незашифрованная регистрация от {client_id} (первоначальная синхронизация)")
                        # Обрабатываем регистрацию напрямую (TOFU: pending approval)
                        new_client_id = await self._handle_registration(websocket, try_json, skip_secrets_send=True)
                        
                        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Обновляем client_id СРАЗУ после регистрации
                        if new_client_id and new_client_id != "unknown":
                            await self.websocket_manager.update_client_id(client_id, new_client_id)
                            client_id = new_client_id
                            self.websocket_manager.update_metadata(client_id, {
                                'registered': True,
                                'registered_at': time.time()
                            })
                            
                            # Если клиент ещё не доверен — кладем заявку и отправляем pending
                            if not self.enrollments.is_trusted(client_id):
                                try:
                                    data = try_json.get("data", {})
                                    self.enrollments.add_pending(client_id, data)
                                    import os, json as _json
                                    auto_approve = str(os.getenv("AUTO_APPROVE_ENROLLMENTS", "true")).lower() in ("1", "true", "yes")
                                    logger.info(f"🔍 AUTO_APPROVE_ENROLLMENTS={auto_approve} для клиента {client_id}")
                                    if auto_approve:
                                        # Автоаппрув: помечаем доверенным и шлём понятные клиенту сообщения
                                        self.enrollments.approve(client_id)
                                        logger.info(f"✅ Клиент {client_id} автоматически утвержден")
                                        await websocket.send_text(_json.dumps({
                                            "type": "registration_success",
                                            "client_id": client_id,
                                            "message": "Клиент успешно зарегистрирован (auto-approve)",
                                            "secrets_version": self.secrets_sync.get_version(),
                                        }))
                                        await websocket.send_text(_json.dumps({
                                            "type": "secrets_needed",
                                            "message": "Отправьте 'request_secrets' с вашим публичным RSA ключом для получения секретов"
                                        }))
                                    else:
                                        logger.info(f"⏳ Клиент {client_id} ожидает утверждения")
                                        await websocket.send_text(_json.dumps({
                                            "type": "enrollment_pending",
                                            "data": {"client_id": client_id}
                                        }))
                                except Exception as e:
                                    logger.error(f"❌ Ошибка обработки enrollment для {client_id}: {e}")
                                    pass
                            
                            # Запуск мониторинга соединения:
                            # - не запускаем, если WS_MONITOR_DISABLE=true
                            # - не запускаем во время TOFU/plaintext регистрации (skip_secrets_send=True)
                            import os, asyncio as _asyncio
                            ws_mon_disable = str(os.getenv("WS_MONITOR_DISABLE", "true")).lower() in ("1", "true", "yes")
                            if not ws_mon_disable and self.enrollments.is_trusted(client_id):
                                # Небольшая задержка, чтобы клиент успел обработать registration_success
                                await _asyncio.sleep(0.5)
                                await self.websocket_manager.start_monitoring(client_id)
                            # иначе мониторинг запустим позже, когда клиент станет trusted/зашифрован
                        
                            # Дальнейшие действия зависят от approve — ждём решения админа
                            continue
                        else:
                            logger.warning(f"⚠️ Регистрация не удалась для {client_id}")
                            break
                except (json.JSONDecodeError, ValueError, KeyError):
                    # Не JSON или не ожидаемый тип - продолжаем обычную обработку
                    pass
                
                # ОБЫЧНАЯ ОБРАБОТКА: дешифрование и маршрутизация
                try:
                    message = await self.encryption_service.decrypt_message(data, client_id)
                except Exception as sec_err:
                    # БЕЗОПАСНАЯ ОБРАБОТКА первого подключения с неверными ключами
                    # Вместо незашифрованной отправки - ожидаем запрос с публичным ключом
                    error_msg = str(sec_err)
                    is_hmac_error = "HMAC mismatch" in error_msg or "Unencrypted WebSocket message" in error_msg
                    
                    if client_id == "unknown" and is_hmac_error:
                        logger.warning(f"⚠️ HMAC mismatch при первом подключении - клиент использует неверные ключи")
                        logger.info(f"🔐 Ожидаем запрос секретов с публичным ключом для безопасной передачи")
                        
                        # Отправляем сообщение с инструкцией запросить секреты через request_secrets
                        error_response = {
                            "type": "secrets_needed",
                            "message": "Требуется синхронизация секретов. Отправьте незашифрованное сообщение 'request_secrets' с вашим публичным RSA ключом (PEM формат) в поле 'data.public_key'."
                        }
                        try:
                            import json
                            await websocket.send_text(json.dumps(error_response))
                            await asyncio.sleep(0.1)
                        except Exception:
                            pass
                        
                        # НЕ закрываем соединение сразу - даем возможность клиенту запросить секреты
                        # Соединение закроется автоматически если клиент не отправит запрос
                        continue
                    else:
                        # Немедленно закрываем соединение при других ошибках безопасности
                        reason = f"Security error: {sec_err}"
                        logger.warning(f"🔒 Отклонено сообщение и закрыто соединение {client_id}: {reason}")
                        try:
                            await websocket.close(code=1008, reason="Policy violation: invalid encryption/HMAC")
                        except:
                            pass
                        break
                logger.debug(f"📥 Обработанное сообщение: {message}")
                
                    # Обработка регистрации (особый случай)
                if message.get('type') == 'register':
                    # Обновляем client_id после регистрации
                    old_client_id = client_id
                    new_client_id = await self._handle_registration(websocket, message)
                    if new_client_id != "unknown":
                        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ P0-5: Переносим sequence numbers из unknown в зарегистрированного клиента
                        if old_client_id == "unknown":
                            # Переносим состояние sequence numbers из unknown в нового клиента
                            self.encryption_service.migrate_unknown_to_registered(old_client_id, new_client_id)
                        
                        # Обновляем client_id в WebSocketManager
                        await self.websocket_manager.update_client_id(old_client_id, new_client_id)
                        client_id = new_client_id
                        
                        # Обновляем метаданные соединения
                        self.websocket_manager.update_metadata(client_id, {
                            'registered': True,
                            'registered_at': time.time()
                        })
                        
                        # ИСПРАВЛЕНИЕ: НЕ запускаем мониторинг здесь!
                        # Мониторинг запускается внутри _handle_registration ПОСЛЕ отправки registration_success
                else:
                    # Маршрутизация остальных сообщений
                    await self.message_router.route_message(websocket, message, client_id)
                
        except WebSocketDisconnect:
            logger.info(f"🔌 WebSocket отключен: {client_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка WebSocket: {e}", exc_info=True)
        finally:
            if client_id != "unknown":
                # Помечаем все трансферы клиента на паузу при отключении (только если есть активные)
                try:
                    paused_count = await self.transfers.pause_all_for_client(client_id)
                    # Логируем только если действительно что-то приостановили
                    if paused_count > 0:
                        logger.debug(f"⏸️ Приостановлено активных трансферов: {paused_count} для клиента {client_id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка паузы трансферов для {client_id}: {e}")
                await self._cleanup_client(client_id)
            self.stats["active_connections"] -= 1
    
    async def _handle_registration(self, websocket: WebSocket, message: dict, skip_secrets_send: bool = False) -> str:
        """
        Обработка регистрации клиента
        
        Args:
            websocket: WebSocket соединение
            message: Сообщение регистрации
            skip_secrets_send: Если True, не отправляет секреты (для первоначальной синхронизации через RSA)
        """
        # Регистрируем клиента
        client_id = await self.client_manager.register_client(websocket, message.get('data', {}))
        
        # Сбрасываем состояние шифрования при переподключении
        if client_id in self.encryption_service.encryption_states:
            self.encryption_service.reset_encryption_state(client_id)
        
        # Проверяем версию секретов клиента
        client_data = message.get('data', {})
        client_secrets_version = client_data.get('secrets_version', 0)
        server_secrets_version = self.secrets_sync.get_version()
        
        logger.info(f"📋 Регистрация клиента {client_id}: версия секретов клиента={client_secrets_version}, сервера={server_secrets_version}")
        
        # Проверяем статус доверия ДО отправки секретов
        is_trusted = self.enrollments.is_trusted(client_id)
        
        # Автоматическое утверждение для зашифрованных регистраций
        if not is_trusted:
            import os
            auto_approve = str(os.getenv("AUTO_APPROVE_ENROLLMENTS", "false")).lower() in ("1", "true", "yes")
            if auto_approve:
                self.enrollments.approve(client_id)
                is_trusted = True
                logger.info(f"✅ Автоматическое утверждение для клиента {client_id}")
        
        # Если версия устарела или отсутствует - отправляем секреты (только если не пропущено и уже trusted)
        if not skip_secrets_send and client_secrets_version < server_secrets_version and is_trusted:
            logger.info(f"🔄 Клиент {client_id} имеет устаревшую версию секретов, отправляем обновление")
            try:
                await self.secrets_sync.send_secrets_to_client(client_id)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки секретов клиенту {client_id}: {e}")
        
        # Отправляем подтверждение с информацией о версии секретов (только если не пропущено)
        if not skip_secrets_send:
            response = {
                "type": "registration_success",
                "client_id": client_id,
                "message": "Клиент зарегистрирован" if is_trusted else "Клиент зарегистрирован (ожидает утверждения)",
                "server_info": {
                    "version": "2.0.0",
                    "features": ["commands", "file_ops", "monitoring", "encryption", "secrets_sync"],
                    "max_command_timeout": 300,
                    "max_output_size": "10MB"
                },
                "secrets_version": server_secrets_version,  # Информируем клиента о версии
                "trusted": is_trusted  # Сообщаем клиенту его статус
            }
            
            # Шифруем и отправляем ответ
            encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
            
            # Проверяем, что соединение еще активно
            try:
                await websocket.send_text(encrypted_response)
                logger.info(f"✅ Регистрация завершена для клиента {client_id} (trusted={is_trusted})")
                # После успешной регистрации (зашифрованной) запустим мониторинг, если разрешено
                import os, asyncio as _asyncio
                ws_mon_disable = str(os.getenv("WS_MONITOR_DISABLE", "true")).lower() in ("1", "true", "yes")
                if not ws_mon_disable and is_trusted:
                    await _asyncio.sleep(0.5)
                    await self.websocket_manager.start_monitoring(client_id)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки ответа регистрации: {e}")
                return client_id  # Возвращаем client_id даже если не удалось отправить ответ
        else:
            # Если skip_secrets_send=True, работаем по TOFU: ожидаем approve администратора
            logger.info(f"✅ Регистрация завершена для клиента {client_id} (TOFU pending)")
        
        return client_id

    async def send_enrollment_result(self, client_id: str, status: str = "approved") -> None:
        """Отправить клиенту результат утверждения (approved/rejected)."""
        import json
        import os
        pinned_spkis_env = os.getenv("SERVER_PINNED_SPKIS", "").strip()
        pinned_list = [s.strip() for s in pinned_spkis_env.split(",") if s.strip()] if pinned_spkis_env else []
        message = {
            "type": "enrollment_result",
            "data": {
                "status": status,
                "client_id": client_id,
                "pinned_spkis": pinned_list,
            },
        }
        # Помечаем доверенным при approve
        if status == "approved":
            self._trusted_clients.add(client_id)
        # Отправляем как plaintext (первое соединение ещё без шифрования)
        ws = self.client_manager.get_client(client_id)
        if ws:
            await ws.send_text(json.dumps(message))
        # Если approved — можно предложить клиенту запросить секреты
        if status == "approved":
            try:
                ws = self.client_manager.get_client(client_id)
                if ws:
                    await ws.send_text(json.dumps({
                        "type": "secrets_needed",
                        "message": "Отправьте 'request_secrets' с вашим публичным RSA ключом для получения секретов"
                    }))
            except Exception:
                pass
    
    async def _handle_request_secrets(self, websocket: WebSocket, message: dict, client_id: str):
        """
        БЕЗОПАСНЫЙ обмен секретами с использованием RSA шифрования
        
        Клиент отправляет свой публичный RSA ключ, сервер шифрует секреты этим ключом.
        """
        try:
            data = message.get('data', {})
            public_key_pem = data.get('public_key')
            
            if not public_key_pem:
                error_response = {
                    "type": "secrets_error",
                    "error": "Публичный ключ не предоставлен",
                    "message": "Отправьте публичный RSA ключ в формате PEM в поле 'public_key'"
                }
                import json
                await websocket.send_text(json.dumps(error_response))
                return
            
            # Регистрируем публичный ключ клиента
            if not self.key_exchange.register_client_public_key(client_id, public_key_pem):
                error_response = {
                    "type": "secrets_error",
                    "error": "Неверный формат публичного ключа",
                    "message": "Публичный ключ должен быть в формате PEM (RSA)"
                }
                import json
                await websocket.send_text(json.dumps(error_response))
                return
            
            # Получаем текущие секреты
            secrets = self.secrets_sync.get_current_secrets()
            if not secrets.get('encryption_key') or not secrets.get('encryption_salt'):
                error_response = {
                    "type": "secrets_error",
                    "error": "Секреты не настроены на сервере",
                    "message": "Обратитесь к администратору для настройки SERVER_ENCRYPTION_KEY и SERVER_ENCRYPTION_SALT"
                }
                import json
                await websocket.send_text(json.dumps(error_response))
                return
            
            # Шифруем секреты публичным ключом клиента
            encrypted_secrets = self.key_exchange.encrypt_secrets_for_client(client_id, secrets)
            if not encrypted_secrets:
                error_response = {
                    "type": "secrets_error",
                    "error": "Ошибка шифрования секретов",
                    "message": "Не удалось зашифровать секреты публичным ключом клиента"
                }
                import json
                await websocket.send_text(json.dumps(error_response))
                return
            
            # Отправляем зашифрованные секреты
            response = {
                "type": "secrets_config",
                "data": {
                    "encrypted_secrets": encrypted_secrets,
                    "encryption": "RSA-OAEP-SHA256",  # Указываем алгоритм для клиента (в data)
                    "version": secrets.get("version"),
                    "timestamp": secrets.get("timestamp")
                }
            }
            
            import json
            json_response = json.dumps(response)
            
            try:
                await websocket.send_text(json_response)
                logger.info(f"✅ Секреты безопасно отправлены клиенту {client_id} (зашифрованы RSA, версия {secrets.get('version')})")
                logger.info(f"   Клиент должен расшифровать секреты своим приватным ключом и переподключиться")
                
                # Даем время клиенту получить и обработать секреты
                await asyncio.sleep(0.5)
            except Exception as send_err:
                logger.error(f"❌ Ошибка отправки зашифрованных секретов клиенту {client_id}: {send_err}")
                return
            
            # НЕ закрываем соединение сразу - клиент сам закроет после получения секретов
            # или соединение закроется при переподключении
            logger.debug(f"✅ Зашифрованные секреты отправлены, ожидаем переподключение клиента {client_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки запроса секретов от {client_id}: {e}", exc_info=True)
            try:
                error_response = {
                    "type": "secrets_error",
                    "error": "Внутренняя ошибка сервера",
                    "message": str(e)
                }
                import json
                await websocket.send_text(json.dumps(error_response))
            except:
                pass

    async def _send_ws_challenge(self, websocket: WebSocket, client_id: str):
        """Отправка однократного WS challenge с nonce и timestamp (мягкий режим)."""
        nonce = secrets.token_bytes(16)
        ts = int(time.time())
        challenge = {
            "type": "auth_challenge",
            "data": {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ts": ts,
                "alg": "HMAC-SHA256",
                "hint": "sign: WS|/ws|<nonce_b64>|<ts>"
            }
        }
        # Сохраняем ожидаемый challenge для клиента
        meta = self.websocket_manager.get_metadata(client_id) or {}
        meta['challenge'] = {"nonce": nonce, "ts": ts}
        meta['challenge_verified'] = False
        self.websocket_manager.update_metadata(client_id, meta)
        enc = await self.encryption_service.encrypt_message(challenge, client_id)
        await websocket.send_text(enc)

    async def _handle_auth_challenge_response(self, websocket: WebSocket, message: dict, client_id: str):
        """Проверка ответа на WS challenge. Мягкий режим: при неуспехе не разрываем соединение."""
        try:
            data = message.get('data', {})
            sig_b64 = data.get('signature', '')
            client_id_claim = data.get('client_id', client_id)
            ts = int(data.get('ts', 0))
            nonce_b64 = data.get('nonce', '')

            meta = self.websocket_manager.get_metadata(client_id) or {}
            ch = meta.get('challenge') or {}
            expected_nonce = ch.get('nonce')
            expected_ts = ch.get('ts')

            if not sig_b64 or not expected_nonce or not expected_ts:
                return

            # Восстановим nonce
            try:
                recv_nonce = base64.b64decode(nonce_b64)
            except Exception:
                recv_nonce = b""

            # Простая защита от отложенной реплики: окно 60с
            now = int(time.time())
            if abs(now - ts) > 60:
                return

            # Сверим nonce
            if recv_nonce != expected_nonce or ts != expected_ts:
                return

            # Подготовим строку для подписи
            signing_str = f"WS|/ws|{nonce_b64}|{ts}".encode('utf-8')

            # Ключ для HMAC берём из сессионного ключа шифрования (PSK)
            key = getattr(self.encryption_service, "_encryption_key", None)
            if not key:
                return

            calc = compute_hmac(key, signing_str)
            try:
                recv_sig = base64.b64decode(sig_b64)
            except Exception:
                return
            if _hmac.compare_digest(calc, recv_sig):
                meta['challenge_verified'] = True
                meta['client_id_claim'] = client_id_claim
                self.websocket_manager.update_metadata(client_id, meta)
                ack = {"type": "auth_challenge_ok"}
            else:
                ack = {"type": "auth_challenge_fail"}

            enc = await self.encryption_service.encrypt_message(ack, client_id)
            await websocket.send_text(enc)
        except Exception as e:
            logger.warning(f"Ошибка проверки WS challenge: {e}")
    
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
            
            # Сверяем client_id из токена с текущим client_id соединения (после регистрации)
            registered = (self.websocket_manager.get_metadata(client_id) or {}).get('registered')
            if registered and token_client_id and token_client_id != client_id:
                logger.warning(f"⚠️ Несоответствие client_id: токен для {token_client_id}, соединение {client_id}")
                response = {
                    "type": "auth_failed",
                    "message": "Несоответствие client_id в токене"
                }
                encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
                await websocket.send_text(encrypted_response)
                # Закрываем соединение как нарушение политики
                await websocket.close(code=1008, reason="Client ID mismatch in token")
                return
            
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
    
    async def _handle_tls_downgrade_request(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка запроса на TLS downgrade от клиента"""
        try:
            data = message.get('data', {})
            reason = data.get('reason', 'TLS connection failed')
            tls_error = data.get('tls_error', '')
            hostname = data.get('hostname', 'unknown')
            
            logger.warning(f"⚠️⚠️⚠️ TLS DOWNGRADE REQUEST от {client_id}")
            logger.warning(f"  Hostname: {hostname}")
            logger.warning(f"  Причина: {reason}")
            logger.warning(f"  TLS ошибка: {tls_error}")
            
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ P0-4: TLS downgrade разрешен только администратором
            # По умолчанию ЗАПРЕЩАЕМ, но администратор может явно разрешить через ALLOW_TLS_DOWNGRADE
            allow_downgrade = getattr(settings, "allow_tls_downgrade", False)
            
            # Проверяем окружение для дополнительных предупреждений
            import os
            env = os.getenv("ENVIRONMENT", os.getenv("ENV", "production")).lower()
            is_production = env in ("production", "prod", "live")
            
            if allow_downgrade:
                # КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ в production
                if is_production:
                    logger.critical(f"🔴 КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: TLS downgrade РАЗРЕШЕН в ПРОДАКШЕНЕ!")
                    logger.critical(f"   Это создает серьезный риск безопасности!")
                    logger.critical(f"   Убедитесь что это явное административное решение")
                else:
                    logger.warning(f"⚠️ TLS downgrade разрешен для разработки")
                logger.warning(f"⚠️ TLS downgrade РАЗРЕШЕН для {client_id} (настройка allow_tls_downgrade=True)")
                logger.warning(f"🔓 ВНИМАНИЕ: Соединение будет незащищенным!")
                
                response = {
                    "type": "tls_downgrade_approved",
                    "message": "TLS downgrade разрешен администратором",
                    "data": {
                        "warning": "Соединение будет НЕЗАЩИЩЕННЫМ! Все данные передаются в открытом виде.",
                        "approved_by": "server_config",
                        "timestamp": int(time.time())
                    }
                }
                
                # Помечаем клиента как использующего незащищенное соединение
                meta = self.websocket_manager.get_metadata(client_id) or {}
                meta['insecure_connection'] = True
                meta['tls_downgrade_approved_at'] = time.time()
                self.websocket_manager.update_metadata(client_id, meta)
            else:
                logger.error(f"❌ TLS downgrade ОТКЛОНЕН для {client_id}")
                logger.error(f"💡 Для разрешения отката установите ALLOW_TLS_DOWNGRADE=true в конфиге")
                
                response = {
                    "type": "tls_downgrade_denied",
                    "message": "TLS downgrade запрещен политикой безопасности",
                    "data": {
                        "reason": "Server security policy denies TLS downgrade",
                        "suggestion": "Исправьте TLS конфигурацию или обратитесь к администратору",
                        "timestamp": int(time.time())
                    }
                }
            
            encrypted_response = await self.encryption_service.encrypt_message(response, client_id)
            await websocket.send_text(encrypted_response)
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки TLS downgrade request: {e}")

    # --- Terminal helpers (PoC) ---
    async def register_terminal_session(self, session_id: str, agent_id: str, initiator: dict = None):
        """Register a new terminal session mapping to an agent."""
        # create recording dir and metadata
        import os, time, json
        from ..config import settings
        rec_dir = getattr(settings, "TERMINAL_RECORDING_DIR", "/tmp/terminals")
        try:
            os.makedirs(rec_dir, exist_ok=True)
        except Exception:
            logger.warning(f"Не удалось создать каталог для записей терминалов: {rec_dir}")
        rec_path = os.path.join(rec_dir, f"{session_id}.log")
        meta = {"session_id": session_id, "agent_id": agent_id, "initiator": initiator, "started_at": time.time()}
        # write initial metadata header
        try:
            with open(rec_path, "a", encoding="utf-8") as rf:
                rf.write(json.dumps({"meta": meta}) + "\n")
        except Exception:
            logger.exception("Ошибка записи метаданных терминальной сессии")

        self.terminal_sessions[session_id] = {"agent_id": agent_id, "frontend_ws": None, "record_path": rec_path, "initiator": initiator}
        # send audit event (best-effort) to core_service
        try:
            payload = {
                "session_id": session_id,
                "client_id": agent_id,
                "initiator": initiator,
                "event": "started",
                "ts": time.time(),
                "record_path": rec_path,
            }
            await asyncio.to_thread(self._send_audit_to_core, payload)
        except Exception:
            logger.exception("Ошибка отправки аудита в core_service при создании сессии")

    def _send_audit_to_core(self, payload: dict):
        """Synchronous helper to POST audit to core admin api (run in thread)."""
        try:
            import os, json, http.client
            from urllib.parse import urlparse as _parse
            base = os.getenv("CORE_ADMIN_URL", "http://127.0.0.1:11000")
            b = _parse(base)
            scheme = (b.scheme or "http").lower()
            host = b.hostname or "127.0.0.1"
            port = b.port or (443 if scheme == "https" else 80)
            path = "/api/terminals/audit"
            if scheme == "https":
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn = http.client.HTTPSConnection(host, port, timeout=5, context=ctx)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=5)
            hdrs = {"Content-Type": "application/json"}
            body = json.dumps(payload).encode("utf-8")
            conn.request("POST", path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            try:
                text = data.decode("utf-8") if data else ""
            except Exception:
                text = ''
            if resp.status < 200 or resp.status >= 300:
                logger.debug(f"Audit POST returned {resp.status}: {text}")
        except Exception:
            logger.exception("_send_audit_to_core failed")

    async def attach_frontend_to_session(self, session_id: str, websocket: WebSocket):
        sess = self.terminal_sessions.get(session_id)
        if not sess:
            return False
        sess['frontend_ws'] = websocket
        return True

    async def detach_session(self, session_id: str):
        sess = self.terminal_sessions.pop(session_id, None)
        if sess and sess.get('frontend_ws'):
            try:
                await sess['frontend_ws'].close()
            except Exception:
                pass

        # mark session end in recording
        try:
            rp = sess.get('record_path') if sess else None
            if rp:
                import time, json
                with open(rp, 'a', encoding='utf-8') as rf:
                    rf.write(json.dumps({"event": "session_detached", "ts": time.time()}) + '\n')
        except Exception:
            logger.exception("Ошибка при пометке завершения сессии в записи")

    async def send_input_to_agent(self, session_id: str, b64_payload: str) -> bool:
        """Send terminal input to agent (encrypted message)."""
        sess = self.terminal_sessions.get(session_id)
        if not sess:
            return False
        agent_id = sess.get('agent_id')
        if not agent_id:
            return False
        msg = {
            "type": "terminal.input",
            "data": {"session_id": session_id, "payload": b64_payload}
        }
        try:
            enc = await self.encryption_service.encrypt_message(msg, agent_id)
            return await self.websocket_manager.send_message(agent_id, enc)
        except Exception as e:
            logger.error(f"Ошибка отправки terminal.input агенту {agent_id}: {e}")
            return False


    async def _handle_terminal_output(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle terminal output coming from agent and forward to frontend websocket."""
        try:
            data = message.get('data', {})
            session_id = data.get('session_id')
            payload_b64 = data.get('payload')
            if not session_id or not payload_b64:
                return
            import base64
            decoded = base64.b64decode(payload_b64)
            sess = self.terminal_sessions.get(session_id)
            if not sess:
                logger.debug(f"Terminal output for unknown session {session_id}")
                return
            fw = sess.get('frontend_ws')
            if not fw:
                logger.debug(f"No frontend attached for session {session_id}")
                return
            # write to recording (audit)
            try:
                rp = sess.get('record_path')
                if rp:
                    import time, json
                    entry = {"event": "output", "ts": time.time(), "data_b64": payload_b64}
                    with open(rp, 'a', encoding='utf-8') as rf:
                        rf.write(json.dumps(entry) + '\n')
            except Exception:
                logger.exception("Ошибка записи вывода терминала в файл")

            await fw.send_bytes(decoded)
        except Exception as e:
            logger.error(f"Ошибка при обработке terminal.output: {e}")

    async def _handle_terminal_started(self, websocket: WebSocket, message: dict, client_id: str):
        try:
            data = message.get('data', {})
            session_id = data.get('session_id')
            sess = self.terminal_sessions.get(session_id)
            if not sess:
                return
            fw = sess.get('frontend_ws')
            if fw:
                await fw.send_text('{"type":"terminal.started","session_id":"%s"}' % session_id)
            # write start event to recording
            try:
                rp = sess.get('record_path')
                if rp:
                    import time, json
                    with open(rp, 'a', encoding='utf-8') as rf:
                        rf.write(json.dumps({"event": "started", "ts": time.time()}) + '\n')
            except Exception:
                logger.exception("Ошибка записи события start в запись терминала")
        except Exception as e:
            logger.error(f"Ошибка при обработке terminal.started: {e}")

    async def _handle_terminal_stopped(self, websocket: WebSocket, message: dict, client_id: str):
        try:
            data = message.get('data', {})
            session_id = data.get('session_id')
            sess = self.terminal_sessions.get(session_id)
            if not sess:
                return
            fw = sess.get('frontend_ws')
            if fw:
                await fw.send_text('{"type":"terminal.stopped","session_id":"%s","exit_code":%s}' % (session_id, data.get('exit_code', 0)))
            # cleanup
            # write stop event
            try:
                rp = sess.get('record_path')
                import time, json
                if rp:
                    with open(rp, 'a', encoding='utf-8') as rf:
                        rf.write(json.dumps({"event": "stopped", "ts": time.time(), "exit_code": data.get('exit_code', 0)}) + '\n')
            except Exception:
                logger.exception("Ошибка записи события stop в запись терминала")
            # send audit stop event
            try:
                payload = {
                    "session_id": session_id,
                    "client_id": sess.get('agent_id'),
                    "event": "stopped",
                    "ts": time.time(),
                    "exit_code": data.get('exit_code', 0),
                    "record_path": sess.get('record_path')
                }
                await asyncio.to_thread(self._send_audit_to_core, payload)
            except Exception:
                logger.exception("Ошибка отправки аудита в core_service при остановке сессии")

            # Try to upload recording to external object storage (S3/MinIO) in background
            try:
                rp = sess.get('record_path')
                if rp:
                    # run background task without waiting
                    asyncio.create_task(self._maybe_upload_recording(rp, session_id))
            except Exception:
                logger.exception("Ошибка запуска фоновой загрузки записи в S3/MinIO")

            await self.detach_session(session_id)
        except Exception as e:
            logger.error(f"Ошибка при обработке terminal.stopped: {e}")
    
    async def _stats_middleware(self, websocket: WebSocket, message: dict, client_id: str) -> dict:
        """Middleware для обновления статистики"""
        self.stats["total_messages"] += 1
        
        if message.get('type') in ['command_request', 'command_result']:
            self.stats["total_commands"] += 1
        
        return message

    async def _maybe_upload_recording(self, record_path: str, session_id: str):
        """Upload recording file to S3/MinIO if configured. Runs in background."""
        try:
            from ..config import settings
            s3_endpoint = getattr(settings, "s3_endpoint", None)
            bucket = getattr(settings, "s3_bucket", None)
            if not s3_endpoint or not bucket:
                logger.debug("S3/MinIO not configured, skipping upload of recording")
                return

            # Lazy import boto3 to keep dependency optional until used
            try:
                import boto3
            except Exception:
                logger.error("boto3 is not installed - cannot upload recordings to S3/MinIO")
                return

            aws_key = getattr(settings, "s3_access_key_id", None)
            aws_secret = getattr(settings, "s3_secret_access_key", None)
            region = getattr(settings, "s3_region", None)
            use_ssl = getattr(settings, "s3_use_ssl", True)
            presign_expiry = int(getattr(settings, "s3_presign_expiry_seconds", 3600))

            # Key name in bucket: terminals/{session_id}.log
            key_name = f"terminals/{session_id}.log"

            client_kwargs = {"endpoint_url": s3_endpoint}
            if aws_key and aws_secret:
                client_kwargs.update({"aws_access_key_id": aws_key, "aws_secret_access_key": aws_secret})
            if region:
                client_kwargs.update({"region_name": region})

            s3 = boto3.client("s3", **client_kwargs)

            # Upload
            try:
                s3.upload_file(record_path, bucket, key_name)
                logger.info(f"Recording {record_path} uploaded to {bucket}/{key_name}")
            except Exception as e:
                logger.exception(f"Failed to upload recording to S3/MinIO: {e}")
                return

            # Generate presigned URL if possible
            presigned_url = None
            try:
                presigned_url = s3.generate_presigned_url(
                    ClientMethod='get_object',
                    Params={'Bucket': bucket, 'Key': key_name},
                    ExpiresIn=presign_expiry
                )
            except Exception:
                logger.debug("Could not generate presigned URL for uploaded recording")

            # Send audit update to core with storage info
            try:
                payload = {
                    "session_id": session_id,
                    "event": "recording_uploaded",
                    "ts": time.time(),
                    "storage_bucket": bucket,
                    "storage_key": key_name,
                    "presigned_url": presigned_url,
                }
                await asyncio.to_thread(self._send_audit_to_core, payload)
            except Exception:
                logger.exception("Ошибка отправки аудита о загруженной записи в core_service")

            # Optionally remove local recording file to save disk space (respect retention policy)
            try:
                import os
                retention_days = int(getattr(settings, "recordings_retention_days", 30))
                # If retention is configured <=0 we still remove local copy after upload
                os.remove(record_path)
                logger.debug(f"Local recording {record_path} removed after upload")
            except Exception:
                logger.exception("Не удалось удалить локальный файл записи после загрузки")

        except Exception:
            logger.exception("Unexpected error in _maybe_upload_recording")
    
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
            "uptime": time.time() - self.stats["uptime_start"],
            # Сводка по файловым трансферам
            "transfer_stats": self._collect_transfer_stats()
        }

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
    
    async def download_file_from_device(self, device_id: str, remote_path: str) -> bytes:
        """Скачивание файла с устройства через агента через WebSocket"""
        # 1. Создаём трансфер в менеджере (direction = download)
        transfer_id = await self.transfers.create_upload(device_id, remote_path, None, None, "download")
        # Устанавливаем состояние в progress
        from .transfers_manager import TransferState
        await self.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)

        # 2. Устанавливаем временный путь хранения на сервере
        import tempfile, os
        tmpfd, tmp_path = tempfile.mkstemp(prefix=f"download_{transfer_id}_", suffix=".bin")
        os.close(tmpfd)
        self.transfers.transfers[transfer_id]["dest_path"] = tmp_path

        # 3. Отправляем сообщение клиенту (совместимо с клиентским протоколом)
        start_msg = {
            "type": "file_download_start",
            "data": {
                "transfer_id": transfer_id,
                "path": remote_path,
                "chunk_size": 1 << 20,
                "start_offset": 0,
            },
        }
        encrypted = await self.encryption_service.encrypt_message(start_msg, device_id)
        await self.websocket_manager.send_message(device_id, encrypted)

        # 4. Ждём завершения трансфера (event-based) с таймаутом
        timeout = getattr(settings, "file_transfer_timeout", 120)
        try:
            final_state = await self.transfers.wait_for_state(transfer_id, [TransferState.COMPLETED], timeout=int(timeout))
        except KeyError:
            raise Exception("Трансфер удалён неожиданно")
        except TimeoutError:
            raise Exception("Таймаут ожидания завершения файлового трансфера")

        # Проверим итоговое состояние
        t = self.transfers.get(transfer_id)
        if not t:
            raise Exception("Трансфер удалён неожиданно")
        state = t.get("state")
        if state == TransferState.COMPLETED:
            try:
                with open(t.get("dest_path"), "rb") as f:
                    data = f.read()
                return data
            except Exception as e:
                raise
        if state in (TransferState.FAILED, TransferState.CANCELLED):
            raise Exception(f"Transfer {transfer_id} ended with state {state}")
        # На всякий случай
        raise Exception(f"Transfer {transfer_id} finished in unexpected state: {state}")

    async def upload_file_to_device(self, device_id: str, local_path: str, file_data: bytes):
        """Загрузка файла на устройство через агента через WebSocket"""
        # 1. Создаём трансфер (direction = upload)
        transfer_id = await self.transfers.create_upload(device_id, local_path, len(file_data), None, "upload")
        from .transfers_manager import TransferState
        await self.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)

        # 2. Запишем байты во временный файл и используем существующий код отправки
        import tempfile, os
        fd, tmp_path = tempfile.mkstemp(prefix=f"upload_{transfer_id}_", suffix=".bin")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(file_data)

            # Используем FileTransferHandler.send_upload_from_server(client_id, transfer_id, src_path)
            await self.file_handler.send_upload_from_server(device_id, transfer_id, tmp_path)
            # Ожидаем завершения трансфера (event-based)
            timeout = getattr(settings, "file_transfer_timeout", 120)
            try:
                await self.transfers.wait_for_state(transfer_id, [TransferState.COMPLETED], timeout=int(timeout))
            except Exception as e:
                t = self.transfers.get(transfer_id)
                state = t.get("state") if t else "unknown"
                raise Exception(f"Upload failed or timed out, final state: {state}")
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return True

    async def delete_file_on_device(self, device_id: str, remote_path: str):
        """Удаление файла на устройстве"""
        command = f"rm -f '{remote_path}'"
        result = await self.send_command_to_client(device_id, command)

        if not result.success:
            logger.warning(f"Не удалось удалить файл {remote_path} на устройстве {device_id}: {result.error}")

    async def list_files_on_device(self, device_id: str, remote_path: str, recursive: bool = False) -> list:
        """Получение списка файлов на устройстве"""
        flag = "-R" if recursive else ""
        command = f"find '{remote_path}' {flag} -type f -exec ls -la {{}} \\; 2>/dev/null || ls -la '{remote_path}'"

        result = await self.send_command_to_client(device_id, command)

        if not result.success:
            logger.warning(f"Не удалось получить список файлов на {device_id}: {result.error}")
            return []

        # Парсим вывод ls
        lines = result.result.split('\n')
        files = []

        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 9:
                files.append({
                    "permissions": parts[0],
                    "type": "file",  # упрощение
                    "path": ' '.join(parts[8:]),  # имя файла может содержать пробелы
                })

        return files

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
