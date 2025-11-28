import os
import requests
import math
from celery.utils.log import get_task_logger
from .celery_app import celery

logger = get_task_logger(__name__)

CHUNK_SIZE = int(os.getenv("UPLOAD_TASK_CHUNK_SIZE", str(4 * 1024 * 1024)))  # default 4 MiB
CM_BASE = os.getenv("CM_BASE_URL", "http://client_manager:10000")


@celery.task(bind=True, max_retries=3, default_retry_delay=5)
def process_upload_task(self, transfer_id: str, client_id: str, src_path: str, start_offset: int = 0, chunk_size: int = None):
    """Worker task: read src_path and POST chunks to client_manager internal endpoints.

    This worker does NOT attempt to send WS messages directly; it posts chunks to
    `http://client_manager:10000/api/files/upload/chunk` and finally calls
    `/api/files/upload/complete`.
    """
    if chunk_size is None:
        chunk_size = CHUNK_SIZE

    # Получим мета для трансфера (path, original_filename) из client_manager, если нужно
    try:
        info_url = f"{CM_BASE}/api/files/transfers/{transfer_id}/status"
        rinfo = requests.get(info_url, timeout=10)
        if rinfo.status_code == 200:
            info = rinfo.json()
            target_path = info.get("path") or ""
            original_filename = info.get("original_filename")
        else:
            logger.warning(f"Could not fetch transfer info for {transfer_id}, status {rinfo.status_code}")
            target_path = ""
            original_filename = None
    except Exception:
        target_path = ""
        original_filename = None

    try:
        size = os.path.getsize(src_path)
    except Exception as e:
        logger.error(f"Source file not found for transfer {transfer_id}: {src_path} ({e})")
        raise

    total_chunks = math.ceil((size - start_offset) / chunk_size) if size > start_offset else 0
    offset = start_offset
    try:
        with open(src_path, "rb") as f:
            if start_offset:
                f.seek(start_offset)
            chunk_index = 0
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                files = {"file": (f"chunk_{chunk_index}", chunk, "application/octet-stream")}
                data = {
                    "client_id": client_id,
                    "path": target_path,
                    "offset": str(offset),
                    "transfer_id": transfer_id,
                }
                url = f"{CM_BASE}/api/files/upload/chunk"
                try:
                    resp = requests.post(url, data=data, files=files, timeout=60)
                    if resp.status_code >= 400:
                        logger.error(f"Chunk POST failed {resp.status_code}: {resp.text}")
                        raise Exception(f"Upstream error {resp.status_code}")
                    j = resp.json()
                    offset = int(j.get("received", offset + len(chunk)))
                except Exception as exc:
                    logger.error(f"Error posting chunk for transfer {transfer_id}: {exc}")
                    raise self.retry(exc=exc)
                chunk_index += 1

        # Complete
        urlc = f"{CM_BASE}/api/files/upload/complete"
        try:
            rc = requests.post(urlc, data={"transfer_id": transfer_id}, timeout=30)
            if rc.status_code >= 400:
                logger.error(f"Complete POST failed {rc.status_code}: {rc.text}")
                raise Exception("Complete failed")
        except Exception as exc:
            logger.error(f"Error posting complete for transfer {transfer_id}: {exc}")
            raise self.retry(exc=exc)

        logger.info(f"Upload task completed for transfer {transfer_id}")
        return True
    except self.MaxRetriesExceededError:
        logger.error(f"Max retries exceeded for transfer {transfer_id}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in upload task for {transfer_id}: {e}")
        raise
