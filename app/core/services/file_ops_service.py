from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Callable, Dict, Any, List

from ..config import settings
from ..transfers_manager import TransfersManager, TransferState
from ..file_transfer_handler import FileTransferHandler
from ..connection.websocket_manager import WebSocketManager
from ..security.encryption_service import EncryptionService
from ..models import FileInfo


class FileOpsService:
    """Операции скачивания/загрузки/листинга файлов через агента по WebSocket."""

    def __init__(
        self,
        transfers: TransfersManager,
        file_handler: FileTransferHandler,
        websocket_manager: WebSocketManager,
        encryption_service: EncryptionService,
        send_command_fn: Callable[..., asyncio.Future],
    ):
        self.transfers = transfers
        self.file_handler = file_handler
        self.websocket_manager = websocket_manager
        self.encryption_service = encryption_service
        self.send_command_fn = send_command_fn

    async def download_file_from_device(self, device_id: str, remote_path: str) -> bytes:
        # 1. Создаём трансфер (direction = download)
        transfer_id = await self.transfers.create_upload(device_id, remote_path, None, None, "download")
        await self.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)

        # 2. Временный путь на сервере
        tmpfd, tmp_path = tempfile.mkstemp(prefix=f"download_{transfer_id}_", suffix=".bin")
        os.close(tmpfd)
        self.transfers.transfers[transfer_id]["dest_path"] = tmp_path

        # 3. Сообщаем клиенту
        start_msg = {
            "type": "file_download_start",
            "data": {
                "transfer_id": transfer_id,
                "path": remote_path,
                "chunk_size": 1 << 20,
                "start_offset": 0,
            },
        }
        encrypted = await self.encryption_service.encrypt_message(start_msg, device_id)
        await self.websocket_manager.send_message(device_id, encrypted)

        # 4. Ждём завершения трансфера
        timeout = getattr(settings, "file_transfer_timeout", 120)
        try:
            await self.transfers.wait_for_state(transfer_id, [TransferState.COMPLETED], timeout=int(timeout))
        except KeyError:
            raise Exception("Трансфер удалён неожиданно")
        except TimeoutError:
            raise Exception("Таймаут ожидания завершения файлового трансфера")

        t = self.transfers.get(transfer_id)
        if not t:
            raise Exception("Трансфер удалён неожиданно")
        state = t.get("state")
        if state == TransferState.COMPLETED:
            with open(t.get("dest_path"), "rb") as f:
                return f.read()
        if state in (TransferState.FAILED, TransferState.CANCELLED):
            raise Exception(f"Transfer {transfer_id} ended with state {state}")
        raise Exception(f"Transfer {transfer_id} finished in unexpected state: {state}")

    async def upload_file_to_device(self, device_id: str, local_path: str, file_data: bytes):
        # 1. Создаём трансфер (direction = upload)
        transfer_id = await self.transfers.create_upload(device_id, local_path, len(file_data), None, "upload")
        await self.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)

        # 2. Запишем байты во временный файл и используем существующий код отправки
        fd, tmp_path = tempfile.mkstemp(prefix=f"upload_{transfer_id}_", suffix=".bin")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(file_data)

            await self.file_handler.send_upload_from_server(device_id, transfer_id, tmp_path)
            timeout = getattr(settings, "file_transfer_timeout", 120)
            try:
                await self.transfers.wait_for_state(transfer_id, [TransferState.COMPLETED], timeout=int(timeout))
            except Exception:
                t = self.transfers.get(transfer_id)
                state = t.get("state") if t else "unknown"
                raise Exception(f"Upload failed or timed out, final state: {state}")
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return True

    async def delete_file_on_device(self, device_id: str, remote_path: str):
        command = f"rm -f '{remote_path}'"
        result = await self.send_command_fn(device_id, command)
        if not result.success:
            raise Exception(f"Не удалось удалить файл {remote_path} на устройстве {device_id}: {result.error}")

    async def list_files_on_device(self, device_id: str, remote_path: str, recursive: bool = False) -> List[FileInfo]:
        flag = "-R" if recursive else ""
        command = f"find '{remote_path}' {flag} -type f -exec ls -la {{}} \\; 2>/dev/null || ls -la '{remote_path}'"
        result = await self.send_command_fn(device_id, command)
        if not result.success:
            return []

        lines = result.result.split("\n")
        files: List[FileInfo] = []
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 9:
                files.append(
                    FileInfo(
                        permissions=parts[0],
                        type="file",
                        path=" ".join(parts[8:]),
                    )
                )
        return files

