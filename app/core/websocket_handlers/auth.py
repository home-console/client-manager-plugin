from __future__ import annotations

import base64
import logging
import os
import secrets
import time
import hmac as _hmac
from typing import TYPE_CHECKING

from fastapi import WebSocket

from ...utils.encryption import compute_hmac
from ...config import settings

if TYPE_CHECKING:  # pragma: no cover
    from ..websocket_handler import WebSocketHandler

logger = logging.getLogger(__name__)


class AuthHandlers:
    """Обработчики, отвечающие за challenge/auth и TLS-политику."""

    def __init__(self, handler: "WebSocketHandler"):
        self.handler = handler

    async def send_ws_challenge(self, websocket: WebSocket, client_id: str):
        """Отправка однократного WS challenge с nonce и timestamp (мягкий режим)."""
        h = self.handler
        nonce = secrets.token_bytes(16)
        ts = int(time.time())
        challenge = {
            "type": "auth_challenge",
            "data": {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ts": ts,
                "alg": "HMAC-SHA256",
                "hint": "sign: WS|/ws|<nonce_b64>|<ts>",
            },
        }
        # Сохраняем ожидаемый challenge для клиента
        meta = h.websocket_manager.get_metadata(client_id) or {}
        meta["challenge"] = {"nonce": nonce, "ts": ts}
        meta["challenge_verified"] = False
        h.websocket_manager.update_metadata(client_id, meta)
        enc = await h.encryption_service.encrypt_message(challenge, client_id)
        await websocket.send_text(enc)

    async def handle_auth_challenge_response(self, websocket: WebSocket, message: dict, client_id: str):
        """Проверка ответа на WS challenge. Мягкий режим: при неуспехе не разрываем соединение."""
        h = self.handler
        try:
            data = message.get("data", {})
            sig_b64 = data.get("signature", "")
            client_id_claim = data.get("client_id", client_id)
            ts = int(data.get("ts", 0))
            nonce_b64 = data.get("nonce", "")

            meta = h.websocket_manager.get_metadata(client_id) or {}
            ch = meta.get("challenge") or {}
            expected_nonce = ch.get("nonce")
            expected_ts = ch.get("ts")

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
            signing_str = f"WS|/ws|{nonce_b64}|{ts}".encode("utf-8")

            # Ключ для HMAC берём из сессионного ключа шифрования (PSK)
            key = getattr(h.encryption_service, "_encryption_key", None)
            if not key:
                return

            calc = compute_hmac(key, signing_str)
            try:
                recv_sig = base64.b64decode(sig_b64)
            except Exception:
                return
            if _hmac.compare_digest(calc, recv_sig):
                meta["challenge_verified"] = True
                meta["client_id_claim"] = client_id_claim
                h.websocket_manager.update_metadata(client_id, meta)
                ack = {"type": "auth_challenge_ok"}
            else:
                ack = {"type": "auth_challenge_fail"}

            enc = await h.encryption_service.encrypt_message(ack, client_id)
            await websocket.send_text(enc)
        except Exception as e:  # pragma: no cover - мягкий режим
            logger.warning(f"Ошибка проверки WS challenge: {e}")

    async def handle_auth(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка аутентификации с JWT токенами."""
        h = self.handler
        auth_data = message.get("data", {})
        auth_token = auth_data.get("auth_token", "")

        if not auth_token:
            response = {"type": "auth_failed", "message": "Токен аутентификации не предоставлен"}
            encrypted_response = await h.encryption_service.encrypt_message(response, client_id)
            await websocket.send_text(encrypted_response)
            logger.warning(f"⚠️ Пустой токен аутентификации от клиента {client_id}")
            return

        # Проверяем JWT токен
        payload = h.auth_service.verify_token(auth_token)

        if payload:
            # Токен валиден
            token_client_id = payload.get("client_id")
            permissions = payload.get("permissions", [])

            # Сверяем client_id из токена с текущим client_id соединения (после регистрации)
            registered = (h.websocket_manager.get_metadata(client_id) or {}).get("registered")
            if registered and token_client_id and token_client_id != client_id:
                logger.warning(f"⚠️ Несоответствие client_id: токен для {token_client_id}, соединение {client_id}")
                response = {"type": "auth_failed", "message": "Несоответствие client_id в токене"}
                encrypted_response = await h.encryption_service.encrypt_message(response, client_id)
                await websocket.send_text(encrypted_response)
                # Закрываем соединение как нарушение политики
                await websocket.close(code=1008, reason="Client ID mismatch in token")
                return

            # Сохраняем разрешения для клиента
            for permission in permissions:
                h.auth_service.add_permission(client_id, permission)

            logger.info(f"🔐 Аутентификация успешна для клиента {client_id} (разрешения: {permissions})")

            response = {
                "type": "auth_success",
                "message": "Аутентификация успешна",
                "permissions": permissions,
            }
        else:
            # Токен невалиден
            logger.warning(f"⚠️ Невалидный токен от клиента {client_id}")
            response = {"type": "auth_failed", "message": "Невалидный или истекший токен аутентификации"}

        encrypted_response = await h.encryption_service.encrypt_message(response, client_id)
        await websocket.send_text(encrypted_response)

    async def handle_key_exchange(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка обмена ключами (игнорируем в PSK режиме)."""
        h = self.handler
        logger.info(f"🔑 Key exchange получен от {client_id}, PSK режим — игнорируем")

        response = {"type": "key_exchange_response", "message": "PSK режим активен, key exchange не требуется"}

        encrypted_response = await h.encryption_service.encrypt_message(response, client_id)
        await websocket.send_text(encrypted_response)

    async def handle_tls_downgrade_request(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка запроса на TLS downgrade от клиента."""
        h = self.handler
        try:
            data = message.get("data", {})
            reason = data.get("reason", "TLS connection failed")
            tls_error = data.get("tls_error", "")
            hostname = data.get("hostname", "unknown")

            logger.warning("⚠️⚠️⚠️ TLS DOWNGRADE REQUEST от %s", client_id)
            logger.warning("  Hostname: %s", hostname)
            logger.warning("  Причина: %s", reason)
            logger.warning("  TLS ошибка: %s", tls_error)

            # TLS downgrade разрешен только администратором
            allow_downgrade = getattr(settings, "allow_tls_downgrade", False)

            # Проверяем окружение для дополнительных предупреждений
            env = os.getenv("ENVIRONMENT", os.getenv("ENV", "production")).lower()
            is_production = env in ("production", "prod", "live")

            if allow_downgrade:
                # Критическое предупреждение в production
                if is_production:
                    logger.critical("🔴 КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: TLS downgrade РАЗРЕШЕН в ПРОДАКШЕНЕ!")
                    logger.critical("   Это создает серьезный риск безопасности!")
                    logger.critical("   Убедитесь что это явное административное решение")
                else:
                    logger.warning("⚠️ TLS downgrade разрешен для разработки")
                logger.warning(f"⚠️ TLS downgrade РАЗРЕШЕН для {client_id} (allow_tls_downgrade=True)")
                logger.warning("🔓 ВНИМАНИЕ: Соединение будет незащищенным!")

                response = {
                    "type": "tls_downgrade_approved",
                    "message": "TLS downgrade разрешен администратором",
                    "data": {
                        "warning": "Соединение будет НЕЗАЩИЩЕННЫМ! Все данные передаются в открытом виде.",
                        "approved_by": "server_config",
                        "timestamp": int(time.time()),
                    },
                }

                # Помечаем клиента как использующего незащищенное соединение
                meta = h.websocket_manager.get_metadata(client_id) or {}
                meta["insecure_connection"] = True
                meta["tls_downgrade_approved_at"] = time.time()
                h.websocket_manager.update_metadata(client_id, meta)
            else:
                logger.error(f"❌ TLS downgrade ОТКЛОНЕН для {client_id}")
                logger.error("💡 Для разрешения отката установите ALLOW_TLS_DOWNGRADE=true в конфиге")

                response = {
                    "type": "tls_downgrade_denied",
                    "message": "TLS downgrade запрещен политикой безопасности",
                    "data": {
                        "reason": "Server security policy denies TLS downgrade",
                        "suggestion": "Исправьте TLS конфигурацию или обратитесь к администратору",
                        "timestamp": int(time.time()),
                    },
                }

            encrypted_response = await h.encryption_service.encrypt_message(response, client_id)
            await websocket.send_text(encrypted_response)

        except Exception as e:  # pragma: no cover
            logger.error(f"❌ Ошибка обработки TLS downgrade request: {e}")

