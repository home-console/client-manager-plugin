"""
Сервис синхронизации секретов между сервером и клиентами
Обеспечивает централизованное управление ключами шифрования
"""

import time
import base64
import logging
from typing import Dict, Optional
from .security.encryption_service import EncryptionService

logger = logging.getLogger(__name__)


class SecretsSyncService:
    """Сервис синхронизации секретов между сервером и клиентами"""
    
    def __init__(self, encryption_service: EncryptionService, websocket_handler=None):
        self.encryption_service = encryption_service
        self.websocket_handler = websocket_handler
        # Версия секретов для отслеживания изменений (начинаем с 1)
        self.secrets_version = 1
        self.secrets_history: Dict[int, Dict] = {}  # История версий для отката
        logger.info(f"🔐 SecretsSyncService инициализирован, версия секретов: {self.secrets_version}")
    
    def get_current_secrets(self) -> Dict:
        """Получить текущие секреты"""
        return {
            "encryption_key": self.encryption_service.encryption_key,
            "encryption_salt": base64.b64encode(self.encryption_service.salt).decode(),
            "version": self.secrets_version,
            "timestamp": int(time.time())
        }
    
    async def send_secrets_to_client(self, client_id: str):
        """Отправить секреты конкретному клиенту"""
        if not self.websocket_handler:
            logger.error("WebSocketHandler не установлен, невозможно отправить секреты")
            return False
        
        secrets = self.get_current_secrets()
        message = {
            "type": "secrets_config",
            "data": secrets
        }
        
        try:
            encrypted = await self.encryption_service.encrypt_message(message, client_id)
            await self.websocket_handler.websocket_manager.send_message(client_id, encrypted)
            logger.info(f"✅ Секреты отправлены клиенту {client_id} (версия {self.secrets_version})")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка отправки секретов клиенту {client_id}: {e}")
            return False
    
    async def rotate_secrets(self, new_key: str, new_salt: bytes):
        """Ротация секретов с уведомлением всех клиентов"""
        # Сохраняем старую версию в историю
        old_secrets = self.get_current_secrets()
        self.secrets_history[self.secrets_version] = old_secrets
        
        # Обновляем секреты в EncryptionService
        if not self._update_encryption_service_secrets(new_key, new_salt):
            logger.error("❌ Не удалось обновить секреты в EncryptionService")
            return False
        
        self.secrets_version += 1
        
        logger.warning(f"🔄 РОТАЦИЯ СЕКРЕТОВ: версия {self.secrets_version}")
        logger.info(f"   Старая версия {self.secrets_version - 1} сохранена в истории")
        
        # Уведомляем всех клиентов
        await self._broadcast_secrets_update()
        
        return True
    
    def _update_encryption_service_secrets(self, new_key: str, new_salt: bytes) -> bool:
        """Обновить секреты в EncryptionService"""
        try:
            # Используем метод update_key если доступен, иначе обновляем напрямую
            if hasattr(self.encryption_service, 'update_key'):
                self.encryption_service.update_key(new_key, new_salt)
            else:
                # Fallback: обновляем напрямую
                self.encryption_service.encryption_key = new_key
                self.encryption_service.salt = new_salt
                
                # Перевычисляем производный ключ
                from app.utils.encryption import derive_key
                self.encryption_service._encryption_key = derive_key(new_key, new_salt)
                
                # Сбрасываем состояния шифрования для всех клиентов (новый ключ)
                self.encryption_service.encryption_states.clear()
            
            logger.info("✅ Секреты обновлены в EncryptionService, состояния сброшены")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка обновления секретов: {e}")
            return False
    
    async def _broadcast_secrets_update(self):
        """Отправка обновления всем подключенным клиентам"""
        if not self.websocket_handler:
            logger.warning("WebSocketHandler не установлен, невозможно уведомить клиентов")
            return
        
        secrets = self.get_current_secrets()
        update_msg = {
            "type": "secrets_update",
            "data": {
                **secrets,
                "requires_reconnect": True  # Требуется переподключение для применения новых ключей
            }
        }
        
        try:
            clients = self.websocket_handler.client_manager.get_all_clients()
            updated = 0
            failed = 0
            
            for client_id in clients.keys():
                try:
                    encrypted = await self.encryption_service.encrypt_message(update_msg, client_id)
                    success = await self.websocket_handler.websocket_manager.send_message(client_id, encrypted)
                    if success:
                        updated += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки обновления клиенту {client_id}: {e}")
                    failed += 1
            
            logger.info(f"📢 Обновление секретов: отправлено {updated} клиентам, ошибок: {failed}")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при рассылке обновления секретов: {e}")
    
    def check_client_version(self, client_version: int) -> bool:
        """Проверить, актуальна ли версия секретов клиента"""
        return client_version >= self.secrets_version
    
    def get_version(self) -> int:
        """Получить текущую версию секретов"""
        return self.secrets_version
    
    def set_websocket_handler(self, handler):
        """Установить WebSocketHandler для отправки сообщений"""
        self.websocket_handler = handler
        logger.info("✅ WebSocketHandler установлен в SecretsSyncService")

