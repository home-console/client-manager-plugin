from __future__ import annotations

import time
from typing import Dict, Any

from ..config import settings


class StatsService:
    """Статистика и лимиты WebSocket (размер/частота сообщений)."""

    def __init__(self):
        self.ws_max_message_bytes = getattr(settings, "websocket_max_message_bytes", 1024 * 1024)
        self.ws_max_messages_per_minute = getattr(settings, "websocket_max_messages_per_minute", 600)
        self._msg_counters: Dict[str, Dict[str, float]] = {}
        self.stats = {
            "total_connections": 0,
            "active_connections": 0,
            "total_messages": 0,
            "total_commands": 0,
            "uptime_start": time.time(),
        }

    def on_connect(self):
        self.stats["total_connections"] += 1
        self.stats["active_connections"] += 1

    def on_disconnect(self):
        self.stats["active_connections"] = max(0, self.stats["active_connections"] - 1)

    def record_message(self, message: dict) -> dict:
        """Middleware hook: обновить счетчики и вернуть сообщение."""
        self.stats["total_messages"] += 1
        if message.get("type") in ["command_request", "command_result"]:
            self.stats["total_commands"] += 1
        return message

    def check_size_ok(self, text: str) -> bool:
        if not self.ws_max_message_bytes:
            return True
        return len(text.encode("utf-8")) <= self.ws_max_message_bytes

    def check_rate_ok(self, client_id: str) -> bool:
        """True если лимит не превышен; обновляет счетчик."""
        now = time.time()
        ctr = self._msg_counters.get(client_id) or {"count": 0.0, "window_start": now}
        if now - ctr["window_start"] >= 60:
            ctr = {"count": 0.0, "window_start": now}
        ctr["count"] += 1
        self._msg_counters[client_id] = ctr
        if not self.ws_max_messages_per_minute:
            return True
        return ctr["count"] <= float(self.ws_max_messages_per_minute)

    def snapshot(self, command_stats: Dict[str, Any], connection_count: int, transfer_stats: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "server_stats": self.stats,
            "command_stats": command_stats,
            "client_count": connection_count,
            "uptime": time.time() - self.stats["uptime_start"],
            "transfer_stats": transfer_stats,
        }

