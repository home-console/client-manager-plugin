"""
Роуты для управления клиентами
"""

import logging
from typing import List
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ..core.models import ClientInfo
from ..core.websocket_handler import WebSocketHandler

logger = logging.getLogger(__name__)

router = APIRouter()

# Получаем экземпляр WebSocket обработчика (синглтон)
websocket_handler = WebSocketHandler()


@router.get("/", response_class=HTMLResponse)
async def root():
    """Главная страница с интерфейсом управления"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Remote Client Manager</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            .client { border: 1px solid #ccc; margin: 10px 0; padding: 10px; }
            .connected { background-color: #d4edda; }
            .disconnected { background-color: #f8d7da; }
            button { margin: 5px; padding: 5px 10px; }
            input { margin: 5px; padding: 5px; }
        </style>
    </head>
    <body>
        <h1>Remote Client Manager</h1>
        <div id="clients"></div>
        <script>
            async function loadClients() {
                const response = await fetch('/api/clients');
                const clients = await response.json();
                const div = document.getElementById('clients');
                div.innerHTML = '';
                clients.forEach(client => {
                    const clientDiv = document.createElement('div');
                    clientDiv.className = `client ${client.status}`;
                    clientDiv.innerHTML = `
                        <h3>${client.hostname} (${client.id})</h3>
                        <p>IP: ${client.ip}:${client.port}</p>
                        <p>Status: ${client.status}</p>
                        <p>Connected: ${client.connected_at}</p>
                        <p>Last heartbeat: ${client.last_heartbeat}</p>
                        <input type="text" id="cmd_${client.id}" placeholder="Команда">
                        <button onclick="sendCommand('${client.id}')">Выполнить</button>
                        <button onclick="sendCancel('${client.id}')">Отменить</button>
                    `;
                    div.appendChild(clientDiv);
                });
            }
            
            async function sendCommand(clientId) {
                const command = document.getElementById(`cmd_${clientId}`).value;
                if (!command) return;
                
                const response = await fetch(`/api/commands/${clientId}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({command: command})
                });
                
                if (response.ok) {
                    alert('Команда отправлена');
                } else {
                    alert('Ошибка отправки команды');
                }
            }
            
            async function sendCancel(clientId) {
                const response = await fetch(`/api/commands/${clientId}/cancel`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    alert('Отмена отправлена');
                } else {
                    alert('Ошибка отправки отмены');
                }
            }
            
            // Обновляем список каждые 5 секунд
            setInterval(loadClients, 5000);
            loadClients();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get("/api/clients", response_model=List[ClientInfo])
async def get_clients():
    """Получить список всех клиентов"""
    return list(websocket_handler.client_manager.get_all_clients().values())


@router.get("/api/clients/{client_id}", response_model=ClientInfo)
async def get_client(client_id: str):
    """Получить информацию о конкретном клиенте"""
    client_info = websocket_handler.client_manager.get_client_info(client_id)
    if not client_info:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return client_info


@router.delete("/api/clients/{client_id}")
async def disconnect_client(client_id: str):
    """Отключить клиента"""
    websocket = websocket_handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    try:
        await websocket.close()
        await websocket_handler.client_manager.unregister_client(client_id)
        return {"message": f"Клиент {client_id} отключен"}
    except Exception as e:
        logger.error(f"Ошибка отключения клиента {client_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка отключения клиента")
