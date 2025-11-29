"""Простейшая синхронная обёртка для записи/чтения очереди аудита в Postgres.

Использует переменную окружения `DATABASE_URL` или `PGBOUNCER_URL`.
Реализация минимальна и предназначена для использования в `asyncio.to_thread`.
"""
from typing import List, Dict, Any, Optional
import os
import json
import time
import logging

logger = logging.getLogger(__name__)


def _get_dsn() -> Optional[str]:
    return os.getenv('DATABASE_URL') or os.getenv('PGBOUNCER_URL') or os.getenv('PG_URL')


def _connect():
    dsn = _get_dsn()
    if not dsn:
        raise RuntimeError('DATABASE_URL/PGBOUNCER_URL not set')
    try:
        import psycopg
    except Exception as e:
        raise RuntimeError('psycopg is required for DB persistence: %s' % e)
    # use autocommit connection for simplicity
    return psycopg.connect(dsn, autocommit=True)


def ensure_tables():
    """Create audit queue table if not exists."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_queue (
                id BIGSERIAL PRIMARY KEY,
                session_id TEXT,
                client_id TEXT,
                event TEXT,
                payload JSONB,
                ts DOUBLE PRECISION,
                queued_at TIMESTAMPTZ DEFAULT now(),
                sent BOOLEAN DEFAULT false,
                sent_at TIMESTAMPTZ NULL
            )
            """
        )
        cur.close()
        conn.close()
    except Exception:
        logger.exception('Failed to ensure audit_queue table')


def enqueue_audit(payload: Dict[str, Any]) -> Optional[int]:
    """Insert payload into audit_queue and return inserted id or None on error."""
    try:
        conn = _connect()
        cur = conn.cursor()
        session_id = payload.get('session_id')
        client_id = payload.get('client_id')
        event = payload.get('event')
        ts = float(payload.get('ts') or time.time())
        cur.execute(
            """
            INSERT INTO audit_queue (session_id, client_id, event, payload, ts)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (session_id, client_id, event, json.dumps(payload), ts)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None
    except Exception:
        logger.exception('Failed to enqueue audit into DB')
        return None


def fetch_pending(limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch up to `limit` pending audit rows (sent=false).
    Returns list of dicts with keys: id, payload
    """
    out = []
    try:
        conn = _connect()
        cur = conn.cursor()
        # select pending rows
        cur.execute(
            "SELECT id, payload FROM audit_queue WHERE sent = false ORDER BY queued_at LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        for r in rows:
            rid, payload = r[0], r[1]
            out.append({'id': rid, 'payload': payload})
        cur.close()
        conn.close()
    except Exception:
        logger.exception('Failed to fetch pending audits from DB')
    return out


def mark_sent(row_id: int) -> bool:
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("UPDATE audit_queue SET sent = true, sent_at = now() WHERE id = %s", (row_id,))
        cur.close()
        conn.close()
        return True
    except Exception:
        logger.exception('Failed to mark audit row as sent')
        return False
