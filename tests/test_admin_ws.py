import os
import json

import pytest
from fastapi.testclient import TestClient

# client-manager-plugin tests require python-multipart for FastAPI Form parsing.
pytest.importorskip("multipart", reason='python-multipart is required for client-manager-plugin e2e tests')

# Ensure JWT secret is set before app startup
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret')

from client_manager_plugin_app.main import app
from client_manager_plugin_app.core.security.auth_service import AuthService


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

