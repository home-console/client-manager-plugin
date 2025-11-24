"""
Интеграция с облачными сервисами для умного дома
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, BinaryIO

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None

try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False
    aiofiles = None

try:
    import json
except ImportError:
    json = None

logger = logging.getLogger(__name__)


class CloudService(ABC):
    """Базовый класс для облачных сервисов"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for cloud services")
        self.session = httpx.AsyncClient(timeout=30.0)

    @abstractmethod
    async def authenticate(self) -> bool:
        """Аутентификация в сервисе"""
        pass

    @abstractmethod
    async def upload_file(self, file_data: bytes, remote_path: str) -> Dict[str, Any]:
        """Загрузка файла в облако"""
        pass

    @abstractmethod
    async def download_file(self, remote_path: str) -> bytes:
        """Скачивание файла из облака"""
        pass

    @abstractmethod
    async def list_files(self, remote_path: str = "") -> list:
        """Получение списка файлов"""
        pass

    @abstractmethod
    async def delete_file(self, remote_path: str) -> bool:
        """Удаление файла из облака"""
        pass

    async def close(self):
        """Закрытие соединения"""
        await self.session.aclose()


class YandexDiskService(CloudService):
    """Интеграция с Яндекс.Диск"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.token = config.get("token", "")
        self.base_url = "https://cloud-api.yandex.net/v1/disk"

    async def authenticate(self) -> bool:
        """Проверка токена"""
        if not self.token:
            return False

        try:
            response = await self.session.get(
                f"{self.base_url}/",
                headers={"Authorization": f"OAuth {self.token}"}
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Ошибка аутентификации Яндекс.Диск: {e}")
            return False

    async def upload_file(self, file_data: bytes, remote_path: str) -> Dict[str, Any]:
        """Загрузка файла на Яндекс.Диск"""
        try:
            # Получаем ссылку для загрузки
            upload_url = await self._get_upload_url(remote_path)

            # Загружаем файл
            response = await self.session.put(upload_url, content=file_data)

            if response.status_code in [201, 202]:
                return {
                    "success": True,
                    "path": remote_path,
                    "size": len(file_data)
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text}"
                }

        except Exception as e:
            logger.error(f"Ошибка загрузки на Яндекс.Диск: {e}")
            return {"success": False, "error": str(e)}

    async def download_file(self, remote_path: str) -> bytes:
        """Скачивание файла с Яндекс.Диск"""
        try:
            # Получаем ссылку для скачивания
            download_url = await self._get_download_url(remote_path)

            # Скачиваем файл
            response = await self.session.get(download_url)
            response.raise_for_status()

            return response.content

        except Exception as e:
            logger.error(f"Ошибка скачивания с Яндекс.Диск: {e}")
            raise

    async def _get_upload_url(self, remote_path: str) -> str:
        """Получение URL для загрузки"""
        response = await self.session.get(
            f"{self.base_url}/resources/upload",
            headers={"Authorization": f"OAuth {self.token}"},
            params={
                "path": remote_path,
                "overwrite": "true"
            }
        )
        response.raise_for_status()

        data = response.json()
        return data["href"]

    async def _get_download_url(self, remote_path: str) -> str:
        """Получение URL для скачивания"""
        response = await self.session.get(
            f"{self.base_url}/resources/download",
            headers={"Authorization": f"OAuth {self.token}"},
            params={"path": remote_path}
        )
        response.raise_for_status()

        data = response.json()
        return data["href"]

    async def list_files(self, remote_path: str = "") -> list:
        """Получение списка файлов"""
        try:
            response = await self.session.get(
                f"{self.base_url}/resources",
                headers={"Authorization": f"OAuth {self.token}"},
                params={"path": remote_path}
            )
            response.raise_for_status()

            data = response.json()
            return data.get("_embedded", {}).get("items", [])
        except Exception as e:
            logger.error(f"Ошибка получения списка файлов: {e}")
            return []

    async def delete_file(self, remote_path: str) -> bool:
        """Удаление файла"""
        try:
            response = await self.session.delete(
                f"{self.base_url}/resources",
                headers={"Authorization": f"OAuth {self.token}"},
                params={"path": remote_path, "permanently": "true"}
            )
            return response.status_code == 204
        except Exception as e:
            logger.error(f"Ошибка удаления файла: {e}")
            return False


class ICloudService(CloudService):
    """Интеграция с iCloud"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.base_url = "https://api.icloud.com"

    async def authenticate(self) -> bool:
        """Аутентификация в iCloud"""
        # iCloud использует более сложную аутентификацию
        # Здесь должна быть реализация через Apple ID
        logger.warning("iCloud интеграция пока не реализована")
        return False

    async def upload_file(self, file_data: bytes, remote_path: str) -> Dict[str, Any]:
        """Загрузка файла в iCloud"""
        return {"success": False, "error": "iCloud интеграция не реализована"}

    async def download_file(self, remote_path: str) -> bytes:
        """Скачивание файла из iCloud"""
        raise NotImplementedError("iCloud интеграция не реализована")

    async def list_files(self, remote_path: str = "") -> list:
        """Получение списка файлов"""
        return []

    async def delete_file(self, remote_path: str) -> bool:
        """Удаление файла из iCloud"""
        return False


class CloudServiceFactory:
    """Фабрика облачных сервисов"""

    @staticmethod
    def create_service(service_type: str, config: Dict[str, Any]) -> CloudService:
        """Создание экземпляра облачного сервиса"""
        if service_type == "yandex_disk":
            return YandexDiskService(config)
        elif service_type == "icloud":
            return ICloudService(config)
        elif service_type == "google_drive":
            # return GoogleDriveService(config)
            raise NotImplementedError("Google Drive не реализован")
        elif service_type == "dropbox":
            # return DropboxService(config)
            raise NotImplementedError("Dropbox не реализован")
        else:
            raise ValueError(f"Неизвестный тип сервиса: {service_type}")


class CloudManager:
    """Менеджер облачных сервисов"""

    def __init__(self):
        self.services: Dict[str, CloudService] = {}
        self.auth_service_url = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8000")
        self.internal_token = os.getenv("INTERNAL_SERVICE_TOKEN", "internal-service-token")
        self._load_services()

    def _load_services(self):
        """Загрузка настроек облачных сервисов через auth сервис"""
        # Сначала пробуем получить токены из auth сервиса
        auth_tokens = self._get_tokens_from_auth_service()

        # Яндекс.Диск
        if yandex_token := auth_tokens.get("yandex_disk") or os.getenv("YANDEX_DISK_TOKEN"):
            try:
                self.services["yandex_disk"] = CloudServiceFactory.create_service(
                    "yandex_disk",
                    {"token": yandex_token}
                )
                logger.info("✅ Яндекс.Диск подключен")
            except Exception as e:
                logger.error(f"❌ Ошибка подключения Яндекс.Диска: {e}")

        # iCloud
        if icloud_creds := auth_tokens.get("icloud") or {
            "username": os.getenv("ICLOUD_USERNAME"),
            "password": os.getenv("ICLOUD_PASSWORD", "")
        }:
            if icloud_creds.get("username"):
                try:
                    self.services["icloud"] = CloudServiceFactory.create_service(
                        "icloud",
                        icloud_creds
                    )
                    logger.info("✅ iCloud подключен")
                except Exception as e:
                    logger.error(f"❌ Ошибка подключения iCloud: {e}")

        logger.info(f"Загружено {len(self.services)} облачных сервисов")

    def _get_tokens_from_auth_service(self) -> Dict[str, Any]:
        """Получение токенов из auth сервиса"""
        try:
            if not HTTPX_AVAILABLE:
                logger.warning("httpx не доступен, токены не получены из auth сервиса")
                return {}

            # Получаем токены для cloud сервисов
            response = httpx.get(
                f"{self.auth_service_url}/api/tokens/cloud",
                headers={"Authorization": f"Bearer {self.internal_token}"},
                timeout=5.0
            )

            if response.status_code == 200:
                tokens = response.json()
                logger.info(f"Получено {len(tokens)} токенов из auth сервиса")
                return tokens
            else:
                logger.warning(f"Не удалось получить токены из auth сервиса: {response.status_code}")
                return {}

        except Exception as e:
            logger.warning(f"Ошибка получения токенов из auth сервиса: {e}")
            # Fallback на переменные окружения
            return {}

    async def get_service(self, service_type: str) -> Optional[CloudService]:
        """Получение сервиса по типу"""
        service = self.services.get(service_type)
        if service:
            # Проверяем аутентификацию
            if await service.authenticate():
                return service
            else:
                logger.error(f"Не удалось аутентифицироваться в {service_type}")
                return None
        return None

    async def upload_to_cloud(self, service_type: str, file_data: bytes, remote_path: str) -> Dict[str, Any]:
        """Загрузка файла в облако"""
        service = await self.get_service(service_type)
        if not service:
            return {"success": False, "error": f"Сервис {service_type} недоступен"}

        return await service.upload_file(file_data, remote_path)

    async def download_from_cloud(self, service_type: str, remote_path: str) -> Optional[bytes]:
        """Скачивание файла из облака"""
        service = await self.get_service(service_type)
        if not service:
            return None

        return await service.download_file(remote_path)

    async def list_cloud_files(self, service_type: str, remote_path: str = "") -> list:
        """Получение списка файлов в облаке"""
        service = await self.get_service(service_type)
        if not service:
            return []

        return await service.list_files(remote_path)

    async def delete_from_cloud(self, service_type: str, remote_path: str) -> bool:
        """Удаление файла из облака"""
        service = await self.get_service(service_type)
        if not service:
            return False

        return await service.delete_file(remote_path)

    async def close_all(self):
        """Закрытие всех соединений"""
        for service in self.services.values():
            await service.close()


# Глобальный менеджер облачных сервисов
cloud_manager = CloudManager()
