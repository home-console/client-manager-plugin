from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import threading
import time
from typing import Optional
from urllib.parse import urlparse as _parse

logger = logging.getLogger(__name__)


class AuditService:
    """Фоновые задачи аудита и доставки событий в core."""

    def __init__(self):
        self.core_reachable: bool = False
        self._audit_pending: list[dict] = []
        self._audit_lock = threading.Lock()
        self._core_monitor_task: Optional[asyncio.Task] = None
        self._audit_flusher_task: Optional[asyncio.Task] = None
        # File-backed persistence (JSONL) если доступна
        self.fs_available = False
        try:
            from ..core import audit_queue_fs as _fs  # type: ignore

            _fs.ensure_queue_dir()
            self._fs = _fs
            self.fs_available = True
            logger.info("FS persistence enabled for audit queue (JSONL)")
        except Exception:
            logger.info("FS persistence unavailable - using in-memory audit queue")

    async def start_background_tasks(self):
        """Запуск фоновых задач мониторинга core и флеша очереди аудита."""
        if not getattr(self, "_core_monitor_task", None):
            self._core_monitor_task = asyncio.create_task(self._core_monitor_loop())
        if not getattr(self, "_audit_flusher_task", None):
            self._audit_flusher_task = asyncio.create_task(self._audit_flusher_loop())

    def _is_core_reachable_sync(self) -> bool:
        """Synchronous check if CORE_ADMIN_URL/health responds 2xx."""
        try:
            base = os.getenv("CORE_ADMIN_URL", "http://127.0.0.1:11000")
            b = _parse(base)
            scheme = (b.scheme or "http").lower()
            host = b.hostname or "127.0.0.1"
            port = b.port or (443 if scheme == "https" else 80)
            path = "/health"
            if scheme == "https":
                import ssl

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn = http.client.HTTPSConnection(host, port, timeout=3, context=ctx)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=3)
            conn.request("GET", path, headers={"User-Agent": "client_manager/health-check"})
            resp = conn.getresponse()
            status = resp.status
            return 200 <= status < 300
        except Exception:
            return False

    async def _core_monitor_loop(self):
        """Периодически обновляет флаг core_reachable с экспоненциальным бэкоффом."""
        backoff = 1
        while True:
            try:
                reachable = await asyncio.to_thread(self._is_core_reachable_sync)
                if reachable and not self.core_reachable:
                    logger.info("Core reachable: will attempt to flush queued audits")
                if not reachable and self.core_reachable:
                    logger.warning("Core became unreachable: audits will be queued")
                self.core_reachable = bool(reachable)
                if reachable:
                    backoff = 1
                    await asyncio.sleep(5)
                else:
                    await asyncio.sleep(min(backoff, 60))
                    backoff = min(backoff * 2, 60)
            except Exception:
                logger.exception("Error in core monitor loop")
                await asyncio.sleep(5)

    def _post_audit_sync(self, payload: dict) -> bool:
        """Синхронная отправка аудита в core admin API. True при успехе."""
        try:
            base = os.getenv("CORE_ADMIN_URL", "http://127.0.0.1:11000")
            b = _parse(base)
            scheme = (b.scheme or "http").lower()
            host = b.hostname or "127.0.0.1"
            port = b.port or (443 if scheme == "https" else 80)
            path = "/api/terminals/audit"
            if scheme == "https":
                import ssl

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn = http.client.HTTPSConnection(host, port, timeout=5, context=ctx)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=5)
            hdrs = {"Content-Type": "application/json"}
            body = json.dumps(payload).encode("utf-8")
            conn.request("POST", path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            try:
                _ = data.decode("utf-8") if data else ""
            except Exception:
                pass
            if 200 <= resp.status < 300:
                return True
            else:
                logger.debug(f"Audit POST returned {resp.status}")
                return False
        except Exception:
            logger.exception("_post_audit_sync failed")
            return False

    def _enqueue_audit_sync(self, payload: dict):
        """Потокобезопасное добавление события в очередь (FS или память)."""
        try:
            if getattr(self, "fs_available", False):
                try:
                    _id = self._fs.enqueue(payload)
                    logger.info(f"Audit persisted to FS (id={_id})")
                    return
                except Exception:
                    logger.exception("Failed to persist audit to FS, falling back to memory")

            with self._audit_lock:
                self._audit_pending.append({"payload": payload, "queued_at": time.time()})
            logger.info("Audit queued: core unreachable, stored locally until core is reachable")
        except Exception:
            logger.exception("Failed to enqueue audit payload")

    def send_audit_to_core(self, payload: dict):
        """Синхронный враппер для использования из потоков. Кладёт в очередь при недоступности core."""
        try:
            if not getattr(self, "core_reachable", False):
                self._enqueue_audit_sync(payload)
                return
            ok = self._post_audit_sync(payload)
            if not ok:
                self._enqueue_audit_sync(payload)
        except Exception:
            logger.exception("send_audit_to_core wrapper failed")

    async def _audit_flusher_loop(self):
        """Фоновая задача, пытающаяся отправить накопленные аудиты при доступном core."""
        while True:
            try:
                if getattr(self, "core_reachable", False):
                    if getattr(self, "fs_available", False):
                        rows = await asyncio.to_thread(self._fs.fetch_pending, 100)
                        if not rows:
                            await asyncio.sleep(2)
                            continue
                        for row in rows:
                            row_id = row.get("id")
                            payload = row.get("payload")
                            ok = await asyncio.to_thread(self._post_audit_sync, payload)
                            if ok:
                                await asyncio.to_thread(self._fs.mark_sent, row_id)
                                logger.info(f"Queued audit row {row_id} successfully posted to core")
                            else:
                                logger.debug(f"Failed to POST queued audit row {row_id}, will retry later")
                                await asyncio.sleep(2)
                                break
                        await asyncio.sleep(1)
                        continue

                    with self._audit_lock:
                        pending = list(self._audit_pending)
                        self._audit_pending.clear()

                    if not pending:
                        await asyncio.sleep(2)
                        continue

                    for item in pending:
                        payload = item.get("payload")
                        ok = await asyncio.to_thread(self._post_audit_sync, payload)
                        if not ok:
                            with self._audit_lock:
                                self._audit_pending.append(item)
                            logger.debug("Requeued audit after failed POST, will retry later")
                            await asyncio.sleep(2)
                            break
                        else:
                            logger.info("Queued audit successfully posted to core")
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(5)
            except Exception:
                logger.exception("Error in audit flusher loop")
                await asyncio.sleep(5)

