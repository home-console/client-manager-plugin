"""File-backed audit queue (JSONL).

Simple, process-local JSONL queue to persist audit events without SQL.
Not safe for concurrent multi-process writers/readers without external locking (e.g. flock).

API:
 - ensure_queue_dir(path)
 - enqueue(payload) -> id
 - fetch_pending(limit) -> list of {'id', 'payload'}
 - mark_sent(id) -> bool
 - purge_old(ttl_seconds) -> removes entries older than ttl

This module is intentionally lightweight and uses atomic file rename when rewriting.
"""
from typing import List, Dict, Any, Optional
import os
import json
import time
import uuid
import tempfile
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
try:
    import fcntl
    _have_fcntl = True
except Exception:
    _have_fcntl = False


def _get_queue_path() -> str:
    # configurable via env
    return os.getenv('AUDIT_QUEUE_FILE', '/var/lib/client_manager/audit_queue.jsonl')


def ensure_queue_dir():
    path = _get_queue_path()
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        logger.exception('Failed to ensure audit queue dir')


def enqueue(payload: Dict[str, Any]) -> Optional[str]:
    """Append payload to JSONL queue. Returns generated id or None."""
    try:
        ensure_queue_dir()
        rec = {
            'id': str(uuid.uuid4()),
            'payload': payload,
            'queued_at': time.time(),
            'sent': False,
            'sent_at': None
        }
        line = json.dumps(rec, ensure_ascii=False)
        path = _get_queue_path()
        if _have_fcntl:
            with open(path, 'a', encoding='utf-8') as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except Exception:
                    pass
                f.write(line + '\n')
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
        else:
            with _lock:
                with open(path, 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
        return rec['id']
    except Exception:
        logger.exception('Failed to enqueue audit to FS')
        return None


def fetch_pending(limit: int = 100) -> List[Dict[str, Any]]:
    """Return up to `limit` pending records (not sent).
    Each row: {'id': id, 'payload': payload}
    """
    out: List[Dict[str, Any]] = []
    path = _get_queue_path()
    try:
        if not os.path.exists(path):
            return out
        if _have_fcntl:
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                except Exception:
                    pass
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not rec.get('sent'):
                        out.append({'id': rec.get('id'), 'payload': rec.get('payload')})
                        if len(out) >= limit:
                            break
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
        else:
            with _lock:
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if not rec.get('sent'):
                            out.append({'id': rec.get('id'), 'payload': rec.get('payload')})
                            if len(out) >= limit:
                                break
    except Exception:
        logger.exception('Failed to fetch pending audits from FS')
    return out


def mark_sent(row_id: str) -> bool:
    """Mark a record as sent by rewriting the file (atomic rename)."""
    path = _get_queue_path()
    tmp_fd = None
    try:
        if not os.path.exists(path):
            return False
        dirp = os.path.dirname(path) or '.'
        fd, tmpname = tempfile.mkstemp(prefix='audit_queue_', dir=dirp)
        tmp_fd = fd
        # Use file lock while reading/writing
        try:
            if _have_fcntl:
                with open(path, 'r', encoding='utf-8') as in_f:
                    try:
                        fcntl.flock(in_f.fileno(), fcntl.LOCK_SH)
                    except Exception:
                        pass
                    recs = list(in_f)
                    try:
                        fcntl.flock(in_f.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                with open(tmpname, 'w', encoding='utf-8') as out_f:
                    for line in recs:
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            out_f.write(line)
                            continue
                        if rec.get('id') == row_id:
                            rec['sent'] = True
                            rec['sent_at'] = time.time()
                        out_f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                os.replace(tmpname, path)
            else:
                with _lock:
                    with open(path, 'r', encoding='utf-8') as in_f:
                        recs = list(in_f)
                    with os.fdopen(fd, 'w', encoding='utf-8') as out_f:
                        for line in recs:
                            if not line.strip():
                                continue
                            try:
                                rec = json.loads(line)
                            except Exception:
                                out_f.write(line)
                                continue
                            if rec.get('id') == row_id:
                                rec['sent'] = True
                                rec['sent_at'] = time.time()
                            out_f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                os.replace(tmpname, path)
        return True
    except Exception:
        logger.exception('Failed to mark audit row as sent in FS')
        try:
            if tmp_fd:
                os.close(tmp_fd)
        except Exception:
            pass
        return False


def purge_old(ttl_seconds: int = 60 * 60 * 24 * 7):
    """Remove entries older than TTL (queued_at). Rewrites file atomically."""
    path = _get_queue_path()
    try:
        if not os.path.exists(path):
            return
        cutoff = time.time() - ttl_seconds
        # Keep records that are either unsent, or sent but newer than cutoff
        try:
            if _have_fcntl:
                with open(path, 'r', encoding='utf-8') as in_f:
                    try:
                        fcntl.flock(in_f.fileno(), fcntl.LOCK_SH)
                    except Exception:
                        pass
                    recs = list(in_f)
                    try:
                        fcntl.flock(in_f.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                dirp = os.path.dirname(path) or '.'
                fd, tmpname = tempfile.mkstemp(prefix='audit_queue_purge_', dir=dirp)
                with os.fdopen(fd, 'w', encoding='utf-8') as out_f:
                    for line in recs:
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        sent = rec.get('sent', False)
                        sent_at = rec.get('sent_at') or rec.get('queued_at', 0)
                        if (not sent) or (sent_at >= cutoff):
                            out_f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                os.replace(tmpname, path)
            else:
                with _lock:
                    dirp = os.path.dirname(path) or '.'
                    fd, tmpname = tempfile.mkstemp(prefix='audit_queue_purge_', dir=dirp)
                    with os.fdopen(fd, 'w', encoding='utf-8') as out_f:
                        with open(path, 'r', encoding='utf-8') as in_f:
                            for line in in_f:
                                if not line.strip():
                                    continue
                                try:
                                    rec = json.loads(line)
                                except Exception:
                                    continue
                                sent = rec.get('sent', False)
                                sent_at = rec.get('sent_at') or rec.get('queued_at', 0)
                                if (not sent) or (sent_at >= cutoff):
                                    out_f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                    os.replace(tmpname, path)
    except Exception:
        logger.exception('Failed to purge old audit entries')
