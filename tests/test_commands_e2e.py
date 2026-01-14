import asyncio
import json
import os
import sys
import time
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure the plugin root is on sys.path
THIS_DIR = os.path.abspath(os.path.dirname(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(THIS_DIR, '..'))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from app.main import create_app
from app.dependencies import get_websocket_handler


@pytest.fixture(scope="function")
def app() -> FastAPI:
    return create_app()


@pytest.fixture(scope="function")
def client(app: FastAPI):
    return TestClient(app)


def register_fake_client(client: TestClient, client_id: str = "client-001") -> None:
    """Регистрирует тестового клиента через прямой вызов WebSocket handler (без реального ws)."""
    handler = get_websocket_handler()
    # Подменяем хранение клиента напрямую
    handler.client_manager.clients[client_id] = handler.client_manager.clients.get(client_id) or None
    # Имитация, что клиент "подключен" в менеджере сокетов
    handler.websocket_manager.connections[client_id] = None


def test_status_returns_404_for_unknown_command(client: TestClient):
    resp = client.get("/api/commands/unknown/status")
    assert resp.status_code == 404


def test_status_active_then_result_flow(client: TestClient):
    register_fake_client(client, "client-001")
    handler = get_websocket_handler()

    # Добавляем активную команду вручную
    command_id = "cmd_test_1"
    asyncio.get_event_loop().run_until_complete(
        handler.command_handler.add_command("client-001", command_id, "echo hi", timeout=5)
    )

    # 1) Проверяем, что статус показывает active
    r1 = client.get(f"/api/commands/{command_id}/status")
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["command_id"] == command_id
    assert data1["status"] in ("pending", "running", "cancelling")

    # 2) Кладем финальный результат и проверяем статус снова
    result_msg = {
        "type": "command_result",
        "data": {
            "command_id": command_id,
            "success": True,
            "result": "ok",
            "error": None,
        },
    }
    asyncio.get_event_loop().run_until_complete(
        handler.command_handler.handle_command_result(None, result_msg, "client-001")
    )

    r2 = client.get(f"/api/commands/{command_id}/status")
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["command_id"] == command_id
    assert data2["success"] is True
    assert data2["result"] == "ok"


def test_cancel_success_path(client: TestClient):
    register_fake_client(client, "client-002")
    handler = get_websocket_handler()
    command_id = "cmd_cancel_ok"

    # Подготовка активной команды
    asyncio.get_event_loop().run_until_complete(
        handler.command_handler.add_command("client-002", command_id, "sleep 30", timeout=30)
    )

    # Имитация, что отправка сообщения клиенту всегда успешна
    async def fake_send_message(client_id: str, payload: Any) -> bool:
        return True

    handler.websocket_manager.send_message = fake_send_message  # type: ignore

    # Запускаем отмену через REST
    r = client.post(f"/api/commands/client-002/cancel", params={"command_id": command_id, "timeout": 2})

    # Так как обработчик ждёт результата, подкинем результат отмены вручную
    cancel_result = {
        "type": "command_result",
        "data": {
            "command_id": command_id,
            "success": False,
            "result": "",
            "error": "cancelled",
        },
    }
    asyncio.get_event_loop().run_until_complete(
        handler.command_handler.handle_command_result(None, cancel_result, "client-002")
    )

    # Второй вызов отдаст результат
    r2 = client.post(f"/api/commands/client-002/cancel", params={"command_id": command_id, "timeout": 2})
    assert r2.status_code == 200
    data = r2.json()
    assert data["command_id"] == command_id
    assert data["success"] is False
    assert data["error"] == "cancelled"


def test_cancel_timeout(client: TestClient):
    register_fake_client(client, "client-003")
    handler = get_websocket_handler()
    command_id = "cmd_cancel_timeout"

    asyncio.get_event_loop().run_until_complete(
        handler.command_handler.add_command("client-003", command_id, "sleep 30", timeout=30)
    )

    async def fake_send_message(client_id: str, payload: Any) -> bool:
        return True

    handler.websocket_manager.send_message = fake_send_message  # type: ignore

    r = client.post(f"/api/commands/client-003/cancel", params={"command_id": command_id, "timeout": 1})
    # Нет результата в срок — ожидаем 408
    assert r.status_code == 408


