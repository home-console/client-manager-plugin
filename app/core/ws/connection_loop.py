"""
Основной цикл обработки WebSocket вынесен из websocket_handler.py.
Функция handle_connection сохраняет существующую логику, но изолирует её в отдельный модуль.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def handle_connection(handler: "WebSocketHandler", websocket: WebSocket):
    """Обработка WebSocket соединения (основной цикл)."""
    client_id = "unknown"

    try:
        await handler.websocket_manager.connect(websocket, client_id)
        handler.stats_service.on_connect()

        # Основной цикл обработки сообщений
        while True:
            data = await websocket.receive_text()
            logger.debug(f"📥 Получено сообщение от {client_id}: {len(data)} байт")

            # Проверка размера
            if not handler.stats_service.check_size_ok(data):
                logger.warning(f"🔒 Закрытие {client_id}: превышен размер сообщения")
                try:
                    await websocket.close(code=1009, reason="Message too big")
                finally:
                    break

            # Проверка частоты
            if not handler.stats_service.check_rate_ok(client_id):
                logger.warning(f"🔒 Закрытие {client_id}: превышен лимит сообщений в минуту")
                try:
                    await websocket.close(code=1011, reason="Rate limit exceeded")
                finally:
                    break

            # Обработка незашифрованных сообщений (request_secrets/register)
            try:
                try_json: Any = json.loads(data)

                if isinstance(try_json, dict) and try_json.get("type") == "request_secrets":
                    logger.info(f"🔐 Получен безопасный запрос секретов от {client_id}")
                    await handler._handle_request_secrets(websocket, try_json, client_id)
                    break

                if isinstance(try_json, dict) and try_json.get("type") == "register":
                    logger.info(f"📝 Получена незашифрованная регистрация от {client_id} (первоначальная синхронизация)")
                    new_client_id = await handler._handle_registration(websocket, try_json, skip_secrets_send=True)

                    if new_client_id and new_client_id != "unknown":
                        await handler.websocket_manager.update_client_id(client_id, new_client_id)
                        client_id = new_client_id
                        handler.websocket_manager.update_metadata(
                            client_id,
                            {"registered": True, "registered_at": time.time()},
                        )

                        # Enrollment/auto-approve
                        if not handler.enrollments.is_trusted(client_id):
                            try:
                                data_dict = try_json.get("data", {})
                                handler.enrollments.add_pending(client_id, data_dict)
                                auto_approve = str(os.getenv("AUTO_APPROVE_ENROLLMENTS", "true")).lower() in (
                                    "1",
                                    "true",
                                    "yes",
                                )
                                logger.info(f"🔍 AUTO_APPROVE_ENROLLMENTS={auto_approve} для клиента {client_id}")
                                if auto_approve:
                                    handler.enrollments.approve(client_id)
                                    logger.info(f"✅ Клиент {client_id} автоматически утвержден")
                                    await websocket.send_text(
                                        json.dumps(
                                            {
                                                "type": "registration_success",
                                                "client_id": client_id,
                                                "message": "Клиент успешно зарегистрирован (auto-approve)",
                                                "secrets_version": handler.secrets_sync.get_version(),
                                            }
                                        )
                                    )
                                    await websocket.send_text(
                                        json.dumps(
                                            {
                                                "type": "secrets_needed",
                                                "message": "Отправьте 'request_secrets' с вашим публичным RSA ключом для получения секретов",
                                            }
                                        )
                                    )
                                else:
                                    logger.info(f"⏳ Клиент {client_id} ожидает утверждения")
                                    await websocket.send_text(
                                        json.dumps({"type": "enrollment_pending", "data": {"client_id": client_id}})
                                    )
                            except Exception as e:  # pragma: no cover - защитный лог
                                logger.error(f"❌ Ошибка обработки enrollment для {client_id}: {e}")

                        # Мониторинг
                        ws_mon_disable = str(os.getenv("WS_MONITOR_DISABLE", "true")).lower() in ("1", "true", "yes")
                        if not ws_mon_disable and handler.enrollments.is_trusted(client_id):
                            await asyncio.sleep(0.5)
                            await handler.websocket_manager.start_monitoring(client_id)
                        continue
                    else:
                        logger.warning(f"⚠️ Регистрация не удалась для {client_id}")
                        break
            except (json.JSONDecodeError, ValueError, KeyError):
                pass

            # Обычная обработка: дешифрование и маршрутизация
            try:
                message = await handler.encryption_service.decrypt_message(data, client_id)
            except Exception as sec_err:
                error_msg = str(sec_err)
                is_hmac_error = "HMAC mismatch" in error_msg or "Unencrypted WebSocket message" in error_msg

                if client_id == "unknown" and is_hmac_error:
                    logger.warning("⚠️ HMAC mismatch при первом подключении - клиент использует неверные ключи")
                    logger.info("🔐 Ожидаем запрос секретов с публичным ключом для безопасной передачи")

                    error_response = {
                        "type": "secrets_needed",
                        "message": "Требуется синхронизация секретов. Отправьте незашифрованное сообщение 'request_secrets' с вашим публичным RSA ключом (PEM формат) в поле 'data.public_key'.",
                    }
                    try:
                        await websocket.send_text(json.dumps(error_response))
                        await asyncio.sleep(0.1)
                    except Exception:
                        pass
                    continue

                reason = f"Security error: {sec_err}"
                logger.warning(f"🔒 Отклонено сообщение и закрыто соединение {client_id}: {reason}")
                try:
                    await websocket.close(code=1008, reason="Policy violation: invalid encryption/HMAC")
                except Exception:
                    pass
                break

            logger.debug(f"📥 Обработанное сообщение: {message}")

            if message.get("type") == "register":
                old_client_id = client_id
                new_client_id = await handler._handle_registration(websocket, message)
                if new_client_id != "unknown":
                    if old_client_id == "unknown":
                        handler.encryption_service.migrate_unknown_to_registered(old_client_id, new_client_id)
                    await handler.websocket_manager.update_client_id(old_client_id, new_client_id)
                    client_id = new_client_id
                    handler.websocket_manager.update_metadata(
                        client_id,
                        {"registered": True, "registered_at": time.time()},
                    )
            else:
                await handler.message_router.route_message(websocket, message, client_id)

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket отключен: {client_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка WebSocket: {e}", exc_info=True)
    finally:
        if client_id != "unknown":
            try:
                paused_count = await handler.transfers.pause_all_for_client(client_id)
                if paused_count > 0:
                    logger.debug(f"⏸️ Приостановлено активных трансферов: {paused_count} для клиента {client_id}")
            except Exception as e:  # pragma: no cover - защитный лог
                logger.error(f"❌ Ошибка паузы трансферов для {client_id}: {e}")
            await handler._cleanup_client(client_id)
        handler.stats_service.on_disconnect()


