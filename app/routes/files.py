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

# Попытка импортировать Celery app для постановки задач в очередь (PoC RabbitMQ/Celery)
try:
    from client_manager.tasks.celery_app import celery as celery_app  # type: ignore
except Exception:
    celery_app = None


def _enqueue_or_fallback(handler, client_id: str, transfer_id: str, src_path: str, start_offset: int = 0):
    """Попытка поставить задачу в Celery (если доступна), иначе запустить фоновой корутиной."""
    if celery_app and os.getenv("CELERY_BROKER_URL"):
        try:
            res = celery_app.send_task(
                "client_manager.tasks.upload_tasks.process_upload_task",
                args=[transfer_id, client_id, src_path, start_offset],
            )
            try:
                handler.transfers.transfers[transfer_id]["job_id"] = getattr(res, "id", None)
            except Exception:
                pass
            logger.info(f"Enqueued upload task {transfer_id} -> job {getattr(res, 'id', None)}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to enqueue upload task for {transfer_id}: {e}")
            # Fallthrough to fallback

    # Fallback: запускаем локальную background coroutine
    try:
        import asyncio as _asyncio
        _asyncio.create_task(_background_send(handler, client_id, transfer_id, src_path, start_offset=start_offset))
        logger.info(f"Started local background send for transfer {transfer_id}")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to start local background send for {transfer_id}: {e}")
        return False


async def _background_send(handler, client_id: str, transfer_id: str, src_path: str, start_offset: int = 0):
    """Run `send_upload_from_server` in background with retries/backoff and robust state handling.

    This wrapper ensures transient websocket/unavailability issues are retried a few times
    and that the transfer state is set to FAILED on permanent errors. Temporary files are
    cleaned up by `TransfersManager.set_state` when terminal states are set.
    """
    import asyncio as _asyncio
    max_retries = int(os.getenv("SEND_BG_RETRIES", "3"))
    base_backoff = float(os.getenv("SEND_BG_BACKOFF", "2.0"))

    attempt = 0
    last_exc = None
    while attempt < max_retries:
        attempt += 1
        try:
            t = handler.transfers.get(transfer_id)
            if not t:
                logger.warning(f"Background send: transfer {transfer_id} disappeared, aborting")
                return
            # If transfer was cancelled externally — don't attempt
            if t.get("state") == TransferState.CANCELLED:
                logger.info(f"Background send: transfer {transfer_id} was cancelled, stopping")
                return

            # Quick check: is client connected? If not, wait a bit and retry.
            conn = handler.websocket_manager.get_connection(client_id)
            if not conn:
                wait_for = base_backoff * attempt
                logger.info(f"Background send: client {client_id} not connected, attempt {attempt}/{max_retries}, sleeping {wait_for}s")
                await _asyncio.sleep(wait_for)
                continue

            logger.info(f"Background send: starting attempt {attempt} for transfer {transfer_id} to client {client_id}")
            await handler.file_handler.send_upload_from_server(client_id, transfer_id, src_path, start_offset=start_offset)

            # Check end state — send_upload_from_server is expected to set COMPLETED on success
            t2 = handler.transfers.get(transfer_id)
            if t2 and t2.get("state") == TransferState.COMPLETED:
                logger.info(f"Background send: transfer {transfer_id} completed on attempt {attempt}")
                return
            # If not completed, prepare to retry
            logger.warning(f"Background send: transfer {transfer_id} not completed after attempt {attempt}, state={t2.get('state') if t2 else 'unknown'}")
        except Exception as e:
            last_exc = e
            logger.error(f"❌ Background send exception for {transfer_id} attempt {attempt}: {e}")

        # backoff before next attempt
        if attempt < max_retries:
            backoff = base_backoff * (2 ** (attempt - 1))
            await _asyncio.sleep(backoff)

    # Если дошли сюда — все попытки исчерпаны
    try:
        logger.error(f"❌ Background send failed for {transfer_id} after {max_retries} attempts. Marking FAILED.")
        await handler.transfers.set_state(transfer_id, TransferState.FAILED)
    except Exception:
        pass



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
        # Если путь выглядит как директория (заканчивается на / или '.'), дополним его оригинальным именем файла
        if original_filename and (path == '.' or path.endswith(os.path.sep) or path.endswith('/')):
            # Уберём возможный завершающий сепаратор
            normalized = path.rstrip(os.path.sep).rstrip('/')
            path = os.path.join(normalized, original_filename)

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
            # Пытаемся поставить задачу в очередь (RabbitMQ/Celery). При отсутствии — fallback на background.
            _enqueue_or_fallback(handler, client_id, transfer_id, tmp_path)

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

    # Если путь указывает на директорию и у нас есть original_filename,
    # сформируем полный путь на стороне серверa до создания трансфера.
    if req.original_filename and (req.path == '.' or req.path.endswith(os.path.sep) or req.path.endswith('/')):
        normalized = req.path.rstrip(os.path.sep).rstrip('/')
        req.path = os.path.join(normalized, req.original_filename)

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
        # upload-to-client: сервер читает локальный файл и отправляет клиенту (background)
        if not req.source_path_server:
            raise HTTPException(status_code=400, detail="source_path_server обязателен для upload")
        import asyncio as _asyncio
        _asyncio.create_task(_background_send(handler, req.client_id, transfer_id, req.source_path_server))

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
            # Попробуем поставить в очередь, иначе запустим локально
            _enqueue_or_fallback(handler, client_id, transfer_id, tmp_path)
        except Exception as e:
            logger.error(f"❌ Ошибка при запуске отправки: {e}")
            try:
                await handler.transfers.set_state(transfer_id, TransferState.FAILED)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Не удалось запустить отправку файла")
    except Exception as e:
        logger.error(f"❌ Ошибка создания трансфера: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return UploadInitResponse(transfer_id=transfer_id, state=TransferState.IN_PROGRESS, direction=direction)


@router.post("/api/files/upload/chunk")
async def upload_chunk(
    client_id: str = Form(...),
    path: str = Form(...),
    offset: int = Form(...),
    file: UploadFile = File(...),
    transfer_id: str | None = Form(None),
    original_filename: str | None = Form(None),
    handler = Depends(get_websocket_handler),
):
    """Приём чанка файла для resumable upload.

    Если передан `transfer_id` — чанк дописывается в существующий tmp‑файл.
    Иначе создаётся новый трансфер и временный файл, возвращается `transfer_id`.
    Поля формы: `client_id`, `path`, `offset`, `file`, опционально `transfer_id`, `original_filename`.
    """
    import hashlib
    # Простейшая валидация
    if not client_id or not path:
        raise HTTPException(status_code=400, detail="client_id и path обязательны")

    tmp_dir = os.getenv("UPLOAD_TMP_DIR", "/tmp")
    # Если передан transfer_id — используем существующий
    if transfer_id:
        t = handler.transfers.get(transfer_id)
        if not t:
            raise HTTPException(status_code=404, detail="Transfer not found")
        src = t.get("source_path_server")
        if not src:
            # создаём временный файл и сохраним в трансфере
            fd, src = tempfile.mkstemp(prefix="upload_", dir=tmp_dir)
            os.close(fd)
            handler.transfers.transfers[transfer_id]["source_path_server"] = src
            if original_filename:
                handler.transfers.transfers[transfer_id]["original_filename"] = original_filename
        # Пишем чанк в файл по offset, с блокировкой на запись чтобы параллельные запросы не мешали друг другу
        try:
            lock = handler.transfers.get_file_lock(transfer_id)
            async with lock:
                data = await file.read()
                mode = "r+b" if os.path.exists(src) else "w+b"
                with open(src, mode) as f:
                    f.seek(offset)
                    f.write(data)
                received = max(handler.transfers.get(transfer_id).get("received", 0), offset + len(data))
                await handler.transfers.update_progress(transfer_id, received, state=TransferState.IN_PROGRESS)
            return {"transfer_id": transfer_id, "received": received}
        except Exception as e:
            logger.error(f"❌ Error writing chunk for {transfer_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Иначе создаём новый transfer
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="upload_", dir=tmp_dir)
        os.close(fd)
        # Запишем первый чанк в tmp
        data = await file.read()
        with open(tmp_path, "r+b") as f:
            f.seek(offset)
            f.write(data)
        size_est = offset + len(data)
        # Если путь выглядит как директория и есть original_filename — дополним
        if original_filename and (path == '.' or path.endswith(os.path.sep) or path.endswith('/')):
            normalized = path.rstrip(os.path.sep).rstrip('/')
            path = os.path.join(normalized, original_filename)

        transfer_id = await handler.transfers.create_upload(client_id, path, size=size_est, sha256=None, direction="upload")
        handler.transfers.transfers[transfer_id]["source_path_server"] = tmp_path
        if original_filename:
            handler.transfers.transfers[transfer_id]["original_filename"] = original_filename
        await handler.transfers.set_state(transfer_id, TransferState.IN_PROGRESS)
        # Обновим прогресс
        await handler.transfers.update_progress(transfer_id, size_est, state=TransferState.IN_PROGRESS)
        return {"transfer_id": transfer_id, "received": size_est}
    except Exception as e:
        logger.error(f"❌ Error creating resumable transfer: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/files/upload/complete")
async def upload_complete(transfer_id: str = Form(...), handler = Depends(get_websocket_handler)):
    """Завершение resumable загрузки: вычисляем итоговый размер/sha и запускаем фоновой отправку на клиента."""
    t = handler.transfers.get(transfer_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transfer not found")
    src = t.get("source_path_server")
    if not src or not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Source file not found on server")
    # Опционально вычислим sha256 и установим размер
    try:
        import hashlib as _hashlib
        hasher = _hashlib.sha256()
        size_bytes = 0
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                if not chunk:
                    break
                size_bytes += len(chunk)
                hasher.update(chunk)
        handler.transfers.transfers[transfer_id]["size"] = size_bytes
        handler.transfers.transfers[transfer_id]["sha256"] = hasher.hexdigest()
    except Exception:
        pass

    # Запустим фоновую отправку на клиента
    try:
        _enqueue_or_fallback(handler, t.get("client_id"), transfer_id, src)
    except Exception as e:
        logger.error(f"❌ Failed to start background send for {transfer_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Failed to start background send for {transfer_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"transfer_id": transfer_id, "state": "started"}


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
        _enqueue_or_fallback(handler, t["client_id"], req.transfer_id, src, start_offset=start_offset)
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


