from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse
from uuid import uuid4
import base64

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse
from uuid import uuid4
import base64

from ..dependencies import get_websocket_handler

router = APIRouter()


@router.post("/api/clients/{client_id}/terminal/start")
async def start_terminal(client_id: str, request: Request, handler=Depends(get_websocket_handler)):
    """Инициировать терминальную сессию на remote client через существующий WS канал."""
    if not handler:
        raise HTTPException(status_code=500, detail="WebSocket handler not available")

    # Проверка, что агент подключён
    ws = handler.websocket_manager.get_connection(client_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Client not connected")

    # Authorization: require INTERNAL_SERVICE_TOKEN or JWT with terminal_start permission
    auth = request.headers.get("authorization") or ""
    token = None
    if auth.startswith("Bearer "):
        token = auth.split(None, 1)[1]
    from ..config import settings
    initiator = {"type": "unknown"}
    if token:
        # internal token shortcut
        if token == getattr(settings, "INTERNAL_SERVICE_TOKEN", None):
            initiator = {"type": "internal", "id": "internal_service"}
        else:
            # Ensure auth service is available
            if not getattr(handler, 'auth_service', None):
                raise HTTPException(status_code=503, detail="Auth service unavailable")
            # Try JWT verification via auth_service
            try:
                payload = handler.auth_service.verify_token(token)
                if payload:
                    perms = payload.get("permissions", [])
                    if "terminal_start" not in perms and "admin" not in perms:
                        raise HTTPException(status_code=403, detail="Insufficient permissions")
                    initiator = {"type": "user", "id": payload.get("sub") or payload.get("username") or payload.get("client_id"), "permissions": perms}
                else:
                    raise HTTPException(status_code=401, detail="Invalid token")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Authorization required")

    session_id = str(uuid4())
    # Регистрируем сессию в handler (с информацией об инициаторе)
    await handler.register_terminal_session(session_id, client_id, initiator=initiator)

    # Отправляем команду агенту (через encryption_service)
    msg = {
        "type": "terminal.start",
        "data": {
            "session_id": session_id,
            "command": "/bin/bash",
            "cols": 80,
            "rows": 24,
        }
    }
    try:
        enc = await handler.encryption_service.encrypt_message(msg, client_id)
        ok = await handler.websocket_manager.send_message(client_id, enc)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to send start message to client")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending message: {e}")

    return {"session_id": session_id}



@router.websocket('/ws/terminal/{session_id}')
async def terminal_ws(websocket: WebSocket, session_id: str, handler=Depends(get_websocket_handler)):
    """WebSocket endpoint для фронтенда, проксирует ввод/вывод между браузером и агентом через handler."""
    if not handler:
        await websocket.close(code=1011)
        return

    # Authorization: expect Authorization header (Bearer <token>) or ?token= in query
    auth = None
    try:
        auth = websocket.headers.get('authorization') or websocket.query_params.get('token')
    except Exception:
        auth = None
    from ..config import settings
    allowed = False
    if auth and auth.startswith('Bearer '):
        token = auth.split(None, 1)[1]
        if token == getattr(settings, 'INTERNAL_SERVICE_TOKEN', None):
            allowed = True
        else:
            # Ensure auth service exists
            if not getattr(handler, 'auth_service', None):
                # no auth backend — cannot verify token
                allowed = False
            else:
                try:
                    payload = handler.auth_service.verify_token(token)
                    if payload and ("admin" in payload.get('permissions', []) or "terminal_connect" in payload.get('permissions', [])):
                        allowed = True
                except Exception:
                    allowed = False
    if not allowed:
        # If auth service is unavailable, return server error; otherwise policy violation
        if not getattr(handler, 'auth_service', None):
            try:
                await websocket.send_text('{"type":"error","message":"Auth service unavailable"}')
            except Exception:
                pass
            await websocket.close(code=1011)
            return
        await websocket.close(code=1008)
        return

    # Подключаем фронтенд
    await websocket.accept()
    attached = await handler.attach_frontend_to_session(session_id, websocket)
    if not attached:
        await websocket.send_text('{"type":"error","message":"Unknown session or agent disconnected"}')
        await websocket.close(code=1008)
        return

    try:
        while True:
            msg = await websocket.receive()
            # Обрабатываем разные типы сообщений: text control (JSON) и bytes for input
            if msg.get('type') == 'websocket.receive':
                if 'text' in msg:
                    text = msg['text']
                    # ожидаем JSON control, например resize
                    try:
                        import json
                        j = json.loads(text)
                        if j.get('type') == 'resize':
                            cols = int(j.get('cols', 80))
                            rows = int(j.get('rows', 24))
                            control = {"type": "terminal.resize", "data": {"session_id": session_id, "cols": cols, "rows": rows}}
                            enc = await handler.encryption_service.encrypt_message(control, handler.terminal_sessions[session_id]['agent_id'])
                            await handler.websocket_manager.send_message(handler.terminal_sessions[session_id]['agent_id'], enc)
                        else:
                            # ignore
                            pass
                    except Exception:
                        pass
                elif 'bytes' in msg and msg['bytes'] is not None:
                    b = msg['bytes']
                    # encode to base64 and send as terminal.input
                    b64 = base64.b64encode(b).decode('ascii')
                    await handler.send_input_to_agent(session_id, b64)
            else:
                # other events (disconnect) handled below
                pass
    except WebSocketDisconnect:
        # detach frontend
        await handler.detach_session(session_id)
    except Exception:
        await handler.detach_session(session_id)
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/api/terminals/{session_id}/recording")
async def get_terminal_recording(session_id: str, request: Request, handler=Depends(get_websocket_handler)):
    """Download recorded terminal session (admins/internal only)."""
    auth = request.headers.get("authorization") or ""
    token = None
    if auth.startswith("Bearer "):
        token = auth.split(None, 1)[1]
    from ..config import settings
    allowed = False
    if token and token == getattr(settings, "INTERNAL_SERVICE_TOKEN", None):
        allowed = True
    else:
        if not getattr(handler, 'auth_service', None):
            raise HTTPException(status_code=503, detail="Auth service unavailable")
        try:
            payload = handler.auth_service.verify_token(token)
            if payload and ("admin" in payload.get("permissions", []) or "terminal_audit" in payload.get("permissions", [])):
                allowed = True
        except Exception:
            allowed = False

    if not allowed:
        raise HTTPException(status_code=403, detail="Forbidden")

    import os
    rec_dir = getattr(settings, "TERMINAL_RECORDING_DIR", "/tmp/terminals")
    path = os.path.join(rec_dir, f"{session_id}.log")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(path, media_type="text/plain", filename=f"{session_id}.log")

 
