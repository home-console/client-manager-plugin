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

    async def handle_registration(self, websocket: WebSocket, message: dict, skip_secrets_send: bool = False, pre_validated_agent_name: str | None = None) -> str:
        """
        Обработка регистрации клиента.

        Args:
            websocket: WebSocket соединение
            message: Сообщение регистрации
            skip_secrets_send: Если True, не отправляет секреты (для первоначальной синхронизации через RSA)
            pre_validated_agent_name: Уже валидированное имя агента из enrollment_token
                (передаётся из connection_loop, чтобы не валидировать токен повторно —
                токен уже помечен как USED на moment вызова этой функции).
        """
        h = self.handler
        client_id = await h.client_manager.register_client(websocket, message.get("data", {}))

        # Мигрируем per-agent ключ от "unknown" к реальному client_id СРАЗУ,
        # чтобы зашифровать registration_success правильным ключом.
        if client_id != "unknown" and "unknown" in h.encryption_service._client_keys:
            h.encryption_service._client_keys[client_id] = h.encryption_service._client_keys.pop("unknown")
            logger.info(f"🔑 Per-agent ключ мигрирован: unknown → {client_id} (в handle_registration)")

        # Сбрасываем состояние шифрования при переподключении
        if client_id in h.encryption_service.encryption_states:
            h.encryption_service.reset_encryption_state(client_id)

        client_data = message.get("data", {})

        # Регистрируем агента в agent_manager для отслеживания enrollment деплоем.
        # Приоритет: pre_validated_agent_name (из connection_loop, canonical из токена) >
        # повторная валидация токена > hostname fallback.
        _agent_name: str | None = pre_validated_agent_name

        if not _agent_name:
            # Пробуем валидировать токен (только если он ещё не был использован ранее)
            _enrollment_token = client_data.get("enrollment_token")
            _token_agent_mgr = getattr(h.runtime, "agent_manager", None) if h.runtime else None
            if _enrollment_token and _token_agent_mgr:
                try:
                    _agent_name = await _token_agent_mgr.validate_enrollment_token(_enrollment_token)
                    logger.warning(f"✅ [Enrollment] Token validated in handle_registration: agent_name={_agent_name!r}")
                except ValueError as _ve:
                    logger.warning(f"⚠️ [Enrollment] Token validation failed (ValueError): {_ve}")
                except Exception as _exc:
                    logger.warning(f"⚠️ [Enrollment] Token validation error: {_exc}", exc_info=True)
            elif _enrollment_token and not _token_agent_mgr:
                logger.warning(f"⚠️ [Enrollment] agent_manager unavailable, cannot validate token")

        # Fallback: если token недоступен — используем hostname/client_id
        if not _agent_name:
            _agent_name = (
                client_data.get("agent_name")
                or client_data.get("hostname")
                or client_id
            )
            logger.warning(f"⚠️ [Enrollment] Using fallback agent_name={_agent_name!r} (no token). client_data keys: {list(client_data.keys())}")

        agent_mgr = getattr(h.runtime, "agent_manager", None) if h.runtime else None
        if _agent_name and agent_mgr:
            try:
                await agent_mgr.register_agent_from_ws(_agent_name, client_id)
                logger.warning(f"✅ [Enrollment] register_agent_from_ws OK: agent_name={_agent_name!r}, client_id={client_id}")
            except Exception as _exc:
                logger.warning(f"⚠️ [Enrollment] register_agent_from_ws FAILED: agent_name={_agent_name!r}, client_id={client_id}, error={_exc}", exc_info=True)
        else:
            logger.warning(f"⚠️ [Enrollment] SKIPPED register_agent_from_ws: _agent_name={_agent_name!r}, runtime={h.runtime!r}, agent_mgr={agent_mgr!r}")

        # Проверяем версию секретов клиента
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
        # НО: клиентам с per-agent ключами (RSA-exchange) НЕ слать глобальные секреты —
        # это сломает их шифрование (они переинициализируются на глобальный ключ).
        has_per_agent_key = client_id in h.encryption_service._client_keys
        if not skip_secrets_send and not has_per_agent_key and client_secrets_version < server_secrets_version and is_trusted:
            logger.info(f"🔄 Клиент {client_id} имеет устаревшую версию секретов, отправляем обновление")
            try:
                await h.secrets_sync.send_secrets_to_client(client_id)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки секретов клиенту {client_id}: {e}")
        elif has_per_agent_key:
            logger.info(f"🔑 Клиент {client_id} использует per-agent ключ — secrets_sync пропускаем")

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
        MEDIUM-TERM: Per-Agent Key Exchange with RSA encryption.

        Architecture:
        1. Client sends its public RSA key
        2. Server generates UNIQUE per-agent encryption keys (not global!)
        3. Server encrypts these keys with client's public RSA key
        4. Client receives, decrypts, and stores in keyring
        5. From now on, client uses per-agent keys (not global SERVER_ENCRYPTION_KEY)
        6. Server NEVER stores agent keys (important for isolation!)

        This ensures:
        - Each agent has unique encryption context
        - If one agent is compromised, others are safe
        - Keys are never transmitted in plaintext
        """
        import secrets as sec_module

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

            # ===== MEDIUM-TERM: Generate UNIQUE per-agent keys =====
            logger.info(f"🔐 [MEDIUM-TERM] Generating unique encryption keys for agent {client_id}...")

            import os as _os
            agent_encryption_key = sec_module.token_hex(32)   # 256-bit hex key string
            agent_salt_bytes = _os.urandom(32)                 # 256-bit raw random bytes
            # Go клиент делает base64.StdDecoding.DecodeString(salt) → нужен base64, не hex
            import base64 as _b64
            agent_encryption_salt = _b64.b64encode(agent_salt_bytes).decode("utf-8")

            logger.info(f"✅ Generated unique keys for {client_id} (key: {agent_encryption_key[:8]}..., salt: {agent_encryption_salt[:8]}...)")

            # Сохраняем производный ключ в EncryptionService, чтобы сервер мог расшифровывать
            # сообщения от этого агента после его переподключения с per-agent ключами
            from ...utils.encryption import derive_key
            agent_derived_key = derive_key(agent_encryption_key, agent_salt_bytes)
            h.encryption_service.set_client_derived_key(client_id, agent_derived_key)
            logger.info(f"🔑 Per-agent derived key stored in EncryptionService for {client_id}")

            # Prepare secrets payload with per-agent keys
            secrets = {
                "encryption_key": agent_encryption_key,
                "encryption_salt": agent_encryption_salt,
                "version": h.secrets_sync.get_version(),
                "timestamp": time.time(),
                "client_id": client_id,
                "message": "Per-agent unique encryption keys (MEDIUM-TERM architecture)",
            }

            # Optional: Log audit trail (for compliance/debugging, not for decrypt)
            # SERVER DOES NOT STORE KEYS - this is just for audit trail
            logger.debug(f"🔍 [AUDIT] Agent {client_id} received unique keys at {time.time()}")

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
                    "encryption": "RSA-OAEP-SHA256+AES-256-GCM",  # Hybrid: RSA wraps AES key, AES-GCM encrypts payload
                    "version": secrets.get("version"),
                    "timestamp": secrets.get("timestamp"),
                    "architecture": "MEDIUM-TERM-per-agent",
                },
            }

            json_response = json.dumps(response)

            try:
                await websocket.send_text(json_response)
                logger.info(
                    f"✅ [MEDIUM-TERM] Per-agent encryption keys sent to {client_id} "
                    f"(RSA-encrypted, version {secrets.get('version')})"
                )
                logger.info("   🔐 Client should store keys in keyring and use them for future connections")
                logger.info("   📌 Server does NOT retain these keys (per-agent isolation)")

                # Даем время клиенту получить и обработать секреты
                await asyncio.sleep(0.5)
            except Exception as send_err:  # pragma: no cover
                logger.error(f"❌ Error sending per-agent keys to {client_id}: {send_err}")
                return

            # НЕ закрываем соединение сразу - клиент сам закроет после получения секретов
            logger.debug(f"✅ Per-agent keys sent, waiting for client reconnection with {client_id}")

        except Exception as e:
            logger.error(f"❌ Error processing request_secrets from {client_id}: {e}", exc_info=True)
            try:
                error_response = {
                    "type": "secrets_error",
                    "error": "Внутренняя ошибка сервера",
                    "message": str(e),
                }
                await websocket.send_text(json.dumps(error_response))
            except Exception:  # pragma: no cover
                pass

