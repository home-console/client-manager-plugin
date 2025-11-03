"""
Простое in-memory хранилище заявок на доверие (TOFU) с TTL.
"""

import time
from typing import Dict, Any, List, Optional


class EnrollmentStore:
    """Хранилище заявок на утверждение доверия клиентов."""

    def __init__(self, ttl_seconds: int = 24 * 3600):
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._trusted: Dict[str, Dict[str, Any]] = {}
        self._ttl = ttl_seconds

    def add_pending(self, client_id: str, data: Dict[str, Any]) -> None:
        now = int(time.time())
        record = {
            "client_id": client_id,
            "data": data,
            "created_at": now,
            "expire_at": now + self._ttl,
            "status": "pending",
        }
        self._pending[client_id] = record

    def list_pending(self) -> List[Dict[str, Any]]:
        self._cleanup()
        return list(self._pending.values())

    def get_pending(self, client_id: str) -> Optional[Dict[str, Any]]:
        self._cleanup()
        return self._pending.get(client_id)

    def approve(self, client_id: str) -> Optional[Dict[str, Any]]:
        self._cleanup()
        rec = self._pending.pop(client_id, None)
        if not rec:
            return None
        rec["status"] = "approved"
        rec["approved_at"] = int(time.time())
        self._trusted[client_id] = rec
        return rec

    def reject(self, client_id: str) -> bool:
        self._cleanup()
        return self._pending.pop(client_id, None) is not None

    def is_trusted(self, client_id: str) -> bool:
        self._cleanup()
        return client_id in self._trusted

    def _cleanup(self) -> None:
        now = int(time.time())
        expired = [cid for cid, rec in self._pending.items() if rec.get("expire_at", 0) < now]
        for cid in expired:
            self._pending.pop(cid, None)


