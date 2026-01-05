"""Endpoints for admin->client messaging (internal use).

This exposes a minimal endpoint `/api/admin/send_message` which accepts
JSON {"client_id": "...", "message": {...}} and forwards the message
to the connected client over its WebSocket. Intended for internal use by
`core_service` (must pass ADMIN_TOKEN header).
"""

from fastapi import APIRouter, HTTPException, Request, Depends
from typing import Dict, Any
import os
import logging
import time
import base64
import hmac
import hashlib
import json
from typing import Optional

# Optional PyJWT support for RS256
try:
    import jwt as _pyjwt
except Exception:
    _pyjwt = None

from ..dependencies import get_websocket_handler

router = APIRouter()
logger = logging.getLogger(__name__)


def _base64url_decode(inp: str) -> bytes:
    rem = len(inp) % 4
    if rem:
        inp += '=' * (4 - rem)
    return base64.urlsafe_b64decode(inp.encode('utf-8'))


def verify_jwt(token: str) -> Optional[Dict[str, Any]]:
    """Verify JWT using RS256 (public key) or HS256 (shared secret).
    Returns payload dict on success or None on failure.
    """
    try:
        # Try to detect algorithm from header
        if _pyjwt:
            try:
                hdr = _pyjwt.get_unverified_header(token)
                alg = hdr.get('alg', '').upper()
            except Exception:
                return None
        else:
            # Fallback: parse header manually
            try:
                header_b = token.split('.')[0]
                header = json.loads(_base64url_decode(header_b))
                alg = header.get('alg', '').upper()
            except Exception:
                return None

        if alg == 'RS256':
            pub = os.getenv('ADMIN_JWT_PUBLIC_KEY') or None
            pub_file = os.getenv('ADMIN_JWT_PUBLIC_KEY_FILE') or None
            if not pub and pub_file:
                try:
                    with open(pub_file, 'r') as f:
                        pub = f.read()
                except Exception:
                    return None
            if not pub:
                return None
            if not _pyjwt:
                # PyJWT required for RS256 verification
                return None
            try:
                payload = _pyjwt.decode(token, pub, algorithms=['RS256'], audience='client_manager', options={'require_exp': False})
                # exp checked by library if present
                return payload
            except Exception:
                return None
        elif alg == 'HS256' or True:
            # HS256 or fallback to ADMIN_JWT_SECRET
            secret = os.getenv('ADMIN_JWT_SECRET', '')
            if not secret:
                # legacy: maybe ADMIN_TOKEN is used instead
                return None
            if _pyjwt:
                try:
                    payload = _pyjwt.decode(token, secret, algorithms=['HS256'], audience='client_manager', options={'require_exp': False})
                    return payload
                except Exception:
                    return None
            # fallback manual HMAC verification
            try:
                parts = token.split('.')
                if len(parts) != 3:
                    return None
                header_b, payload_b, sig_b = parts
                signing = (header_b + '.' + payload_b).encode('utf-8')
                sig = _base64url_decode(sig_b)
                expected = hmac.new(secret.encode('utf-8'), signing, hashlib.sha256).digest()
                if not hmac.compare_digest(expected, sig):
                    return None
                payload = json.loads(_base64url_decode(payload_b))
                if 'exp' in payload and int(time.time()) > int(payload['exp']):
                    return None
                return payload
            except Exception:
                return None
    except Exception:
        return None


@router.post("/admin/send_message", tags=["Admin"])
async def admin_send_message(payload: Dict[str, Any], request: Request, handler = Depends(get_websocket_handler)):
    # Prefer JWT signed with HS256 when ADMIN_JWT_SECRET is set. Fallback to simple ADMIN_TOKEN.
    jwt_secret = os.getenv('ADMIN_JWT_SECRET', '')
    auth_hdr = request.headers.get('authorization', '')

    if auth_hdr.startswith('Bearer '):
        token = auth_hdr.split(' ', 1)[1]
        payload_jwt = verify_jwt(token)
        if payload_jwt is None:
            # fallback to legacy ADMIN_TOKEN if set
            admin_token = os.getenv('ADMIN_TOKEN', '')
            if admin_token and token == admin_token:
                payload_jwt = {'legacy': True}
            else:
                raise HTTPException(status_code=403, detail='invalid token')
    else:
        # No Bearer header — try ADMIN_TOKEN headerless match
        admin_token = os.getenv('ADMIN_TOKEN', '')
        if admin_token:
            raise HTTPException(status_code=403, detail='forbidden')

    client_id = payload.get('client_id')
    message = payload.get('message')
    if not client_id or not message:
        raise HTTPException(status_code=400, detail='client_id and message required')

    try:
        ok = await handler.websocket_manager.send_message(client_id, json.dumps(message))
    except Exception as e:
        logger.exception('failed send_message')
        raise HTTPException(status_code=500, detail=str(e))

    if not ok:
        raise HTTPException(status_code=404, detail='client not connected')
    return {'ok': True}
