"""
Сервис шифрования для E2E защиты
"""

import base64
import hmac as hmac_module
import json
import logging
import os
from typing import Dict, Optional

from ...utils.encryption import derive_key, encrypt_aes_gcm, decrypt_aes_gcm, compute_hmac

logger = logging.getLogger(__name__)



class EncryptionService:
    """Сервис шифрования для E2E защиты"""
    
    def __init__(self, encryption_key: str = None, salt: bytes = None):
        # Ключ шифрования обязателен: без него работа запрещена
        self.encryption_key = encryption_key or os.getenv("SERVER_ENCRYPTION_KEY")
        salt_env = os.getenv("SERVER_ENCRYPTION_SALT")
        if salt is not None:
            self.salt = salt
        elif salt_env:
            try:
                import base64 as _b64
                self.salt = _b64.b64decode(salt_env)
            except Exception:
                self.salt = salt_env.encode("utf-8")
        else:
            self.salt = None
        
        if not self.encryption_key or not self.salt:
            raise RuntimeError("SERVER_ENCRYPTION_KEY and SERVER_ENCRYPTION_SALT must be set for EncryptionService")
        
        self._encryption_key = derive_key(self.encryption_key, self.salt)
        
        # Состояние шифрования для каждого клиента
        self.encryption_states: Dict[str, Dict[str, int]] = {}
        
        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ P0-5: Replay protection для unknown клиентов
        # Временное хранилище sequence numbers для unknown клиентов (до регистрации)
        # Ключ: (client_id, connection_start_time) для уникальности соединений
        self.unknown_client_seqs: Dict[str, Dict[str, int]] = {}
    
    def is_encryption_enabled(self) -> bool:
        """Проверить, включено ли шифрование"""
        return self._encryption_key is not None
    
    def get_encryption_state(self, client_id: str) -> Dict[str, int]:
        """Получить состояние шифрования для клиента"""
        if client_id not in self.encryption_states:
            self.encryption_states[client_id] = {"seq_out": 0, "seq_in": 0}
        return self.encryption_states[client_id]
    
    def update_encryption_state(self, client_id: str, state: Dict[str, int]):
        """Обновить состояние шифрования для клиента"""
        self.encryption_states[client_id] = state
    
    def reset_encryption_state(self, client_id: str):
        """Сбросить состояние шифрования для клиента"""
        self.encryption_states[client_id] = {"seq_out": 0, "seq_in": 0}
        logger.info(f"🔄 Сброшено состояние шифрования для клиента {client_id}")
    
    async def encrypt_message(self, message: dict, client_id: str) -> str:
        """Шифрование сообщения"""
        if not self._encryption_key:
            return json.dumps(message)
        
        # Получаем состояние шифрования
        state = self.get_encryption_state(client_id)
        state["seq_out"] = state.get("seq_out", 0) + 1
        
        # Добавляем последовательный номер
        if "data" not in message or message["data"] is None:
            message["data"] = {}
        message["data"]["_seq"] = state["seq_out"]
        
        # Шифруем сообщение
        plaintext = json.dumps(message).encode("utf-8")
        payload_enc = encrypt_aes_gcm(self._encryption_key, plaintext)
        hmac_val = compute_hmac(self._encryption_key, payload_enc)
        
        # Создаем обертку
        wrapper = {
            "payload": base64.b64encode(payload_enc).decode(),
            "hmac": base64.b64encode(hmac_val).decode()
        }
        
        return json.dumps(wrapper)
    
    async def decrypt_message(self, data: str, client_id: str) -> dict:
        """Дешифрование сообщения"""
        if not self._encryption_key:
            return json.loads(data)
        
        try:
            wrapper = json.loads(data)
            if "payload" not in wrapper or "hmac" not in wrapper:
                # При включенном шифровании принимаем только зашифрованные сообщения
                raise ValueError("Unencrypted WebSocket message is not allowed when encryption is enabled")
            
            # Инициализируем состояние если еще нет
            if client_id not in self.encryption_states:
                self.encryption_states[client_id] = {"seq_out": 0, "seq_in": 0}
            
            # Извлекаем данные
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
            message = json.loads(plaintext.decode("utf-8"))
            
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ P0-5: Replay protection для всех клиентов, включая unknown
            if "_seq" in message.get("data", {}):
                seq = int(message["data"]["_seq"])
                
                if client_id != "unknown":
                    # Зарегистрированные клиенты - используем обычный механизм
                    state = self.get_encryption_state(client_id)
                    
                    if seq <= state.get("seq_in", 0):
                        raise ValueError("Replay detected")
                    
                    state["seq_in"] = seq
                else:
                    # Unknown клиенты - используем временное хранилище
                    # Используем IP адрес + timestamp соединения как ключ (из metadata если доступно)
                    # Или просто "unknown" с проверкой по последовательности
                    unknown_key = f"unknown_{client_id}"
                    
                    if unknown_key not in self.unknown_client_seqs:
                        self.unknown_client_seqs[unknown_key] = {"seq_in": 0}
                    
                    unknown_state = self.unknown_client_seqs[unknown_key]
                    
                    # Проверка replay для unknown клиента
                    if seq <= unknown_state.get("seq_in", 0):
                        logger.warning(f"⚠️ Replay detected для unknown клиента (seq={seq} <= last_seq={unknown_state.get('seq_in', 0)})")
                        raise ValueError("Replay detected")
                    
                    unknown_state["seq_in"] = seq
                    logger.debug(f"✅ Replay protection для unknown клиента: seq={seq}")
                
                del message["data"]["_seq"]
            
            return message
            
        except Exception as e:
            # Строгий режим: при ошибке дешифрования/HMAC отвергаем сообщение
            logger.warning(f"Ошибка дешифрования/WebSocket безопасности: {e}")
            raise
    
    def migrate_unknown_to_registered(self, unknown_client_id: str, registered_client_id: str):
        """
        КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ P0-5: Миграция sequence numbers от unknown к зарегистрированному клиенту
        
        При регистрации клиента переносим его sequence state из временного хранилища
        в постоянное, чтобы сохранить защиту от replay атак.
        """
        unknown_key = f"unknown_{unknown_client_id}"
        
        if unknown_key in self.unknown_client_seqs:
            unknown_state = self.unknown_client_seqs[unknown_key]
            seq_in = unknown_state.get("seq_in", 0)
            
            # Инициализируем состояние для зарегистрированного клиента
            if registered_client_id not in self.encryption_states:
                self.encryption_states[registered_client_id] = {"seq_out": 0, "seq_in": 0}
            
            # Переносим seq_in из unknown в зарегистрированного
            self.encryption_states[registered_client_id]["seq_in"] = seq_in
            
            # Удаляем временную запись
            del self.unknown_client_seqs[unknown_key]
            
            logger.debug(f"✅ Мигрированы sequence numbers от unknown ({unknown_client_id}) к зарегистрированному ({registered_client_id}): seq_in={seq_in}")
        else:
            # Если не было unknown записи - просто инициализируем с нуля
            if registered_client_id not in self.encryption_states:
                self.encryption_states[registered_client_id] = {"seq_out": 0, "seq_in": 0}
                logger.debug(f"✅ Инициализировано состояние sequence numbers для нового клиента {registered_client_id}")
    
    def cleanup_client(self, client_id: str):
        """Очистка данных шифрования для клиента"""
        if client_id in self.encryption_states:
            del self.encryption_states[client_id]
            logger.debug(f"Очищены данные шифрования для клиента {client_id}")
        
        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ P0-5: Очистка временных sequence numbers для unknown клиентов
        unknown_key = f"unknown_{client_id}"
        if unknown_key in self.unknown_client_seqs:
            del self.unknown_client_seqs[unknown_key]
            logger.debug(f"Очищены временные sequence numbers для unknown клиента {client_id}")
    
    def update_key(self, new_key: str, new_salt: bytes):
        """Обновить ключ шифрования и соль (для ротации)"""
        self.encryption_key = new_key
        self.salt = new_salt
        self._encryption_key = derive_key(new_key, new_salt)
        # Очищаем состояния шифрования при смене ключа
        self.encryption_states.clear()
        logger.info("✅ Ключ шифрования обновлен, состояния сброшены")
    
    def get_stats(self) -> dict:
        """Получить статистику шифрования"""
        return {
            "encryption_enabled": self.is_encryption_enabled(),
            "active_clients": len(self.encryption_states),
            "clients": list(self.encryption_states.keys()),
            "unknown_clients": len(self.unknown_client_seqs)  # P0-5: добавляем статистику unknown
        }

