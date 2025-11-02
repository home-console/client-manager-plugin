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
from app.utils.encryption import compute_hmac
from app.config import settings

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
        
        # Синхронизация секретов (websocket_handler передается после инициализации)
        from .secrets_sync import SecretsSyncService
        self.secrets_sync = SecretsSyncService(self.encryption_service)
        self.secrets_sync.set_websocket_handler(self)
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
        heartbeat_handler = HeartbeatHandler(self.client_manager)
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
        
        # Обработчик key_exchange (игнорируем в PSK режиме)
        self.message_router.register_handler('key_exchange', self._handle_key_exchange)
        
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
                try:
                    message = await self.encryption_service.decrypt_message(data, client_id)
                except Exception as sec_err:
                    # Специальная обработка для первого подключения с неверными ключами
                    if client_id == "unknown" and ("HMAC mismatch" in str(sec_err) or "Unencrypted WebSocket message" in str(sec_err)):
                        logger.warning(f"⚠️ HMAC mismatch при первом подключении - клиент использует неверные ключи")
                        logger.info(f"🔐 Отправка секретов в незашифрованном виде для синхронизации")
                        try:
                            # Отправляем секреты ПЕРЕД закрытием соединения
                            logger.debug("Попытка отправить незашифрованные секреты...")
                            await self._send_secrets_unencrypted(websocket)
                            logger.info("✅ Секреты отправлены, ожидаем переподключение клиента с новыми ключами")
                            # Небольшая задержка чтобы убедиться что сообщение отправлено
                            import asyncio
                            await asyncio.sleep(0.2)  # Увеличена задержка
                        except Exception as sync_err:
                            logger.error(f"❌ Ошибка отправки секретов: {sync_err}", exc_info=True)
                            # Пробуем еще раз отправку с более детальным логированием
                            try:
                                logger.debug(f"Попытка повторной отправки секретов...")
                                secrets = self.secrets_sync.get_current_secrets()
                                logger.debug(f"Секреты для отправки: версия={secrets.get('version')}, ключ_длина={len(secrets.get('encryption_key', ''))}")
                                message = {
                                    "type": "secrets_config",
                                    "data": secrets,
                                    "unencrypted": True
                                }
                                import json
                                json_data = json.dumps(message)
                                logger.debug(f"JSON размер: {len(json_data)} байт")
                                await websocket.send_text(json_data)
                                logger.info("✅ Повторная отправка секретов успешна")
                            except Exception as retry_err:
                                logger.error(f"❌ Повторная попытка отправки тоже не удалась: {retry_err}", exc_info=True)
                        finally:
                            # Закрываем соединение в finally блоке
                            try:
                                await websocket.close(code=1000, reason="Secrets sent, please reconnect")
                                logger.debug("Соединение закрыто после отправки секретов")
                            except Exception as close_err:
                                logger.debug(f"Соединение уже закрыто или ошибка закрытия: {close_err}")
                        break
                    else:
                        # Немедленно закрываем соединение при провале дешифрования/HMAC/формата
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
                # Помечаем все трансферы клиента на паузу при отключении
                try:
                    paused = self.transfers.pause_all_for_client(client_id)
                    logger.info(f"⏸️ Поставлено на паузу трансферов: {paused} для клиента {client_id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка паузы трансферов для {client_id}: {e}")
                await self._cleanup_client(client_id)
            self.stats["active_connections"] -= 1
    
    async def _handle_registration(self, websocket: WebSocket, message: dict) -> str:
        """Обработка регистрации клиента"""
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
        
        # Если версия устарела или отсутствует - отправляем секреты
        if client_secrets_version < server_secrets_version:
            logger.info(f"🔄 Клиент {client_id} имеет устаревшую версию секретов, отправляем обновление")
            try:
                await self.secrets_sync.send_secrets_to_client(client_id)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки секретов клиенту {client_id}: {e}")
        
        # Отправляем подтверждение с информацией о версии секретов
        response = {
            "type": "registration_success",
            "client_id": client_id,
            "message": "Клиент успешно зарегистрирован",
            "server_info": {
                "version": "2.0.0",
                "features": ["commands", "file_ops", "monitoring", "encryption", "secrets_sync"],
                "max_command_timeout": 300,
                "max_output_size": "10MB"
            },
            "secrets_version": server_secrets_version  # Информируем клиента о версии
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
    
    async def _send_secrets_unencrypted(self, websocket: WebSocket):
        """Отправка секретов в незашифрованном виде для синхронизации при первом подключении"""
        try:
            secrets = self.secrets_sync.get_current_secrets()
            logger.debug(f"Подготовка незашифрованных секретов: версия={secrets.get('version')}")
            
            # Проверяем, что секреты валидны
            if not secrets.get('encryption_key') or not secrets.get('encryption_salt'):
                raise ValueError("Секреты не валидны: отсутствует encryption_key или encryption_salt")
            
            message = {
                "type": "secrets_config",
                "data": secrets,
                "unencrypted": True  # Флаг что сообщение не зашифровано (для начальной синхронизации)
            }
            
            # Отправляем как обычный JSON (незашифрованный)
            import json
            json_str = json.dumps(message)
            logger.debug(f"Отправка незашифрованного JSON: {len(json_str)} байт")
            logger.debug(f"Первые 200 символов: {json_str[:200]}")
            
            await websocket.send_text(json_str)
            logger.info(f"✅ Секреты отправлены незашифрованными для синхронизации (версия {secrets['version']})")
        except Exception as e:
            logger.error(f"❌ Ошибка в _send_secrets_unencrypted: {e}", exc_info=True)
            raise

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
            
            # По умолчанию ЗАПРЕЩАЕМ откат в продакшене
            # Администратор должен явно разрешить через конфиг или панель управления
            allow_downgrade = getattr(settings, "allow_tls_downgrade", False)
            
            if allow_downgrade:
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
