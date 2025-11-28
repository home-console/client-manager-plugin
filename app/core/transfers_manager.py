import time
import os
import uuid
import asyncio
from typing import Dict, Optional, Any


class TransferState:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TransfersManager:
    """Простое in-memory хранилище состояний файловых трансферов."""

    def __init__(self) -> None:
        self.transfers: Dict[str, Dict[str, Any]] = {}
        from asyncio import Lock
        self._lock = Lock()
        # Условные объекты для ожидания изменений состояния конкретного трансфера
        self._conds: Dict[str, asyncio.Condition] = {}
        # Блокировки на запись для файлов по transfer_id — чтобы безопасно писать чанки параллельно
        self._file_locks: Dict[str, asyncio.Lock] = {}

    async def create_upload(self, client_id: str, path: str, size: Optional[int] = None, sha256: Optional[str] = None, direction: str = "download") -> str:
        transfer_id = str(uuid.uuid4())
        async with self._lock:
            self.transfers[transfer_id] = {
                "type": "file",
                "client_id": client_id,
                "path": path,
                "size": size,
                "sha256": sha256,
                "received": 0,
                "state": TransferState.PENDING,
                "created_at": time.time(),
                "updated_at": time.time(),
                "direction": direction,
                "dest_path": None,
            }
            # Создаём условие для этого трансфера, чтобы другие корутины могли ждать изменений
            self._conds[transfer_id] = asyncio.Condition()
            # Создаём lock для файловых операций по этому трансферу
            self._file_locks[transfer_id] = asyncio.Lock()
        return transfer_id

    def get_file_lock(self, transfer_id: str) -> asyncio.Lock:
        """Вернуть asyncio.Lock для операций с файлом трансфера (создаётся при необходимости)."""
        if transfer_id not in self._file_locks:
            self._file_locks[transfer_id] = asyncio.Lock()
        return self._file_locks[transfer_id]

    def get(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        return self.transfers.get(transfer_id)

    async def update_progress(self, transfer_id: str, received: int, state: Optional[str] = None) -> None:
        async with self._lock:
            t = self.transfers.get(transfer_id)
            if not t:
                return
            t["received"] = received
            if state:
                t["state"] = state
            t["updated_at"] = time.time()
        # Уведомляем ожидающие о смене состояния/прогресса
        try:
            cond = self._conds.get(transfer_id)
            if cond:
                async with cond:
                    cond.notify_all()
        except Exception:
            pass

    async def set_state(self, transfer_id: str, state: str) -> None:
        async with self._lock:
            t = self.transfers.get(transfer_id)
            if not t:
                return
            t["state"] = state
            t["updated_at"] = time.time()
            # Авто-очистка временных файлов: если трансфер завершён/отменён/упал,
            # удалим локальный временный файл, если он находится в UPLOAD_TMP_DIR
            # или имеет префикс 'upload_'. Это предотвращает накопление /tmp.
            try:
                if state in (TransferState.COMPLETED, TransferState.CANCELLED, TransferState.FAILED):
                    src = t.get("source_path_server")
                    if src:
                        try:
                            tmp_dir = os.getenv("UPLOAD_TMP_DIR", "/tmp")
                            bname = os.path.basename(src)
                            if src.startswith(tmp_dir) or bname.startswith("upload_"):
                                if os.path.exists(src):
                                    os.remove(src)
                        except Exception:
                            # не критично — просто логируем при необходимости в более верхнем слое
                            pass
                    # Также можно удалить dest_path если он внутри tmp
                    dest = t.get("dest_path")
                    if dest:
                        try:
                            tmp_dir = os.getenv("UPLOAD_TMP_DIR", "/tmp")
                            dbname = os.path.basename(dest)
                            if dest.startswith(tmp_dir) or dbname.startswith("upload_"):
                                if os.path.exists(dest):
                                    os.remove(dest)
                        except Exception:
                            pass
            except Exception:
                # Защитный catch на случай непредвиденных ошибок при очистке
                pass
        # Уведомляем ожидающие корутины о смене состояния
        try:
            cond = self._conds.get(transfer_id)
            if cond:
                async with cond:
                    cond.notify_all()
        except Exception:
            pass

    async def pause(self, transfer_id: str) -> None:
        await self.set_state(transfer_id, TransferState.PAUSED)

    async def resume(self, transfer_id: str) -> None:
        await self.set_state(transfer_id, TransferState.IN_PROGRESS)

    async def cancel(self, transfer_id: str) -> None:
        await self.set_state(transfer_id, TransferState.CANCELLED)

    async def pause_all_for_client(self, client_id: str) -> int:
        """Пометить все активные трансферы клиента как paused. Возвращает количество."""
        count = 0
        now = time.time()
        async with self._lock:
            for tid, t in self.transfers.items():
                if t.get("client_id") == client_id and t.get("state") in (TransferState.IN_PROGRESS, TransferState.PENDING):
                    t["state"] = TransferState.PAUSED
                    t["updated_at"] = now
                    count += 1
        return count

    async def pause_stale(self, threshold_seconds: int = 60) -> int:
        """Поставить на паузу трансферы без активности дольше threshold_seconds."""
        now = time.time()
        count = 0
        async with self._lock:
            for tid, t in self.transfers.items():
                if t.get("state") == TransferState.IN_PROGRESS:
                    updated = t.get("updated_at", now)
                    if now - updated > threshold_seconds:
                        t["state"] = TransferState.PAUSED
                        t["updated_at"] = now
                        count += 1
        return count

    def list_by_client(self, client_id: str) -> Dict[str, Dict[str, Any]]:
        return {tid: t for tid, t in self.transfers.items() if t.get("client_id") == client_id}

    async def wait_for_state(self, transfer_id: str, desired_states: list[str], timeout: Optional[float] = None) -> str:
        """Ожидание перехода трансфера в одно из состояний `desired_states`.

        Возвращает итоговое состояние или бросает TimeoutError при таймауте.
        """
        # Быстрая проверка
        t = self.get(transfer_id)
        if not t:
            raise KeyError("Transfer not found")
        if str(t.get("state")) in desired_states:
            return str(t.get("state"))

        cond = self._conds.get(transfer_id)
        if not cond:
            # создаём условие если его вдруг нет
            from asyncio import Condition
            cond = Condition()
            self._conds[transfer_id] = cond

        import asyncio
        start = asyncio.get_event_loop().time()
        deadline = start + timeout if timeout else None

        async with cond:
            while True:
                t = self.get(transfer_id)
                if not t:
                    raise KeyError("Transfer removed while waiting")
                state = str(t.get("state"))
                if state in desired_states:
                    return state
                now = asyncio.get_event_loop().time()
                if deadline and now >= deadline:
                    raise TimeoutError("Timeout waiting for transfer state")
                # вычислим оставшееся время для wait
                wait_for = None
                if deadline:
                    wait_for = max(0.1, deadline - now)
                try:
                    await asyncio.wait_for(cond.wait(), timeout=wait_for)
                except asyncio.TimeoutError:
                    # loop around and recheck
                    continue

    async def delete_transfer(self, transfer_id: str) -> bool:
        """Удалить трансфер и связанное условие ожидания. Возвращает True если удалено."""
        async with self._lock:
            if transfer_id in self.transfers:
                try:
                    del self.transfers[transfer_id]
                except KeyError:
                    pass
                try:
                    if transfer_id in self._conds:
                        # нельзя дождаться notify, просто удалить
                        del self._conds[transfer_id]
                except Exception:
                    pass
                try:
                    if transfer_id in self._file_locks:
                        del self._file_locks[transfer_id]
                except Exception:
                    pass
                return True
        return False


