import base64
import hashlib
import os
from typing import Any, Dict

from fastapi import WebSocket

from .transfers_manager import TransfersManager, TransferState


class FileTransferHandler:
    """Обработка файловых сообщений по WS: прием чанков, EOF, ack-логика."""

    def __init__(self, transfers: TransfersManager, websocket_manager, encryption_service) -> None:
        self.transfers = transfers
        self.websocket_manager = websocket_manager
        self.encryption_service = encryption_service

    async def handle_file_chunk(self, websocket: WebSocket, message: Dict[str, Any], client_id: str):
        data = message.get("data", {})
        transfer_id = data.get("transfer_id")
        chunk_index = int(data.get("chunk_index", 0))
        chunk_size = int(data.get("chunk_size", 0))
        offset = int(data.get("offset", 0))
        chunk_hash = data.get("sha256")

        t = self.transfers.get(transfer_id) if transfer_id else None
        if not t or t.get("state") in (TransferState.CANCELLED, TransferState.COMPLETED):
            # Игнор, можно отправить nack
            return

        # Декод base64 и запись по offset в файл назначения (MVP: temp path)
        b64 = data.get("data_b64", "")
        try:
            raw = base64.b64decode(b64)
        except Exception:
            raw = b""

        # Проверка хэша чанка (если пришел sha256)
        if chunk_hash:
            calc = hashlib.sha256(raw).hexdigest()
            if calc != str(chunk_hash):
                # Помечаем как ошибочный и уведомляем клиента
                self.transfers.set_state(transfer_id, TransferState.FAILED)
                nack = {
                    "type": "file_chunk_ack",
                    "data": {
                        "transfer_id": transfer_id,
                        "chunk_index": chunk_index,
                        "received": t.get("received", 0),
                        "ok": False,
                        "error": "hash_mismatch",
                    },
                }
                encrypted = await self.encryption_service.encrypt_message(nack, client_id)
                await self.websocket_manager.send_message(client_id, encrypted)
                return
        dest = t.get("dest_path") or f"/tmp/transfer_{transfer_id}.bin"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Запись по offset (random access): используем r+b / w+b
        mode = "r+b" if os.path.exists(dest) else "w+b"
        with open(dest, mode) as f:
            f.seek(offset)
            f.write(raw)

        # Простая проверка длины
        if len(raw) != chunk_size and t.get("size", 0) != offset+len(raw):
            # можно пометить как failed/nack; пока игнорируем
            pass
        received = max(t.get("received", 0), offset + len(raw))
        self.transfers.update_progress(transfer_id, received, state=TransferState.IN_PROGRESS)

        # Отправляем ack чанка (по тому же WS клиенту)
        ack = {
            "type": "file_chunk_ack",
            "data": {
                "transfer_id": transfer_id,
                "chunk_index": chunk_index,
                "received": received,
                "ok": True,
            },
        }
        encrypted = await self.encryption_service.encrypt_message(ack, client_id)
        await self.websocket_manager.send_message(client_id, encrypted)

    async def handle_file_eof(self, websocket: WebSocket, message: Dict[str, Any], client_id: str):
        data = message.get("data", {})
        transfer_id = data.get("transfer_id")
        t = self.transfers.get(transfer_id) if transfer_id else None
        if not t:
            return

        # Финальная проверка sha256 всего файла (если ожидание задано)
        final_state = TransferState.COMPLETED
        expected_sha = t.get("sha256")
        if expected_sha:
            dest = t.get("dest_path") or f"/tmp/transfer_{transfer_id}.bin"
            try:
                with open(dest, "rb") as f:
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        hasher.update(chunk)
                if hasher.hexdigest() != expected_sha:
                    final_state = TransferState.FAILED
            except Exception:
                final_state = TransferState.FAILED

        if t.get("received", 0) >= t.get("size", 0) and final_state == TransferState.COMPLETED:
            self.transfers.set_state(transfer_id, TransferState.COMPLETED)
        else:
            self.transfers.set_state(transfer_id, TransferState.FAILED)

        done = {
            "type": "file_transfer_done",
            "data": {
                "transfer_id": transfer_id,
                "state": self.transfers.get(transfer_id).get("state"),
                "received": self.transfers.get(transfer_id).get("received"),
                "size": self.transfers.get(transfer_id).get("size"),
            },
        }
        encrypted = await self.encryption_service.encrypt_message(done, client_id)
        await self.websocket_manager.send_message(client_id, encrypted)

    async def send_upload_from_server(self, client_id: str, transfer_id: str, src_path: str, chunk_size: int = 1 << 20):
        """Отправка файла с сервера на клиент (upload-to-client)."""
        if not os.path.exists(src_path):
            return
        size = os.path.getsize(src_path)
        # Старт
        start = {
            "type": "file_upload_start",
            "data": {
                "transfer_id": transfer_id,
                "path": self.transfers.get(transfer_id).get("path"),
                "chunk_size": chunk_size,
                "start_offset": 0,
                "size": size,
            },
        }
        encrypted = await self.encryption_service.encrypt_message(start, client_id)
        await self.websocket_manager.send_message(client_id, encrypted)

        sent = 0
        with open(src_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                b64 = base64.b64encode(chunk).decode("ascii")
                msg = {
                    "type": "file_chunk",
                    "data": {
                        "transfer_id": transfer_id,
                        "chunk_index": sent // chunk_size,
                        "offset": sent,
                        "chunk_size": len(chunk),
                        "data_b64": b64,
                        "sha256": hashlib.sha256(chunk).hexdigest(),
                    },
                }
                enc = await self.encryption_service.encrypt_message(msg, client_id)
                await self.websocket_manager.send_message(client_id, enc)
                sent += len(chunk)

        eof = {"type": "file_eof", "data": {"transfer_id": transfer_id}}
        enc = await self.encryption_service.encrypt_message(eof, client_id)
        await self.websocket_manager.send_message(client_id, enc)


