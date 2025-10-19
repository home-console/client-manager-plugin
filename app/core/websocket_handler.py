"""
WebSocket обработчик для управления соединениями
"""

import asyncio
import base64
import hmac as hmac_module
import json
import logging
import os
import time
from typing import Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from .client_manager import ClientManager
from .command_handler import CommandHandler
from app.utils.encryption import derive_key, encrypt_aes_gcm, decrypt_aes_gcm, compute_hmac

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Обработчик WebSocket соединений"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self.client_manager = ClientManager()
        self.command_handler = CommandHandler()
        
        # E2E шифрование (PSK режим)
        self.encryption_key = os.getenv("SERVER_ENCRYPTION_KEY", "my-super-secret-encryption-key-2025")
        self.salt = b"remote-client-salt"
        
        if self.encryption_key:
            self._encryption_key = derive_key(self.encryption_key, self.salt)
        else:
            self._encryption_key = None
        
        self._initialized = True
    
    async def handle_websocket(self, websocket: WebSocket):
        """Обработка WebSocket соединения"""
        await websocket.accept()
        client_id = "unknown"
        
        try:
            while True:
                # Получение сообщения
                data = await websocket.receive_text()
                logger.debug(f"📥 Получено сообщение от {client_id}: {len(data)} байт")
                
                # Распаковка сообщения
                message = await self.unwrap_message(data, client_id)
                logger.debug(f"📥 Распакованное сообщение: {message}")
                
                # Обработка регистрации
                if message.get('type') == 'register':
                    client_id = await self.client_manager.register_client(websocket, message.get('data', {}))
                    logger.info(f"🔐 Регистрация клиента: {message.get('data', {})}")
                    
                    # Сбрасываем seq при новой регистрации (переподключение)
                    if client_id in self.client_manager.encryption_state:
                        self.client_manager.encryption_state[client_id] = {"seq_out": 0, "seq_in": 0}
                        logger.info(f"🔄 Сброшены seq для клиента {client_id}")
                    
                    # Отправляем подтверждение регистрации
                    response = {
                        "type": "registration_success",
                        "client_id": client_id,
                        "message": "Клиент успешно зарегистрирован"
                    }
                    wrapped = await self.wrap_message(response, client_id)
                    logger.info(f"📤 Отправка registration_success клиенту {client_id}, длина: {len(wrapped)}")
                    await websocket.send_text(wrapped)
                    logger.info(f"✅ Ответ отправлен")
                
                # Обработка остальных сообщений
                await self.command_handler.handle_client_message(websocket, message, client_id)
                
        except WebSocketDisconnect:
            logger.info(f"🔌 WebSocket отключен: {client_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка WebSocket: {e}")
        finally:
            if client_id != "unknown":
                await self.client_manager.unregister_client(client_id)
    
    async def unwrap_message(self, data: str, client_id: str) -> dict:
        """Распаковка зашифрованного сообщения"""
        if not self._encryption_key:
            return json.loads(data)
        try:
            wrapper = json.loads(data)
            if "payload" not in wrapper or "hmac" not in wrapper:
                # Нешифрованное — разрешаем до регистрации
                return wrapper
            # Инициализируем состояние если еще нет
            if client_id not in self.client_manager.encryption_state:
                self.client_manager.encryption_state[client_id] = {"seq_out": 0, "seq_in": 0}
            payload_b64 = wrapper["payload"]
            hmac_b64 = wrapper["hmac"]
            payload_enc = base64.b64decode(payload_b64)
            hmac_recv = base64.b64decode(hmac_b64)
            # Проверка HMAC
            hmac_calc = compute_hmac(self._encryption_key, payload_enc)
            if not hmac_module.compare_digest(hmac_calc, hmac_recv):
                raise ValueError("HMAC mismatch")
            # Дешифровка
            plaintext = decrypt_aes_gcm(self._encryption_key, payload_enc)
            msg = json.loads(plaintext.decode("utf-8"))
            # Проверка seq (только для зарегистрированных клиентов)
            if "_seq" in msg.get("data", {}) and client_id != "unknown":
                seq = int(msg["data"]["_seq"])
                state = self.client_manager.encryption_state.get(client_id, {})
                if seq <= state.get("seq_in", 0):
                    raise ValueError("Replay detected")
                state["seq_in"] = seq
                del msg["data"]["_seq"]
            elif "_seq" in msg.get("data", {}):
                # Для unknown просто удаляем _seq
                del msg["data"]["_seq"]
            return msg
        except Exception as e:
            logger.warning(f"Unwrap failed: {e}, treating as plaintext")
            return json.loads(data)
    
    async def wrap_message(self, msg: dict, client_id: str) -> str:
        """Упаковка сообщения в шифрованную обёртку"""
        if not self._encryption_key:
            return json.dumps(msg)
        # Инициализируем состояние если еще нет
        if client_id not in self.client_manager.encryption_state:
            self.client_manager.encryption_state[client_id] = {"seq_out": 0, "seq_in": 0}
        state = self.client_manager.encryption_state[client_id]
        state["seq_out"] = state.get("seq_out", 0) + 1
        if "data" not in msg or msg["data"] is None:
            msg["data"] = {}
        msg["data"]["_seq"] = state["seq_out"]
        plaintext = json.dumps(msg).encode("utf-8")
        payload_enc = encrypt_aes_gcm(self._encryption_key, plaintext)
        hmac_val = compute_hmac(self._encryption_key, payload_enc)
        wrapper = {
            "payload": base64.b64encode(payload_enc).decode(),
            "hmac": base64.b64encode(hmac_val).decode()
        }
        return json.dumps(wrapper)
    
    async def send_command_to_client(self, client_id: str, command: str, command_id: str = None):
        """Отправка команды клиенту"""
        websocket = self.client_manager.get_client(client_id)
        if not websocket:
            logger.error(f"❌ Клиент {client_id} не найден")
            return False
        
        if not command_id:
            command_id = f"cmd_{int(time.time())}"
        
        # Создаем сообщение команды
        command_name = ""
        command_args = []
        
        if isinstance(command, dict):
            command_name = command.get("name", "")
            params = command.get("params", {})
            if isinstance(params, dict):
                command_args = [f"{k}={v}" for k, v in params.items()]
            elif isinstance(params, list):
                command_args = [str(p) for p in params]
            else:
                command_args = [str(params)]
        else:
            command_name = str(command)
        
        command_msg = {
            "type": "command",
            "command": command_name,
            "args": command_args,
            "command_id": command_id
        }
        
        # Отправляем зашифрованное сообщение
        wrapped = await self.wrap_message(command_msg, client_id)
        await websocket.send_text(wrapped)
        
        logger.info(f"📤 Команда отправлена клиенту {client_id}: {command_name}")
        return True
    
    async def send_cancel_to_client(self, client_id: str, command_id: str):
        """Отправка отмены команды клиенту"""
        websocket = self.client_manager.get_client(client_id)
        if not websocket:
            logger.error(f"❌ Клиент {client_id} не найден")
            return False
        
        cancel_msg = {
            "type": "cancel",
            "command_id": command_id
        }
        
        # Отправляем зашифрованное сообщение
        wrapped = await self.wrap_message(cancel_msg, client_id)
        await websocket.send_text(wrapped)
        
        logger.info(f"📤 Отмена команды отправлена клиенту {client_id}: {command_id}")
        return True
