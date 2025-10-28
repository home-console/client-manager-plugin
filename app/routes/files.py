from fastapi import APIRouter, HTTPException, Depends

from app.dependencies import get_websocket_handler
from app.core.transfers_manager import TransferState
from app.dependencies import get_websocket_handler
from app.schemas.files import (
    UploadInitRequest,
    UploadInitResponse,
    TransferStatusResponse,
    PauseResumeRequest,
)


router = APIRouter()



@router.post("/api/files/upload/init", response_model=UploadInitResponse)
async def upload_init(req: UploadInitRequest, handler = Depends(get_websocket_handler)):
    # Базовая валидация пути (size больше не обязателен)
    if not req.path:
        raise HTTPException(status_code=400, detail="Некорректные параметры")

    # Создаем трансфер и вернем transfer_id
    transfer_id = await handler.transfers.create_upload(req.client_id, req.path, None, req.sha256, req.direction)
    # Отправка WS-команды клиенту будет на следующем шаге (init_upload)
    await handler.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)
    # Установим целевой путь сохранения на сервере при download
    if req.direction == "download" and req.dest_path_server:
        handler.transfers.transfers[transfer_id]["dest_path"] = req.dest_path_server

    # Если это download (загрузка файла с клиента) — сразу инициируем WS старт
    if req.direction == "download":
        start_msg = {
            "type": "file_download_start",
            "data": {
                "transfer_id": transfer_id,
                "path": req.path,
                "chunk_size": 1 << 20,
                "start_offset": 0,
            },
        }
        encrypted = await handler.encryption_service.encrypt_message(start_msg, req.client_id)
        await handler.websocket_manager.send_message(req.client_id, encrypted)
    else:
        # upload-to-client: сервер читает локальный файл и отправляет клиенту
        if not req.source_path_server:
            raise HTTPException(status_code=400, detail="source_path_server обязателен для upload")
        await handler.file_handler.send_upload_from_server(req.client_id, transfer_id, req.source_path_server, 0)

    return UploadInitResponse(transfer_id=transfer_id, state=TransferState.IN_PROGRESS, direction=req.direction)


@router.get("/api/files/transfers/{transfer_id}/status", response_model=TransferStatusResponse)
async def transfer_status(transfer_id: str, handler = Depends(get_websocket_handler)):
    t = handler.transfers.get(transfer_id)
    if not t:
        raise HTTPException(status_code=404, detail="Трансфер не найден")
    return TransferStatusResponse(
        transfer_id=transfer_id,
        state=t["state"],
        received=t["received"],
        size=t.get("size"),
        sha256=t.get("sha256"),
    )


@router.get("/api/files/transfers")
async def list_transfers(handler = Depends(get_websocket_handler), state: str = None, client_id: str = None, limit: int = 100, offset: int = 0):
    items = []
    for tid, t in handler.transfers.transfers.items():
        if state and str(t.get("state")) != state:
            continue
        if client_id and t.get("client_id") != client_id:
            continue
        row = {"transfer_id": tid}
        row.update(t)
        items.append(row)
    total = len(items)
    items = items[offset: offset + limit]
    return {"items": items, "total": total}


@router.delete("/api/files/transfers/{transfer_id}")
async def delete_transfer(transfer_id: str, handler = Depends(get_websocket_handler), delete_file: bool = False):
    if not handler.transfers.get(transfer_id):
        raise HTTPException(status_code=404, detail="Трансфер не найден")
    try:
        if delete_file:
            t = handler.transfers.get(transfer_id)
            dest = t.get("dest_path") or f"/tmp/transfer_{transfer_id}.bin"
            try:
                import os
                if os.path.exists(dest):
                    os.remove(dest)
            except Exception:
                pass
        del handler.transfers.transfers[transfer_id]
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/files/transfers/pause")
async def transfer_pause(req: PauseResumeRequest, handler = Depends(get_websocket_handler)):
    if not handler.transfers.get(req.transfer_id):
        raise HTTPException(status_code=404, detail="Трансфер не найден")
    await handler.transfers.pause(req.transfer_id)
    # WS команда клиенту
    t = handler.transfers.get(req.transfer_id)
    msg = {"type": "file_pause", "data": {"transfer_id": req.transfer_id}}
    encrypted = await handler.encryption_service.encrypt_message(msg, t["client_id"])  # type: ignore
    await handler.websocket_manager.send_message(t["client_id"], encrypted)  # type: ignore
    return {"ok": True}


@router.post("/api/files/transfers/resume")
async def transfer_resume(req: PauseResumeRequest, handler = Depends(get_websocket_handler)):
    if not handler.transfers.get(req.transfer_id):
        raise HTTPException(status_code=404, detail="Трансфер не найден")
    await handler.transfers.resume(req.transfer_id)
    t = handler.transfers.get(req.transfer_id)
    # Отправим повторный старт с offset = received
    start_offset = t.get("received", 0)
    direction = t.get("direction")
    if direction == "download":
        msg = {"type": "file_download_start", "data": {"transfer_id": req.transfer_id, "path": t.get("path"), "chunk_size": 1 << 20, "start_offset": start_offset}}
        encrypted = await handler.encryption_service.encrypt_message(msg, t["client_id"])  # type: ignore
        await handler.websocket_manager.send_message(t["client_id"], encrypted)  # type: ignore
    else:
        # upload-to-client: сервер продолжит отправку с offset
        src = t.get("source_path_server") or t.get("path")
        await handler.file_handler.send_upload_from_server(t["client_id"], req.transfer_id, src, start_offset)
        msg = {"type": "file_resume", "data": {"transfer_id": req.transfer_id}}
        encrypted = await handler.encryption_service.encrypt_message(msg, t["client_id"])  # type: ignore
        await handler.websocket_manager.send_message(t["client_id"], encrypted)  # type: ignore
    return {"ok": True}


@router.post("/api/files/transfers/cancel")
async def transfer_cancel(req: PauseResumeRequest, handler = Depends(get_websocket_handler)):
    if not handler.transfers.get(req.transfer_id):
        raise HTTPException(status_code=404, detail="Трансфер не найден")
    await handler.transfers.cancel(req.transfer_id)
    t = handler.transfers.get(req.transfer_id)
    msg = {"type": "file_cancel", "data": {"transfer_id": req.transfer_id}}
    encrypted = await handler.encryption_service.encrypt_message(msg, t["client_id"])  # type: ignore
    await handler.websocket_manager.send_message(t["client_id"], encrypted)  # type: ignore
    return {"ok": True}


