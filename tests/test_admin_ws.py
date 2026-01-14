import os
import sys
import json

# Ensure the plugin root is on sys.path
THIS_DIR = os.path.abspath(os.path.dirname(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..'))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from fastapi.testclient import TestClient

# Ensure JWT secret is set before app startup
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret')

from app.main import app
from app.core.security.auth_service import AuthService


def test_admin_ws_connect_and_ping():
    # Create a token with the same secret
    auth = AuthService(secret_key='test-secret')
    token = auth.create_token('test_admin', permissions=['admin'])

    with TestClient(app) as client:
        # Connect to admin websocket with token as query param
        with client.websocket_connect(f"/admin/ws?token={token}") as ws:
            # First message should be client_list
            msg = ws.receive_json()
            assert isinstance(msg, dict)
            assert msg.get('type') == 'client_list'

            # Send ping and expect pong
            ws.send_json({'type': 'ping'})
            resp = ws.receive_json()
            assert resp.get('type') == 'pong'

            # Request clients explicitly
            ws.send_json({'type': 'get_clients'})
            resp2 = ws.receive_json()
            assert resp2.get('type') == 'client_list'

