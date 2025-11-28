from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Request
import tempfile
import shutil
import os
import logging

from ..dependencies import get_websocket_handler
from ..core.transfers_manager import TransferState
from ..schemas.files import (
    UploadInitRequest,
    UploadInitResponse,
    TransferStatusResponse,
    PauseResumeRequest,
)


router = APIRouter()
logger = logging.getLogger(__name__)



@router.post("/api/files/upload/init", response_model=UploadInitResponse)
async def upload_init(request: Request, handler = Depends(get_websocket_handler)):
    """Обрабатывает как JSON `UploadInitRequest`, так и multipart/form-data с файлом.

    Для multipart формата ожидаются поля `client_id`, `path`, опционально `direction` и `original_filename`,
    а также поле `file` с загружаемым файлом. В этом случае файл сохраняется во временный путь и используется
    как `source_path_server` для upload.
    """
    content_type = request.headers.get("content-type", "")
    # Поддержка multipart/form-data с file
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        client_id = form.get("client_id")
        path = form.get("path")
        direction = form.get("direction") or "upload"
        original_filename = form.get("original_filename")
        upload_file = form.get("file")  # type: ignore
        if not client_id or not path or not upload_file:
            raise HTTPException(status_code=400, detail="client_id, path и file обязательны для multipart upload")
        # Сохраняем файл во временный путь
        try:
            tmp_dir = os.getenv("UPLOAD_TMP_DIR", "/tmp")
            fd, tmp_path = tempfile.mkstemp(prefix="upload_", dir=tmp_dir)
            os.close(fd)
            with open(tmp_path, "wb") as out_f:
                shutil.copyfileobj(upload_file.file, out_f)
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения загруженного файла: {e}")
            raise HTTPException(status_code=500, detail="Не удалось сохранить файл")

        size = os.path.getsize(tmp_path)
        # Создаем трансфер
        transfer_id = await handler.transfers.create_upload(client_id, path, size=size, sha256=None, direction=direction)
        handler.transfers.transfers[transfer_id]["source_path_server"] = tmp_path
        if original_filename:
            handler.transfers.transfers[transfer_id]["original_filename"] = original_filename
        await handler.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)
        # Запустить отправку
        if direction == "download":
            start_msg = {
                "type": "file_download_start",
                "data": {"transfer_id": transfer_id, "path": path, "chunk_size": 1 << 20, "start_offset": 0},
            }
            encrypted = await handler.encryption_service.encrypt_message(start_msg, client_id)
            await handler.websocket_manager.send_message(client_id, encrypted)
        else:
            await handler.file_handler.send_upload_from_server(client_id, transfer_id, tmp_path)

        return UploadInitResponse(transfer_id=transfer_id, state=TransferState.IN_PROGRESS, direction=direction)

    # Иначе ожидаем JSON body в формате UploadInitRequest
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")
    try:
        req = UploadInitRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Базовая валидация пути (size больше не обязателен)
    if not req.path:
        raise HTTPException(status_code=400, detail="Некорректные параметры")

    # Создаем трансфер и вернем transfer_id
    transfer_id = await handler.transfers.create_upload(req.client_id, req.path, req.size, req.sha256, req.direction)
    # Отправка WS-команды клиенту будет на следующем шаге (init_upload)
    await handler.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)
    # Установим целевой путь сохранения на сервере при download
    if req.direction == "download" and req.dest_path_server:
        handler.transfers.transfers[transfer_id]["dest_path"] = req.dest_path_server
    # Установим исходный путь на сервере при upload
    if req.direction == "upload" and req.source_path_server:
        handler.transfers.transfers[transfer_id]["source_path_server"] = req.source_path_server
    if req.original_filename:
        handler.transfers.transfers[transfer_id]["original_filename"] = req.original_filename

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
        await handler.file_handler.send_upload_from_server(req.client_id, transfer_id, req.source_path_server)

    return UploadInitResponse(transfer_id=transfer_id, state=TransferState.IN_PROGRESS, direction=req.direction)


@router.post("/api/files/upload_and_start", response_model=UploadInitResponse)
async def upload_and_start(
    client_id: str = Form(...),
    path: str = Form(...),
    direction: str = Form("upload"),
    file: UploadFile = File(...),
    handler = Depends(get_websocket_handler),
    original_filename: str | None = Form(None),
):
    """Приём multipart файла и немедленный запуск отправки на клиент.

    Поля формы: `client_id`, `path`, опционально `original_filename`.
    Возвращает `transfer_id`.
    """
    # Сохраним файл во временный путь внутри контейнера
    try:
        tmp_dir = os.getenv("UPLOAD_TMP_DIR", "/tmp")
        fd, tmp_path = tempfile.mkstemp(prefix="upload_", dir=tmp_dir)
        os.close(fd)
        with open(tmp_path, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f)
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения загруженного файла: {e}")
        raise HTTPException(status_code=500, detail="Не удалось сохранить файл")

    # Создаём трансфер и запустим отправку
    try:
        transfer_id = await handler.transfers.create_upload(client_id, path, size=os.path.getsize(tmp_path), sha256=None, direction=direction)
        handler.transfers.transfers[transfer_id]["source_path_server"] = tmp_path
        if original_filename:
            handler.transfers.transfers[transfer_id]["original_filename"] = original_filename
        await handler.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)
        # Запуск отправки в background
        try:
            # Не дожидаемся: send_upload_from_server выполняет логи и обновление прогресса
            await handler.file_handler.send_upload_from_server(client_id, transfer_id, tmp_path)
        except Exception as e:
            logger.error(f"❌ Ошибка при запуске send_upload_from_server: {e}")
            # оставим трансфер в FAILED
            try:
                await handler.transfers.set_state(transfer_id, TransferState.FAILED)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Не удалось запустить отправку файла")
    except Exception as e:
        logger.error(f"❌ Ошибка создания трансфера: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return UploadInitResponse(transfer_id=transfer_id, state=TransferState.IN_PROGRESS, direction=direction)


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
        client_id=t.get("client_id"),
        direction=t.get("direction"),
        path=t.get("path"),
        source_path_server=t.get("source_path_server"),
        dest_path=t.get("dest_path"),
        original_filename=t.get("original_filename"),
    )


@router.get("/api/files/transfers/{transfer_id}/download")
async def transfer_download(transfer_id: str, handler = Depends(get_websocket_handler)):
    """Отдать скачанный файл по transfer_id (если доступен)."""
    import os
    t = handler.transfers.get(transfer_id)
    if not t:
        raise HTTPException(status_code=404, detail="Трансфер не найден")

    # Ожидаем, что трансфер в состоянии completed и есть dest_path
    state = t.get("state")
    dest = t.get("dest_path")
    if state != "completed" or not dest:
        raise HTTPException(status_code=409, detail="Файл ещё не готов для скачивания")

    if not os.path.exists(dest):
        raise HTTPException(status_code=404, detail="Файл не найден на сервере")

    from fastapi.responses import FileResponse
    return FileResponse(dest, filename=os.path.basename(dest))


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
        # Используем API менеджера трансферов для корректной очистки
        try:
            removed = await handler.transfers.delete_transfer(transfer_id)
        except Exception:
            removed = False
        if not removed:
            raise Exception("Не удалось удалить трансфер")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/clients/{client_id}/reset_encryption")
async def reset_client_encryption(client_id: str, handler = Depends(get_websocket_handler)):
    """Админ: сброс состояния шифрования для клиента (помогает при Replay / рассинхронизации)."""
    try:
        handler.encryption_service.reset_encryption_state(client_id)
        return {"ok": True, "message": f"Encryption state reset for {client_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/clients/{client_id}/debug/seq")
async def client_debug_seq(client_id: str, handler = Depends(get_websocket_handler)):
    """Диагностический эндпоинт: возвращает состояние seq и статус WS соединения для клиента."""
    try:
        # Наличие соединения
        conn = handler.websocket_manager.get_connection(client_id)
        connected = bool(conn)
        metadata = handler.websocket_manager.get_metadata(client_id) or {}

        # Состояние шифрования
        try:
            state = handler.encryption_service.get_encryption_state(client_id)
        except Exception:
            state = {"seq_in": None, "seq_out": None}

        # Unknown temporary state
        unknown_key = f"unknown_{client_id}"
        unknown_state = handler.encryption_service.unknown_client_seqs.get(unknown_key, {})

        return {
            "client_id": client_id,
            "connected": connected,
            "metadata": metadata,
            "encryption_state": state,
            "unknown_state": unknown_state,
        }
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
        await handler.file_handler.send_upload_from_server(t["client_id"], req.transfer_id, src, start_offset=start_offset)
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


