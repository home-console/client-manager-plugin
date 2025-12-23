from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Request

from ...dependencies import get_websocket_handler
from ...schemas.files import UploadInitResponse, TransferStatusResponse, PauseResumeRequest
from . import service

router = APIRouter()


@router.post("/api/files/upload/init", response_model=UploadInitResponse)
async def upload_init(request: Request, handler=Depends(get_websocket_handler)):
    """Инициализация загрузки: multipart или JSON."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        return await service.handle_multipart_upload(request, handler)
    return await service.handle_json_upload(request, handler)


@router.post("/api/files/upload_and_start", response_model=UploadInitResponse)
async def upload_and_start(
    client_id: str = Form(...),
    path: str = Form(...),
    direction: str = Form("upload"),
    file: UploadFile = File(...),
    handler=Depends(get_websocket_handler),
    original_filename: str | None = Form(None),
):
    return await service.upload_and_start(client_id, path, direction, file, handler, original_filename)


@router.post("/api/files/upload/chunk")
async def upload_chunk(
    client_id: str = Form(...),
    path: str = Form(...),
    offset: int = Form(...),
    file: UploadFile = File(...),
    transfer_id: str | None = Form(None),
    original_filename: str | None = Form(None),
    handler=Depends(get_websocket_handler),
):
    return await service.upload_chunk(client_id, path, offset, file, handler, transfer_id, original_filename)


@router.post("/api/files/upload/complete")
async def upload_complete(transfer_id: str = Form(...), handler=Depends(get_websocket_handler)):
    return await service.upload_complete(transfer_id, handler)


@router.get("/api/files/transfers/{transfer_id}/status", response_model=TransferStatusResponse)
async def transfer_status(transfer_id: str, handler=Depends(get_websocket_handler)):
    return await service.transfer_status(transfer_id, handler)


@router.get("/api/files/transfers/{transfer_id}/download")
async def transfer_download(transfer_id: str, handler=Depends(get_websocket_handler)):
    return await service.transfer_download(transfer_id, handler)


@router.get("/api/files/transfers")
async def list_transfers(handler=Depends(get_websocket_handler), state: str | None = None, client_id: str | None = None, limit: int = 100, offset: int = 0):
    return await service.list_transfers(handler, state, client_id, limit, offset)


@router.delete("/api/files/transfers/{transfer_id}")
async def delete_transfer(transfer_id: str, handler=Depends(get_websocket_handler), delete_file: bool = False):
    return await service.delete_transfer(transfer_id, handler, delete_file)


@router.post("/api/clients/{client_id}/reset_encryption")
async def reset_client_encryption(client_id: str, handler=Depends(get_websocket_handler)):
    return await service.reset_client_encryption(client_id, handler)


@router.get("/api/clients/{client_id}/debug/seq")
async def client_debug_seq(client_id: str, handler=Depends(get_websocket_handler)):
    return await service.client_debug_seq(client_id, handler)


@router.post("/api/files/transfers/pause")
async def transfer_pause(req: PauseResumeRequest, handler=Depends(get_websocket_handler)):
    return await service.transfer_pause(req, handler)


@router.post("/api/files/transfers/resume")
async def transfer_resume(req: PauseResumeRequest, handler=Depends(get_websocket_handler)):
    return await service.transfer_resume(req, handler)


@router.post("/api/files/transfers/cancel")
async def transfer_cancel(req: PauseResumeRequest, handler=Depends(get_websocket_handler)):
    return await service.transfer_cancel(req, handler)

