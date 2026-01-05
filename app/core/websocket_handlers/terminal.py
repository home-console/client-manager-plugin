from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import TYPE_CHECKING

from fastapi import WebSocket

if TYPE_CHECKING:  # pragma: no cover
    from ..websocket_handler import WebSocketHandler

logger = logging.getLogger(__name__)


class TerminalHandlers:
    """Обработчики терминальных сессий и связанных событий."""

    def __init__(self, handler: "WebSocketHandler"):
        self.handler = handler

    async def register_terminal_session(self, session_id: str, agent_id: str, initiator: dict = None):
        """Register a new terminal session mapping to an agent."""
        h = self.handler
        # create recording dir and metadata
        from ...config import settings

        rec_dir = getattr(settings, "TERMINAL_RECORDING_DIR", "/tmp/terminals")
        try:
            os.makedirs(rec_dir, exist_ok=True)
        except Exception:
            logger.warning(f"Не удалось создать каталог для записей терминалов: {rec_dir}")
        rec_path = os.path.join(rec_dir, f"{session_id}.log")
        meta = {"session_id": session_id, "agent_id": agent_id, "initiator": initiator, "started_at": time.time()}
        # write initial metadata header
        try:
            with open(rec_path, "a", encoding="utf-8") as rf:
                rf.write(json.dumps({"meta": meta}) + "\n")
        except Exception:
            logger.exception("Ошибка записи метаданных терминальной сессии")

        h.terminal_sessions[session_id] = {
            "agent_id": agent_id,
            "frontend_ws": None,
            "record_path": rec_path,
            "initiator": initiator,
        }
        # send audit event (best-effort) to core_service
        try:
            payload = {
                "session_id": session_id,
                "client_id": agent_id,
                "initiator": initiator,
                "event": "started",
                "ts": time.time(),
                "record_path": rec_path,
            }
            # Use thread to avoid blocking; audit_service will enqueue if core unreachable
            await asyncio.to_thread(h.audit_service.send_audit_to_core, payload)
        except Exception:
            logger.exception("Ошибка отправки аудита в core_service при создании сессии")

    async def attach_frontend_to_session(self, session_id: str, websocket: WebSocket):
        h = self.handler
        sess = h.terminal_sessions.get(session_id)
        if not sess:
            return False
        sess["frontend_ws"] = websocket
        return True

    async def detach_session(self, session_id: str):
        h = self.handler
        sess = h.terminal_sessions.pop(session_id, None)
        if sess and sess.get("frontend_ws"):
            try:
                await sess["frontend_ws"].close()
            except Exception:
                pass

        # mark session end in recording
        try:
            rp = sess.get("record_path") if sess else None
            if rp:
                with open(rp, "a", encoding="utf-8") as rf:
                    rf.write(json.dumps({"event": "session_detached", "ts": time.time()}) + "\n")
        except Exception:
            logger.exception("Ошибка при пометке завершения сессии в записи")

    async def send_input_to_agent(self, session_id: str, b64_payload: str) -> bool:
        """Send terminal input to agent (encrypted message)."""
        h = self.handler
        sess = h.terminal_sessions.get(session_id)
        if not sess:
            return False
        agent_id = sess.get("agent_id")
        if not agent_id:
            return False
        msg = {"type": "terminal.input", "data": {"session_id": session_id, "payload": b64_payload}}
        try:
            enc = await h.encryption_service.encrypt_message(msg, agent_id)
            return await h.websocket_manager.send_message(agent_id, enc)
        except Exception as e:
            logger.error(f"Ошибка отправки terminal.input агенту {agent_id}: {e}")
            return False

    async def handle_terminal_output(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle terminal output coming from agent and forward to frontend websocket."""
        h = self.handler
        try:
            data = message.get("data", {})
            session_id = data.get("session_id")
            payload_b64 = data.get("payload")
            if not session_id or not payload_b64:
                return
            decoded = base64.b64decode(payload_b64)
            sess = h.terminal_sessions.get(session_id)
            if not sess:
                logger.debug(f"Terminal output for unknown session {session_id}")
                return
            fw = sess.get("frontend_ws")
            if not fw:
                logger.debug(f"No frontend attached for session {session_id}")
                return
            # write to recording (audit)
            try:
                rp = sess.get("record_path")
                if rp:
                    entry = {"event": "output", "ts": time.time(), "data_b64": payload_b64}
                    with open(rp, "a", encoding="utf-8") as rf:
                        rf.write(json.dumps(entry) + "\n")
            except Exception:
                logger.exception("Ошибка записи вывода терминала в файл")

            await fw.send_bytes(decoded)
        except Exception as e:  # pragma: no cover
            logger.error(f"Ошибка при обработке terminal.output: {e}")

    async def handle_terminal_started(self, websocket: WebSocket, message: dict, client_id: str):
        h = self.handler
        try:
            data = message.get("data", {})
            session_id = data.get("session_id")
            sess = h.terminal_sessions.get(session_id)
            if not sess:
                return
            fw = sess.get("frontend_ws")
            if fw:
                await fw.send_text('{"type":"terminal.started","session_id":"%s"}' % session_id)
            # write start event to recording
            try:
                rp = sess.get("record_path")
                if rp:
                    with open(rp, "a", encoding="utf-8") as rf:
                        rf.write(json.dumps({"event": "started", "ts": time.time()}) + "\n")
            except Exception:
                logger.exception("Ошибка записи события start в запись терминала")
        except Exception as e:  # pragma: no cover
            logger.error(f"Ошибка при обработке terminal.started: {e}")

    async def handle_terminal_stopped(self, websocket: WebSocket, message: dict, client_id: str):
        h = self.handler
        try:
            data = message.get("data", {})
            session_id = data.get("session_id")
            sess = h.terminal_sessions.get(session_id)
            if not sess:
                return
            fw = sess.get("frontend_ws")
            if fw:
                await fw.send_text(
                    '{"type":"terminal.stopped","session_id":"%s","exit_code":%s}' % (session_id, data.get("exit_code", 0))
                )
            # cleanup
            # write stop event
            try:
                rp = sess.get("record_path")
                if rp:
                    with open(rp, "a", encoding="utf-8") as rf:
                        rf.write(
                            json.dumps(
                                {"event": "stopped", "ts": time.time(), "exit_code": data.get("exit_code", 0)}
                            )
                            + "\n"
                        )
            except Exception:
                logger.exception("Ошибка записи события stop в запись терминала")
            # send audit stop event
            try:
                payload = {
                    "session_id": session_id,
                    "client_id": sess.get("agent_id"),
                    "event": "stopped",
                    "ts": time.time(),
                    "exit_code": data.get("exit_code", 0),
                    "record_path": sess.get("record_path"),
                }
                await asyncio.to_thread(h.audit_service.send_audit_to_core, payload)
            except Exception:
                logger.exception("Ошибка отправки аудита в core_service при остановке сессии")

            # Try to upload recording to external object storage (S3/MinIO) in background
            try:
                rp = sess.get("record_path")
                if rp:
                    # run background task without waiting
                    asyncio.create_task(self._maybe_upload_recording(rp, session_id))
            except Exception:
                logger.exception("Ошибка запуска фоновой загрузки записи в S3/MinIO")

            await self.detach_session(session_id)
        except Exception as e:  # pragma: no cover
            logger.error(f"Ошибка при обработке terminal.stopped: {e}")

    async def handle_terminal_input_error(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle error reports from agent about terminal input (e.g. unknown session)."""
        h = self.handler
        try:
            data = message.get("data", {})
            session_id = data.get("session_id")
            err = data.get("error") or data.get("message") or "unknown error"
            if not session_id:
                logger.debug("terminal.input.error without session_id from agent")
                return
            sess = h.terminal_sessions.get(session_id)
            if not sess:
                logger.debug(f"Received terminal.input.error for unknown session {session_id}")
                return
            fw = sess.get("frontend_ws")
            # notify frontend if attached
            if fw:
                try:
                    await fw.send_text(
                        json.dumps(
                            {"type": "session.unknown", "session_id": session_id, "message": f"Agent reported error: {err}"}
                        )
                    )
                except Exception:
                    logger.exception("Failed to send session.unknown to frontend")
            # detach session mapping to avoid further forwarding
            try:
                await self.detach_session(session_id)
            except Exception:
                logger.exception(f"Failed to detach session {session_id} after agent reported input error")
        except Exception as e:  # pragma: no cover
            logger.error(f"Ошибка при обработке terminal.input.error: {e}")

    async def _maybe_upload_recording(self, record_path: str, session_id: str):
        """Upload recording file to S3/MinIO if configured. Runs in background."""
        h = self.handler
        try:
            from ...config import settings

            s3_endpoint = getattr(settings, "s3_endpoint", None)
            bucket = getattr(settings, "s3_bucket", None)
            if not s3_endpoint or not bucket:
                logger.debug("S3/MinIO not configured, skipping upload of recording")
                return

            # Lazy import boto3 to keep dependency optional until used
            try:
                import boto3  # type: ignore
            except Exception:
                logger.error("boto3 is not installed - cannot upload recordings to S3/MinIO")
                return

            aws_key = getattr(settings, "s3_access_key_id", None)
            aws_secret = getattr(settings, "s3_secret_access_key", None)
            region = getattr(settings, "s3_region", None)
            presign_expiry = int(getattr(settings, "s3_presign_expiry_seconds", 3600))

            # Key name in bucket: terminals/{session_id}.log
            key_name = f"terminals/{session_id}.log"

            client_kwargs = {"endpoint_url": s3_endpoint}
            if aws_key and aws_secret:
                client_kwargs.update({"aws_access_key_id": aws_key, "aws_secret_access_key": aws_secret})
            if region:
                client_kwargs.update({"region_name": region})

            s3 = boto3.client("s3", **client_kwargs)

            # Upload
            try:
                s3.upload_file(record_path, bucket, key_name)
                logger.info(f"Recording {record_path} uploaded to {bucket}/{key_name}")
            except Exception as e:  # pragma: no cover
                logger.exception(f"Failed to upload recording to S3/MinIO: {e}")
                return

            # Generate presigned URL if possible
            presigned_url = None
            try:
                presigned_url = s3.generate_presigned_url(
                    ClientMethod="get_object", Params={"Bucket": bucket, "Key": key_name}, ExpiresIn=presign_expiry
                )
            except Exception:
                logger.debug("Could not generate presigned URL for uploaded recording")

            # Send audit update to core with storage info
            try:
                payload = {
                    "session_id": session_id,
                    "event": "recording_uploaded",
                    "ts": time.time(),
                    "storage_bucket": bucket,
                    "storage_key": key_name,
                    "presigned_url": presigned_url,
                }
                await asyncio.to_thread(h.audit_service.send_audit_to_core, payload)
            except Exception:
                logger.exception("Ошибка отправки аудита о загруженной записи в core_service")

            # Optionally remove local recording file to save disk space (respect retention policy)
            try:
                retention_days = int(getattr(settings, "recordings_retention_days", 30))
                # If retention is configured <=0 we still remove local copy after upload
                os.remove(record_path)
                logger.debug(f"Local recording {record_path} removed after upload")
            except Exception:
                logger.exception("Не удалось удалить локальный файл записи после загрузки")

        except Exception:
            logger.exception("Unexpected error in _maybe_upload_recording")

