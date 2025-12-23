from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fastapi import WebSocket

if TYPE_CHECKING:  # pragma: no cover
    from ..websocket_handler import WebSocketHandler

logger = logging.getLogger(__name__)


class AdminHandlers:
    """Обработчики административных сообщений (install_service / install_plugin)."""

    def __init__(self, handler: "WebSocketHandler"):
        self.handler = handler

    async def handle_admin_install(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle admin.install_service messages forwarded from core."""
        h = self.handler
        try:
            data = message.get("data", {}) or {}
            install_token = data.get("install_token")
            dry_run = bool(data.get("dry_run", True))

            # For MVP we simply echo back a dry-run response and, on real run, attempt SSH install
            if dry_run:
                resp = {"ok": True, "dry_run": True, "info": "Dry-run supported. No changes applied."}
                await websocket.send_text(json.dumps({"type": "admin.install_result", "data": resp}))
                return

            # Real install: attempt to use SSH installer if payload contains ssh params
            ssh_params = data.get("ssh") or {}
            if ssh_params:
                # Lazy import SSH installer
                try:
                    from ..config import settings
                    from ..installers.ssh_installer import SSHInstaller  # type: ignore

                    installer = SSHInstaller(settings)
                    # Build request-like object (we adapt to the installer API)
                    req = type("Req", (), {})()
                    req.host = ssh_params.get("host")
                    req.port = int(ssh_params.get("port", 22))
                    req.username = ssh_params.get("username")
                    req.password = ssh_params.get("password")
                    req.private_key = ssh_params.get("private_key")
                    req.passphrase = ssh_params.get("passphrase")
                    req.install_dir = ssh_params.get("install_dir")
                    req.use_sudo = ssh_params.get("use_sudo", True)
                    req.timeout = int(ssh_params.get("timeout", 120))
                    req.extra_install_commands = ssh_params.get("extra_install_commands", [])
                    req.create_service = ssh_params.get("create_service", True)
                    # Run installer in thread
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, installer.install_remote_client, req)
                    await websocket.send_text(json.dumps({"type": "admin.install_result", "data": {"ok": True, "result": result}}))
                    return
                except Exception as e:
                    await websocket.send_text(
                        json.dumps({"type": "admin.install_result", "data": {"ok": False, "error": str(e)}})
                    )
                    return

            # If no known installer found, return not implemented
            await websocket.send_text(
                json.dumps({"type": "admin.install_result", "data": {"ok": False, "error": "no installer params provided"}})
            )
        except Exception as e:
            logger.exception("Ошибка в handle_admin_install")
            try:
                await websocket.send_text(json.dumps({"type": "admin.install_result", "data": {"ok": False, "error": str(e)}}))
            except Exception:
                pass

    async def handle_admin_install_plugin(self, websocket: WebSocket, message: dict, client_id: str):
        """Handle admin.install_plugin messages."""
        h = self.handler
        try:
            data = message.get("data", {}) or {}
            plugin_name = data.get("plugin_name")
            version = data.get("version")
            mtype = data.get("type")
            artifact = data.get("artifact_url") or data.get("artifact")
            options = data.get("options") or {}
            install_job_id = data.get("install_job_id")

            # Quick validation/ack to admin frontend
            await websocket.send_text(
                json.dumps({"type": "admin.install_plugin_ack", "data": {"ok": True, "install_job_id": install_job_id}})
            )

            # Run install in background so we don't block WS loop
            async def _run_install():
                agent_id = client_id

                def _post_callback(status: str, logs: str = "", finished_at: str | None = None):
                    try:
                        import os
                        import http.client
                        from urllib.parse import urlparse as _parse
                        import datetime

                        base = os.getenv("CORE_ADMIN_URL", "http://127.0.0.1:11000")
                        b = _parse(base)
                        scheme = (b.scheme or "http").lower()
                        host = b.hostname or "127.0.0.1"
                        port = b.port or (443 if scheme == "https" else 80)
                        path = "/api/registry/plugins/install/callback"
                        if scheme == "https":
                            import ssl

                            ctx = ssl.create_default_context()
                            ctx.check_hostname = False
                            ctx.verify_mode = ssl.CERT_NONE
                            conn = http.client.HTTPSConnection(host, port, timeout=10, context=ctx)
                        else:
                            conn = http.client.HTTPConnection(host, port, timeout=10)
                        body = json.dumps(
                            {
                                "install_job_id": install_job_id,
                                "status": status,
                                "logs": logs,
                                "agent_id": agent_id,
                                "finished_at": finished_at,
                            }
                        ).encode("utf-8")
                        hdrs = {"Content-Type": "application/json"}
                        admin_token = os.getenv("ADMIN_TOKEN", "")
                        if admin_token:
                            hdrs["Authorization"] = f"Bearer {admin_token}"
                        conn.request("POST", path, body=body, headers=hdrs)
                        resp = conn.getresponse()
                        resp.read()
                        try:
                            conn.close()
                        except Exception:
                            pass
                    except Exception as e:  # pragma: no cover - best-effort callback
                        logger.exception(f"Failed to post install callback: {e}")

                # Start
                _post_callback("running", "install started")

                if mtype == "docker" and artifact:
                    image = artifact
                    try:
                        import subprocess
                        import datetime

                        # docker pull
                        p = subprocess.run(
                            ["docker", "pull", image], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300
                        )
                        logs = p.stdout + "\n" + p.stderr
                        if p.returncode != 0:
                            _post_callback("failed", f"docker pull failed:\n{logs}", datetime.datetime.utcnow().isoformat())
                            return

                        # docker run (detached)
                        run_cmd = ["docker", "run", "-d", "--name", f"plugin_{plugin_name}_{install_job_id[:8]}", image]
                        # allow options.args to append
                        args = options.get("args") or []
                        if isinstance(args, list):
                            run_cmd.extend(args)
                        p2 = subprocess.run(
                            run_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120
                        )
                        logs2 = p2.stdout + "\n" + p2.stderr
                        if p2.returncode != 0:
                            _post_callback(
                                "failed", f"docker run failed:\n{logs2}", datetime.datetime.utcnow().isoformat()
                            )
                            return

                        _post_callback("success", f"docker run succeeded:\n{logs2}", datetime.datetime.utcnow().isoformat())
                        return
                    except subprocess.TimeoutExpired as te:
                        _post_callback("failed", f"install timeout: {te}", None)
                        return
                    except Exception as e:
                        _post_callback("failed", f"install exception: {e}", None)
                        return
                else:
                    # Unsupported type: mark failed
                    import datetime

                    _post_callback("failed", f"unsupported plugin type: {mtype}", datetime.datetime.utcnow().isoformat())

            # schedule
            try:
                asyncio.create_task(_run_install())
            except Exception as e:  # pragma: no cover
                logger.exception(f"Failed to schedule install task: {e}")

        except Exception as e:
            logger.exception(f"Error in handle_admin_install_plugin: {e}")
            try:
                await websocket.send_text(
                    json.dumps({"type": "admin.install_plugin_result", "data": {"ok": False, "error": str(e)}})
                )
            except Exception:
                pass

