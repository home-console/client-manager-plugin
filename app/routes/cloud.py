"""
Роуты для интеграции с облачными сервисами
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from pydantic import BaseModel

# Импорт cloud_services с проверкой
try:
    from ..core.cloud_services import cloud_manager
    CLOUD_SERVICES_AVAILABLE = True
except ImportError:
    CLOUD_SERVICES_AVAILABLE = False
    cloud_manager = None
from ..dependencies import get_websocket_handler

logger = logging.getLogger(__name__)

router = APIRouter()


class CloudUploadRequest(BaseModel):
    """Запрос на загрузку в облако"""
    device_id: str
    remote_path: str  # путь на устройстве
    cloud_service: str  # yandex_disk, icloud, etc.
    cloud_path: str  # путь в облаке
    delete_after: bool = False  # удалить с устройства после загрузки


class CloudDownloadRequest(BaseModel):
    """Запрос на скачивание из облака"""
    device_id: str
    local_path: str  # путь на устройстве для сохранения
    cloud_service: str
    cloud_path: str  # путь в облаке


@router.post("/upload")
async def upload_to_cloud(
    request: CloudUploadRequest,
    handler = Depends(get_websocket_handler)
):
    if not CLOUD_SERVICES_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Облачные сервисы недоступны. Установите зависимости: pip install httpx"
        )
    """
    Загрузить файл с устройства в облако

    **Процесс:**
    1. Скачиваем файл с устройства через агента
    2. Загружаем файл в выбранный облачный сервис
    3. Опционально удаляем файл с устройства

    **Пример:**
    ```json
    {
      "device_id": "home-server",
      "remote_path": "/home/user/document.pdf",
      "cloud_service": "yandex_disk",
      "cloud_path": "/documents/important.pdf",
      "delete_after": true
    }
    ```
    """
    try:
        # Проверяем доступность httpx для облачных сервисов
        try:
            import httpx
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail="Облачные сервисы недоступны: требуется установить httpx (pip install httpx)"
            )

        # 1. Скачиваем файл с устройства
        logger.info(f"Скачиваем файл с устройства {request.device_id}: {request.remote_path}")
        file_data = await handler.download_file_from_device(request.device_id, request.remote_path)

        # 2. Загружаем в облако
        logger.info(f"Загружаем в {request.cloud_service}: {request.cloud_path}")
        result = await cloud_manager.upload_to_cloud(
            request.cloud_service,
            file_data,
            request.cloud_path
        )

        if not result["success"]:
            raise HTTPException(status_code=500, detail=f"Ошибка загрузки в облако: {result.get('error')}")

        # 3. Опционально удаляем с устройства
        if request.delete_after:
            logger.info(f"Удаляем файл с устройства: {request.remote_path}")
            await handler.delete_file_on_device(request.device_id, request.remote_path)

        return {
            "status": "uploaded",
            "cloud_service": request.cloud_service,
            "cloud_path": request.cloud_path,
            "file_size": len(file_data),
            "deleted_from_device": request.delete_after
        }

    except Exception as e:
        logger.error(f"Ошибка загрузки в облако: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки в облако: {str(e)}")


@router.post("/download")
async def download_from_cloud(
    request: CloudDownloadRequest,
    handler = Depends(get_websocket_handler)
):
    if not CLOUD_SERVICES_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Облачные сервисы недоступны. Установите зависимости: pip install httpx"
        )
    """
    Скачать файл из облака на устройство

    **Процесс:**
    1. Скачиваем файл из облачного сервиса
    2. Передаем файл на устройство через агента
    3. Сохраняем файл на устройстве

    **Пример:**
    ```json
    {
      "device_id": "laptop",
      "local_path": "/home/user/downloads/report.pdf",
      "cloud_service": "yandex_disk",
      "cloud_path": "/documents/report.pdf"
    }
    ```
    """
    try:
        # 1. Скачиваем из облака
        logger.info(f"Скачиваем из {request.cloud_service}: {request.cloud_path}")
        file_data = await cloud_manager.download_from_cloud(request.cloud_service, request.cloud_path)

        if file_data is None:
            raise HTTPException(status_code=404, detail="Файл не найден в облаке")

        # 2. Загружаем на устройство
        logger.info(f"Загружаем на устройство {request.device_id}: {request.local_path}")
        await handler.upload_file_to_device(request.device_id, request.local_path, file_data)

        return {
            "status": "downloaded",
            "cloud_service": request.cloud_service,
            "cloud_path": request.cloud_path,
            "device_id": request.device_id,
            "local_path": request.local_path,
            "file_size": len(file_data)
        }

    except Exception as e:
        logger.error(f"Ошибка скачивания из облака: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка скачивания из облака: {str(e)}")


@router.get("/services")
async def get_available_services():
    """
    Получить список доступных облачных сервисов

    **Возвращает:**
    Список сервисов с статусом подключения
    """
    services_status = []

    for service_type in ["yandex_disk", "icloud", "google_drive", "dropbox"]:
        service = await cloud_manager.get_service(service_type)
        services_status.append({
            "service": service_type,
            "available": service is not None,
            "authenticated": service is not None
        })

    return {
        "services": services_status,
        "total_available": sum(1 for s in services_status if s["available"])
    }


@router.get("/{service_type}/files")
async def list_cloud_files(
    service_type: str,
    path: str = Query("", description="Путь в облаке"),
    limit: int = Query(100, description="Максимальное количество файлов")
):
    """
    Получить список файлов в облаке

    **Параметры:**
    - `service_type`: тип сервиса (yandex_disk, icloud, etc.)
    - `path`: путь в облаке (пустой = корень)
    - `limit`: максимальное количество файлов
    """
    try:
        files = await cloud_manager.list_cloud_files(service_type, path)

        # Ограничиваем количество
        if len(files) > limit:
            files = files[:limit]

        return {
            "service": service_type,
            "path": path,
            "files": files,
            "count": len(files),
            "limit": limit
        }

    except Exception as e:
        logger.error(f"Ошибка получения списка файлов из {service_type}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка получения списка файлов: {str(e)}")


@router.delete("/{service_type}/files")
async def delete_cloud_file(
    service_type: str,
    path: str = Query(..., description="Путь к файлу в облаке")
):
    """
    Удалить файл из облака

    **Параметры:**
    - `service_type`: тип сервиса
    - `path`: путь к файлу в облаке
    """
    try:
        success = await cloud_manager.delete_from_cloud(service_type, path)

        if success:
            return {
                "status": "deleted",
                "service": service_type,
                "path": path
            }
        else:
            raise HTTPException(status_code=500, detail="Не удалось удалить файл")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка удаления файла из {service_type}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка удаления файла: {str(e)}")


@router.post("/sync")
async def sync_device_to_cloud(
    device_id: str,
    remote_path: str,
    cloud_service: str,
    cloud_path: str,
    recursive: bool = Query(True, description="Рекурсивная синхронизация"),
    delete_extra: bool = Query(False, description="Удалять лишние файлы в облаке"),
    handler = Depends(get_websocket_handler)
):
    """
    Синхронизировать директорию с устройства в облако

    **Параметры:**
    - `device_id`: ID устройства
    - `remote_path`: путь к директории на устройстве
    - `cloud_service`: тип облачного сервиса
    - `cloud_path`: путь в облаке
    - `recursive`: рекурсивная синхронизация
    - `delete_extra`: удалять файлы в облаке, которых нет на устройстве

    **Примечание:** Это базовая реализация. Для полной синхронизации
    может потребоваться более сложная логика сравнения файлов.
    """
    try:
        # Получаем список файлов на устройстве
        device_files = await handler.list_files_on_device(device_id, remote_path, recursive=recursive)

        # Получаем список файлов в облаке
        cloud_files = await cloud_manager.list_cloud_files(cloud_service, cloud_path)

        # Простая синхронизация: загружаем все файлы с устройства в облако
        uploaded_count = 0

        for file_info in device_files:
            if file_info["type"] == "file":
                # Скачиваем файл с устройства
                file_data = await handler.download_file_from_device(device_id, file_info["path"])

                # Определяем путь в облаке
                relative_path = file_info["path"].replace(remote_path, "").lstrip("/")
                cloud_file_path = f"{cloud_path.rstrip('/')}/{relative_path}"

                # Загружаем в облако
                result = await cloud_manager.upload_to_cloud(cloud_service, file_data, cloud_file_path)
                if result["success"]:
                    uploaded_count += 1

        return {
            "status": "synced",
            "device_id": device_id,
            "remote_path": remote_path,
            "cloud_service": cloud_service,
            "cloud_path": cloud_path,
            "uploaded_files": uploaded_count,
            "total_device_files": len([f for f in device_files if f["type"] == "file"])
        }

    except Exception as e:
        logger.error(f"Ошибка синхронизации: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка синхронизации: {str(e)}")


