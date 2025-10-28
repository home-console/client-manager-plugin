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

    def create_upload(self, client_id: str, path: str, size: int, sha256: Optional[str] = None, direction: str = "download") -> str:
        transfer_id = str(uuid.uuid4())
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

    def update_progress(self, transfer_id: str, received: int, state: Optional[str] = None) -> None:
        t = self.transfers.get(transfer_id)
        if not t:
            return
        t["received"] = received
        if state:
            t["state"] = state
        t["updated_at"] = time.time()

    def set_state(self, transfer_id: str, state: str) -> None:
        t = self.transfers.get(transfer_id)
        if not t:
            return
        t["state"] = state
        t["updated_at"] = time.time()

    def pause(self, transfer_id: str) -> None:
        self.set_state(transfer_id, TransferState.PAUSED)

    def resume(self, transfer_id: str) -> None:
        self.set_state(transfer_id, TransferState.IN_PROGRESS)

    def cancel(self, transfer_id: str) -> None:
        self.set_state(transfer_id, TransferState.CANCELLED)


