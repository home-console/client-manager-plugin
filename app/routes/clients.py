"""
Роуты для управления клиентами
"""

import logging
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse

from ..core.models import ClientInfo
from ..dependencies import get_websocket_handler

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.get("/api/clients", response_model=List[ClientInfo], tags=["clients"])
async def get_clients(handler = Depends(get_websocket_handler)):
    """
    Получить список всех подключенных клиентов
    
    Возвращает информацию о всех клиентах, которые сейчас подключены к серверу через WebSocket.
    
    **Возвращаемые данные:**
    - `id`: Уникальный идентификатор клиента
    - `hostname`: Имя хоста клиента
    - `ip`: IP адрес клиента
    - `port`: Порт клиента
    - `status`: Статус клиента (connected/disconnected)
    - `connected_at`: Время подключения (ISO format)
    - `last_heartbeat`: Время последнего heartbeat (ISO format)
    
    **Пример ответа:**
    ```json
    [
      {
        "id": "client-123",
        "hostname": "laptop-user",
        "ip": "192.168.1.100",
        "port": 54321,
        "status": "connected",
        "connected_at": "2025-01-15T10:30:00",
        "last_heartbeat": "2025-01-15T10:35:00"
      }
    ]
    ```
    """
    return list(handler.get_all_clients().values())


@router.get("/api/clients/{client_id}", response_model=ClientInfo, tags=["clients"])
async def get_client(client_id: str, handler = Depends(get_websocket_handler)):
    """
    Получить информацию о конкретном клиенте
    
    **Параметры:**
    - `client_id`: Уникальный идентификатор клиента
    
    **Возвращает:**
    Полную информацию о клиенте включая статус подключения и последнюю активность.
    
    **Ошибки:**
    - `404`: Клиент с указанным ID не найден
    
    **Пример запроса:**
    ```
    GET /api/clients/client-123
    ```
    
    **Пример ответа:**
    ```json
    {
      "id": "client-123",
      "hostname": "laptop-user",
      "ip": "192.168.1.100",
      "port": 54321,
      "status": "connected",
      "connected_at": "2025-01-15T10:30:00",
      "last_heartbeat": "2025-01-15T10:35:00"
    }
    ```
    """
    client_info = handler.get_client_info(client_id)
    if not client_info:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return client_info


@router.delete("/api/clients/{client_id}", tags=["clients"])
async def disconnect_client(client_id: str, handler = Depends(get_websocket_handler)):
    """
    Принудительно отключить клиента от сервера
    
    Закрывает WebSocket соединение с указанным клиентом и удаляет его из списка активных клиентов.
    
    **Параметры:**
    - `client_id`: Уникальный идентификатор клиента
    
    **Возвращает:**
    Сообщение об успешном отключении.
    
    **Ошибки:**
    - `404`: Клиент с указанным ID не найден
    - `500`: Ошибка при отключении клиента
    
    **Пример запроса:**
    ```
    DELETE /api/clients/client-123
    ```
    
    **Пример ответа:**
    ```json
    {
      "message": "Клиент client-123 отключен"
    }
    ```
    
    **Важно:**
    Это действие немедленно разрывает соединение с клиентом. Если клиент был в процессе выполнения команды, она будет прервана.
    """
    websocket = handler.client_manager.get_client(client_id)
    if not websocket:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    
    try:
        await websocket.close()
        await handler.client_manager.unregister_client(client_id)
        return {"message": f"Клиент {client_id} отключен"}
    except Exception as e:
        logger.error(f"Ошибка отключения клиента {client_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка отключения клиента")
