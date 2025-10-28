import time
import uuid
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
        return transfer_id

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

    async def set_state(self, transfer_id: str, state: str) -> None:
        async with self._lock:
            t = self.transfers.get(transfer_id)
            if not t:
                return
            t["state"] = state
            t["updated_at"] = time.time()

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


