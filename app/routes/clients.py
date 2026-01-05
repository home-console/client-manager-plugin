"""
Роуты для управления клиентами
"""

import logging
from typing import List
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Depends, Request
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
                    const deviceType = client.device_type || 'generic';
                    const tags = client.tags || [];
                    const capabilities = client.capabilities || [];

                    clientDiv.innerHTML = `
                        <h3>${client.hostname} (${client.id})</h3>
                        <p>IP: ${client.ip}:${client.port} | Тип: <strong>${deviceType}</strong></p>
                        <p>Status: ${client.status}</p>
                        <p>Connected: ${client.connected_at}</p>
                        <p>Last heartbeat: ${client.last_heartbeat}</p>
                        ${tags.length > 0 ? `<p>Теги: ${tags.join(', ')}</p>` : ''}
                        <div style="margin: 10px 0;">
                            <select id="universal_cmd_${client.id}" style="margin-right: 10px;">
                                <option value="system.info">Системная информация</option>
                                <option value="network.info">Сетевая информация</option>
                                <option value="file.list">Список файлов</option>
                                <option value="disk.usage">Использование диска</option>
                                <option value="process.list">Список процессов</option>
                            </select>
                            <input type="text" id="cmd_path_${client.id}" placeholder="Путь (опционально)" style="width: 200px;">
                        </div>
                        <button onclick="sendUniversalCommand('${client.id}')">Универсальная команда</button>
                        <input type="text" id="cmd_${client.id}" placeholder="Нативная команда" style="margin-left: 10px; width: 300px;">
                        <button onclick="sendCommand('${client.id}')">Нативная команда</button>
                        <button onclick="sendCancel('${client.id}')">Отменить</button>
                        <br>
                        <div style="margin-top: 10px;">
                            <input type="text" id="cloud_path_${client.id}" placeholder="Путь к файлу на устройстве" style="width: 200px;">
                            <select id="cloud_service_${client.id}" style="margin: 0 5px;">
                                <option value="yandex_disk">Яндекс.Диск</option>
                                <option value="icloud">iCloud</option>
                            </select>
                            <input type="text" id="cloud_dest_${client.id}" placeholder="Путь в облаке" style="width: 150px;">
                            <button onclick="uploadToCloud('${client.id}')">В облако</button>
                            <button onclick="downloadFromCloud('${client.id}')">Из облака</button>
                        </div>
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

            async function sendUniversalCommand(clientId) {
                const cmdSelect = document.getElementById(`universal_cmd_${clientId}`);
                const pathInput = document.getElementById(`cmd_path_${clientId}`);

                const commandType = cmdSelect.value;
                const path = pathInput.value.trim();

                const requestBody = {
                    command_type: commandType,
                    params: {},
                    async_execution: false
                };

                // Добавляем параметры в зависимости от команды
                if (path && (commandType.startsWith('file.') || commandType === 'disk.usage')) {
                    if (commandType === 'file.list') {
                        requestBody.params = { path: path || '.' };
                    }
                }

                try {
                    const response = await fetch(`/api/universal/${clientId}/execute`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(requestBody)
                    });

                    const result = await response.json();

                    if (response.ok) {
                        alert(`Результат:\\n${JSON.stringify(result.result, null, 2)}`);
                    } else {
                        alert(`Ошибка: ${result.detail}`);
                    }
                } catch (error) {
                    alert(`Ошибка выполнения: ${error.message}`);
                }
            }

            async function uploadToCloud(clientId) {
                const devicePath = document.getElementById(`cloud_path_${clientId}`).value;
                const cloudService = document.getElementById(`cloud_service_${clientId}`).value;
                const cloudPath = document.getElementById(`cloud_dest_${clientId}`).value;

                if (!devicePath || !cloudPath) {
                    alert('Заполните все поля!');
                    return;
                }

                const requestBody = {
                    device_id: clientId,
                    remote_path: devicePath,
                    cloud_service: cloudService,
                    cloud_path: cloudPath,
                    delete_after: confirm('Удалить файл с устройства после загрузки?')
                };

                try {
                    const response = await fetch('/api/cloud/upload', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(requestBody)
                    });

                    const result = await response.json();

                    if (response.ok) {
                        alert(`Файл загружен в ${cloudService}!\\nРазмер: ${result.file_size} bytes`);
                    } else {
                        alert(`Ошибка загрузки: ${result.detail || 'Неизвестная ошибка'}`);
                    }
                } catch (error) {
                    alert(`Ошибка: ${error.message}`);
                }
            }

            async function downloadFromCloud(clientId) {
                const devicePath = document.getElementById(`cloud_path_${clientId}`).value;
                const cloudService = document.getElementById(`cloud_service_${clientId}`).value;
                const cloudPath = document.getElementById(`cloud_dest_${clientId}`).value;

                if (!devicePath || !cloudPath) {
                    alert('Заполните все поля!');
                    return;
                }

                const requestBody = {
                    device_id: clientId,
                    local_path: devicePath,
                    cloud_service: cloudService,
                    cloud_path: cloudPath
                };

                try {
                    const response = await fetch('/api/cloud/download', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(requestBody)
                    });

                    const result = await response.json();

                    if (response.ok) {
                        alert(`Файл скачан из ${cloudService}!\\nРазмер: ${result.file_size} bytes`);
                    } else {
                        alert(`Ошибка скачивания: ${result.detail || 'Неизвестная ошибка'}`);
                    }
                } catch (error) {
                    alert(`Ошибка: ${error.message}`);
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


@router.get("/clients", response_model=List[ClientInfo], tags=["Clients"])
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


@router.get("/clients/{client_id}", response_model=ClientInfo, tags=["Clients"])
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



@router.delete("/clients/{client_id}", tags=["Clients"])
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
