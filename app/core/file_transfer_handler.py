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
        # Лимиты (MVP) c конфигом из ENV
        try:
            self.max_chunk_size = int(os.getenv("MAX_CHUNK_SIZE", str(1 << 22)))  # 4 MiB
        except Exception:
            self.max_chunk_size = 1 << 22
        # Политики путей и лимитов настраиваются динамически сервисом (без ENV)
        self.denied_dirs: list[str] = []  # по умолчанию пусто — запрещённых директорий нет
        self.allowed_base_dir: str | None = None  # если задана — писать можно только внутри неё
        # Лимиты
        try:
            self.max_transfer_size = int(os.getenv("MAX_TRANSFER_SIZE", "0")) or None  # bytes; None = без лимита
        except Exception:
            self.max_transfer_size = None
        try:
            self.per_client_quota_bytes = int(os.getenv("PER_CLIENT_QUOTA_BYTES", "0")) or None
        except Exception:
            self.per_client_quota_bytes = None

    # ===== Политики, управляемые из сервиса =====
    def set_denied_dirs(self, dirs: list[str]):
        self.denied_dirs = [os.path.abspath(d) for d in dirs if d]

    def set_allowed_base_dir(self, base_dir: str | None):
        self.allowed_base_dir = os.path.abspath(base_dir) if base_dir else None

    def set_limits(self, max_transfer_size: int | None = None, per_client_quota_bytes: int | None = None):
        self.max_transfer_size = max_transfer_size
        self.per_client_quota_bytes = per_client_quota_bytes

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

        # Проверка лимита размера чанка
        if self.max_chunk_size and chunk_size and chunk_size > self.max_chunk_size:
            nack = {
                "type": "file_chunk_ack",
                "data": {
                    "transfer_id": transfer_id,
                    "chunk_index": chunk_index,
                    "received": t.get("received", 0),
                    "ok": False,
                    "error": "chunk_too_large",
                },
            }
            encrypted = await self.encryption_service.encrypt_message(nack, client_id)
            await self.websocket_manager.send_message(client_id, encrypted)
            return

        # Декод base64 и запись по offset в файл назначения (MVP: temp path)
        b64 = data.get("data_b64", "")
        try:
            raw = base64.b64decode(b64)
        except Exception:
            # Ошибка декодирования — отправляем NACK и выходим
            nack = {
                "type": "file_chunk_ack",
                "data": {
                    "transfer_id": transfer_id,
                    "chunk_index": chunk_index,
                    "received": t.get("received", 0),
                    "ok": False,
                    "error": "decode_failed",
                },
            }
            encrypted = await self.encryption_service.encrypt_message(nack, client_id)
            await self.websocket_manager.send_message(client_id, encrypted)
            return

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
        # Валидация пути: allow-base-dir (если задан) и deny-list
        try:
            dest_real = os.path.realpath(dest)
            # allow-base-dir
            if self.allowed_base_dir:
                base = os.path.realpath(self.allowed_base_dir)
                common = os.path.commonpath([dest_real, base])
                if (os.name == 'nt' and os.path.normcase(common) != os.path.normcase(base)) or (os.name != 'nt' and common != base):
                    nack = {
                        "type": "file_chunk_ack",
                        "data": {
                            "transfer_id": transfer_id,
                            "chunk_index": chunk_index,
                            "received": t.get("received", 0),
                            "ok": False,
                            "error": "path_not_in_allowed_base",
                        },
                    }
                    encrypted = await self.encryption_service.encrypt_message(nack, client_id)
                    await self.websocket_manager.send_message(client_id, encrypted)
                    return
            denied = False
            for d in self.denied_dirs:
                base = os.path.realpath(d)
                try:
                    common = os.path.commonpath([dest_real, base])
                except Exception:
                    common = ""
                if os.name == 'nt':
                    if os.path.normcase(common) == os.path.normcase(base):
                        denied = True
                        break
                else:
                    if common == base:
                        denied = True
                        break
            if denied:
                nack = {
                    "type": "file_chunk_ack",
                    "data": {
                        "transfer_id": transfer_id,
                        "chunk_index": chunk_index,
                        "received": t.get("received", 0),
                        "ok": False,
                        "error": "path_denied",
                    },
                }
                encrypted = await self.encryption_service.encrypt_message(nack, client_id)
                await self.websocket_manager.send_message(client_id, encrypted)
                return
        except Exception:
            # Любая ошибка валидации — безопасный отказ
            nack = {
                "type": "file_chunk_ack",
                "data": {
                    "transfer_id": transfer_id,
                    "chunk_index": chunk_index,
                    "received": t.get("received", 0),
                    "ok": False,
                    "error": "path_validation_error",
                },
            }
            encrypted = await self.encryption_service.encrypt_message(nack, client_id)
            await self.websocket_manager.send_message(client_id, encrypted)
            return
        
        # Проверка лимитов размеров: per-transfer и per-client quota (до записи на диск)
        try:
            incoming_total = max(t.get("received", 0), offset + len(raw))
            if self.max_transfer_size and incoming_total > self.max_transfer_size:
                await self.transfers.set_state(transfer_id, TransferState.FAILED)
                nack = {
                    "type": "file_chunk_ack",
                    "data": {
                        "transfer_id": transfer_id,
                        "chunk_index": chunk_index,
                        "received": t.get("received", 0),
                        "ok": False,
                        "error": "transfer_size_limit_exceeded",
                    },
                }
                encrypted = await self.encryption_service.encrypt_message(nack, client_id)
                await self.websocket_manager.send_message(client_id, encrypted)
                return

            if self.per_client_quota_bytes:
                # Суммируем bytes_received по всем активным трансферам клиента
                total_client_bytes = 0
                for tid, st in self.transfers.transfers.items():
                    if st.get("client_id") == client_id:
                        total_client_bytes += int(st.get("bytes_received", 0))
                if total_client_bytes + len(raw) > self.per_client_quota_bytes:
                    await self.transfers.set_state(transfer_id, TransferState.FAILED)
                    nack = {
                        "type": "file_chunk_ack",
                        "data": {
                            "transfer_id": transfer_id,
                            "chunk_index": chunk_index,
                            "received": t.get("received", 0),
                            "ok": False,
                            "error": "client_quota_exceeded",
                        },
                    }
                    encrypted = await self.encryption_service.encrypt_message(nack, client_id)
                    await self.websocket_manager.send_message(client_id, encrypted)
                    return
        except Exception:
            # Если проверка лимитов упала — безопасно отказываем
            await self.transfers.set_state(transfer_id, TransferState.FAILED)
            nack = {
                "type": "file_chunk_ack",
                "data": {
                    "transfer_id": transfer_id,
                    "chunk_index": chunk_index,
                    "received": t.get("received", 0),
                    "ok": False,
                    "error": "limit_check_failed",
                },
            }
            encrypted = await self.encryption_service.encrypt_message(nack, client_id)
            await self.websocket_manager.send_message(client_id, encrypted)
            return
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Запись по offset (random access): используем r+b / w+b
        mode = "r+b" if os.path.exists(dest) else "w+b"
        with open(dest, mode) as f:
            f.seek(offset)
            f.write(raw)

        # Простая проверка длины
        if chunk_size and len(raw) != chunk_size and t.get("size") and t.get("size") != offset+len(raw):
            # можно пометить как failed/nack; пока игнорируем
            pass
        # Метрики приёма
        received = max(t.get("received", 0), offset + len(raw))
        self.transfers.update_progress(transfer_id, received, state=TransferState.IN_PROGRESS)
        # bytes_received
        try:
            self.transfers.transfers[transfer_id]["bytes_received"] = self.transfers.transfers[transfer_id].get("bytes_received", 0) + len(raw)
        except Exception:
            pass

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

        # Всегда считаем финальный sha256 принятого файла
        final_state = TransferState.COMPLETED
        expected_sha = t.get("sha256")
        dest = t.get("dest_path") or f"/tmp/transfer_{transfer_id}.bin"
        computed_sha = None
        try:
            with open(dest, "rb") as f:
                hasher = hashlib.sha256()
                size_bytes = 0
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    hasher.update(chunk)
                computed_sha = hasher.hexdigest()
            # Обновим известный размер, если он не был задан
            if t.get("size") is None:
                self.transfers.transfers[transfer_id]["size"] = size_bytes
        except Exception:
            final_state = TransferState.FAILED

        # Сохраняем вычисленный sha256 в состоянии
        if computed_sha:
            self.transfers.transfers[transfer_id]["sha256"] = computed_sha

        # Если передан expected_sha — сверяем
        if expected_sha and computed_sha and computed_sha != expected_sha:
            final_state = TransferState.FAILED

        # Если sha256 не задан — завершаем по EOF всегда
        if not expected_sha and final_state == TransferState.COMPLETED:
            self.transfers.set_state(transfer_id, TransferState.COMPLETED)
        else:
            # Если sha256 задан — завершаем по результату проверки
            if final_state == TransferState.COMPLETED:
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
                "sha256": self.transfers.get(transfer_id).get("sha256"),
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


