from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING

from fastapi import WebSocket

if TYPE_CHECKING:  # pragma: no cover
    from ..websocket_handler import WebSocketHandler

logger = logging.getLogger(__name__)


class RegistrationHandlers:
    """Группа обработчиков, связанных с регистрацией и выдачей секретов."""

    def __init__(self, handler: "WebSocketHandler"):
        self.handler = handler

    async def handle_registration(self, websocket: WebSocket, message: dict, skip_secrets_send: bool = False) -> str:
        """
        Обработка регистрации клиента.

        Args:
            websocket: WebSocket соединение
            message: Сообщение регистрации
            skip_secrets_send: Если True, не отправляет секреты (для первоначальной синхронизации через RSA)
        """
        h = self.handler
        client_id = await h.client_manager.register_client(websocket, message.get("data", {}))

        # Сбрасываем состояние шифрования при переподключении
        if client_id in h.encryption_service.encryption_states:
            h.encryption_service.reset_encryption_state(client_id)

        # Проверяем версию секретов клиента
        client_data = message.get("data", {})
        client_secrets_version = client_data.get("secrets_version", 0)
        server_secrets_version = h.secrets_sync.get_version()

        logger.info(
            f"📋 Регистрация клиента {client_id}: версия секретов клиента={client_secrets_version}, "
            f"сервера={server_secrets_version}"
        )

        # Проверяем статус доверия ДО отправки секретов
        is_trusted = h.enrollments.is_trusted(client_id)

        # Автоматическое утверждение для зашифрованных регистраций
        if not is_trusted:
            auto_approve = str(os.getenv("AUTO_APPROVE_ENROLLMENTS", "false")).lower() in ("1", "true", "yes")
            if auto_approve:
                h.enrollments.approve(client_id)
                is_trusted = True
                logger.info(f"✅ Автоматическое утверждение для клиента {client_id}")

        # Если версия устарела или отсутствует - отправляем секреты (только если не пропущено и уже trusted)
        if not skip_secrets_send and client_secrets_version < server_secrets_version and is_trusted:
            logger.info(f"🔄 Клиент {client_id} имеет устаревшую версию секретов, отправляем обновление")
            try:
                await h.secrets_sync.send_secrets_to_client(client_id)
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
                    "max_output_size": "10MB",
                },
                "secrets_version": server_secrets_version,  # Информируем клиента о версии
                "trusted": is_trusted,  # Сообщаем клиенту его статус
            }

            # Шифруем и отправляем ответ
            encrypted_response = await h.encryption_service.encrypt_message(response, client_id)

            # Проверяем, что соединение еще активно
            try:
                await websocket.send_text(encrypted_response)
                logger.info(f"✅ Регистрация завершена для клиента {client_id} (trusted={is_trusted})")
                # После успешной регистрации (зашифрованной) запустим мониторинг, если разрешено
                ws_mon_disable = str(os.getenv("WS_MONITOR_DISABLE", "true")).lower() in ("1", "true", "yes")
                if not ws_mon_disable and is_trusted:
                    await asyncio.sleep(0.5)
                    await h.websocket_manager.start_monitoring(client_id)
            except Exception as e:  # pragma: no cover - сетевые ошибки
                logger.error(f"❌ Ошибка отправки ответа регистрации: {e}")
                return client_id  # Возвращаем client_id даже если не удалось отправить ответ
        else:
            # Если skip_secrets_send=True, работаем по TOFU: ожидаем approve администратора
            logger.info(f"✅ Регистрация завершена для клиента {client_id} (TOFU pending)")

        return client_id

    async def send_enrollment_result(self, client_id: str, status: str = "approved") -> None:
        """Отправить клиенту результат утверждения (approved/rejected)."""
        h = self.handler
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
            h._trusted_clients.add(client_id)
        # Отправляем как plaintext (первое соединение ещё без шифрования)
        ws = h.client_manager.get_client(client_id)
        if ws:
            await ws.send_text(json.dumps(message))
        # Если approved — можно предложить клиенту запросить секреты
        if status == "approved":
            try:
                ws = h.client_manager.get_client(client_id)
                if ws:
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "secrets_needed",
                                "message": "Отправьте 'request_secrets' с вашим публичным RSA ключом для получения секретов",
                            }
                        )
                    )
            except Exception:  # pragma: no cover - best effort уведомление
                pass

    async def handle_request_secrets(self, websocket: WebSocket, message: dict, client_id: str):
        """
        БЕЗОПАСНЫЙ обмен секретами с использованием RSA шифрования.

        Клиент отправляет свой публичный RSA ключ, сервер шифрует секреты этим ключом.
        """
        h = self.handler
        try:
            data = message.get("data", {})
            public_key_pem = data.get("public_key")

            if not public_key_pem:
                error_response = {
                    "type": "secrets_error",
                    "error": "Публичный ключ не предоставлен",
                    "message": "Отправьте публичный RSA ключ в формате PEM в поле 'public_key'",
                }
                await websocket.send_text(json.dumps(error_response))
                return

            # Регистрируем публичный ключ клиента
            if not h.key_exchange.register_client_public_key(client_id, public_key_pem):
                error_response = {
                    "type": "secrets_error",
                    "error": "Неверный формат публичного ключа",
                    "message": "Публичный ключ должен быть в формате PEM (RSA)",
                }
                await websocket.send_text(json.dumps(error_response))
                return

            # Получаем текущие секреты
            secrets = h.secrets_sync.get_current_secrets()
            if not secrets.get("encryption_key") or not secrets.get("encryption_salt"):
                error_response = {
                    "type": "secrets_error",
                    "error": "Секреты не настроены на сервере",
                    "message": "Обратитесь к администратору для настройки SERVER_ENCRYPTION_KEY и SERVER_ENCRYPTION_SALT",
                }
                await websocket.send_text(json.dumps(error_response))
                return

            # Шифруем секреты публичным ключом клиента
            encrypted_secrets = h.key_exchange.encrypt_secrets_for_client(client_id, secrets)
            if not encrypted_secrets:
                error_response = {
                    "type": "secrets_error",
                    "error": "Ошибка шифрования секретов",
                    "message": "Не удалось зашифровать секреты публичным ключом клиента",
                }
                await websocket.send_text(json.dumps(error_response))
                return

            # Отправляем зашифрованные секреты
            response = {
                "type": "secrets_config",
                "data": {
                    "encrypted_secrets": encrypted_secrets,
                    "encryption": "RSA-OAEP-SHA256",  # Указываем алгоритм для клиента (в data)
                    "version": secrets.get("version"),
                    "timestamp": secrets.get("timestamp"),
                },
            }

            json_response = json.dumps(response)

            try:
                await websocket.send_text(json_response)
                logger.info(
                    f"✅ Секреты безопасно отправлены клиенту {client_id} "
                    f"(зашифрованы RSA, версия {secrets.get('version')})"
                )
                logger.info("   Клиент должен расшифровать секреты своим приватным ключом и переподключиться")

                # Даем время клиенту получить и обработать секреты
                await asyncio.sleep(0.5)
            except Exception as send_err:  # pragma: no cover
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
                    "message": str(e),
                }
                await websocket.send_text(json.dumps(error_response))
            except Exception:  # pragma: no cover
                pass

