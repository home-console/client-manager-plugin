from fastapi import APIRouter, HTTPException, Depends, Header
from ..dependencies import get_websocket_handler
from typing import Any, Dict, List
import os


async def require_admin(handler = Depends(get_websocket_handler), authorization: str | None = Header(None)) -> Dict[str, Any]:
    """Dependency: require Bearer JWT with 'admin' permission via handler.auth_service."""
    if not handler:
        raise HTTPException(status_code=503, detail="WebSocket handler unavailable")
    auth_svc = getattr(handler, 'auth_service', None)
    if not auth_svc:
        raise HTTPException(status_code=503, detail="Auth service unavailable")

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    token = authorization
    if token.lower().startswith('bearer '):
        token = token.split(' ', 1)[1].strip()

    try:
        payload = auth_svc.verify_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    perms = payload.get('permissions', []) or []
    roles = payload.get('roles', []) or []
    if 'admin' not in perms and 'admin' not in roles:
        raise HTTPException(status_code=403, detail="Admin permission required")

    return payload

router = APIRouter()


@router.get("/api/admin/audit_queue/stats", tags=["Admin"])
async def audit_queue_stats(payload: Dict[str, Any] = Depends(require_admin), handler = Depends(get_websocket_handler)) -> Dict[str, Any]:
    """Return basic stats about the audit queue (pending count, backend used)."""
    if not handler:
        raise HTTPException(status_code=503, detail="WebSocket handler unavailable")

    using_fs = getattr(handler, 'fs_available', False)
    pending_count = 0
    sample = []
    queue_file = os.getenv('AUDIT_QUEUE_FILE', '/var/lib/client_manager/audit_queue.jsonl')

    try:
        if using_fs:
            rows = await __import__('asyncio').get_event_loop().run_in_executor(None, handler._fs.fetch_pending, 100)
            pending_count = len(rows)
            sample = rows[:10]
        else:
            # in-memory queue
            pending_count = len(getattr(handler, '_audit_pending', []))
            # return small sample
            sample = [x.get('payload') for x in getattr(handler, '_audit_pending', [])[:10]]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        'using_fs': using_fs,
        'pending_count': pending_count,
        'queue_file': queue_file if using_fs else None,
        'sample': sample
    }


@router.get("/api/admin/audit_queue/peek", tags=["Admin"])
async def audit_queue_peek(limit: int = 20, payload: Dict[str, Any] = Depends(require_admin), handler = Depends(get_websocket_handler)) -> List[Dict[str, Any]]:
    """Return up to `limit` pending audit payloads."""
    if not handler:
        raise HTTPException(status_code=503, detail="WebSocket handler unavailable")

    try:
        if getattr(handler, 'fs_available', False):
            rows = await __import__('asyncio').get_event_loop().run_in_executor(None, handler._fs.fetch_pending, limit)
            return rows
        else:
            pending = getattr(handler, '_audit_pending', [])
            return [{'id': None, 'payload': p.get('payload')} for p in pending[:limit]]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
