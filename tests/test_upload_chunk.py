import os
import sys
import tempfile

THIS_DIR = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..', '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fastapi.testclient import TestClient

# Ensure JWT secret is set before app startup
os.environ.setdefault('JWT_SECRET_KEY', 'test-secret')

from client_manager.app.main import app


def test_upload_chunk_creates_transfer_and_writes():
    data = b"hello-chunked"
    headers = {}
    with TestClient(app) as client:
        files = {
            'file': ('chunk.bin', data, 'application/octet-stream')
        }
        form = {
            'client_id': 'test-client',
            'path': '/tmp/test_upload_chunk.bin',
            'offset': '0',
        }
        res = client.post('/api/files/upload/chunk', data=form, files=files)
        assert res.status_code == 200, res.text
        body = res.json()
        assert 'transfer_id' in body
        assert body.get('received', 0) >= len(data)
*** End Patch