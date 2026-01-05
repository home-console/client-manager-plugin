"""
Админские эндпоинты для TOFU-enrollment: список, approve, reject
"""

import os
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..dependencies import get_websocket_handler


router = APIRouter(prefix="/enrollments", tags=["Enrollments"])
security = HTTPBearer()


def _require_admin(credentials: HTTPAuthorizationCredentials = Security(security)) -> None:
    token = credentials.credentials
    admin_token = os.getenv("ADMIN_TOKEN")
    if not admin_token or token != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/pending", response_model=List[Dict[str, Any]], tags=["Enrollments"])
async def list_pending(_: None = Depends(_require_admin), handler = Depends(get_websocket_handler)):
    store = getattr(handler, "enrollments", None)
    if not store:
        return []
    return store.list_pending()


@router.post("/{client_id}/approve", tags=["Enrollments"])
async def approve_enrollment(client_id: str, _: None = Depends(_require_admin), handler = Depends(get_websocket_handler)):
    store = getattr(handler, "enrollments", None)
    if not store:
        raise HTTPException(status_code=500, detail="Enrollment store not initialized")
    rec = store.approve(client_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Отправляем результат клиенту по WS, если он онлайн
    try:
        await handler.send_enrollment_result(client_id, status="approved")
    except Exception:
        # Клиент может быть оффлайн — он получит статус при следующем подключении
        pass
    return {"message": "approved", "client_id": client_id}


@router.post("/{client_id}/reject", tags=["Enrollments"])
async def reject_enrollment(client_id: str, _: None = Depends(_require_admin), handler = Depends(get_websocket_handler)):
    store = getattr(handler, "enrollments", None)
    if not store:
        raise HTTPException(status_code=500, detail="Enrollment store not initialized")
    ok = store.reject(client_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    try:
        await handler.send_enrollment_result(client_id, status="rejected")
    except Exception:
        pass
    return {"message": "rejected", "client_id": client_id}


