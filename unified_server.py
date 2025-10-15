#!/usr/bin/env python3
"""
Единый сервер с WebSocket и FastAPI REST API
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Set, Optional, Any
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Глобальные переменные для хранения состояния
connected_clients: Dict[str, WebSocket] = {}
client_info: Dict[str, dict] = {}
command_history: list = []
command_results: Dict[str, dict] = {}

class CommandRequest(BaseModel):
    # Простой режим: плоская строка команды
    command: Optional[str] = None
    client_id: Optional[str] = None
    # Модульный режим: имя и параметры
    name: Optional[str] = None
    params: Optional[dict] = None  # произвольные параметры для команды

class CommandResponse(BaseModel):
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    client_id: Optional[str] = None

class ClientInfo(BaseModel):
    id: str
    hostname: str
    ip: str
    port: int
    connected_at: str
    last_heartbeat: str
    status: str
    capabilities: list

class UnifiedServer:
    """Единый сервер с WebSocket и REST API"""
    
    def __init__(self):
        self.clients = connected_clients
        self.client_info = client_info
        self.command_history = command_history
        self.command_results = command_results
    
    async def register_client(self, websocket: WebSocket, client_data: dict) -> str:
        """Регистрация нового клиента"""
        client_id = client_data.get('client_id', f"client_{int(time.time())}")
        hostname = client_data.get('hostname', 'unknown')
        ip = websocket.client.host if websocket.client else 'unknown'
        port = websocket.client.port if websocket.client else 0
        
        # Создаем информацию о клиенте
        client_info_obj = {
            'id': client_id,
            'hostname': hostname,
            'ip': ip,
            'port': port,
            'connected_at': datetime.now().isoformat(),
            'last_heartbeat': datetime.now().isoformat(),
            'status': 'connected',
            'capabilities': client_data.get('capabilities', [])
        }
        
        # Сохраняем клиента
        self.clients[client_id] = websocket
        self.client_info[client_id] = client_info_obj
        
        logger.info(f"✅ Клиент зарегистрирован: {client_id} ({hostname})")
        logger.info(f"📊 Всего клиентов: {len(self.clients)}")
        return client_id
    
    async def unregister_client(self, client_id: str):
        """Отключение клиента"""
        if client_id in self.clients:
            del self.clients[client_id]
        if client_id in self.client_info:
            self.client_info[client_id]['status'] = 'disconnected'
        
        logger.info(f"❌ Клиент отключен: {client_id}")
    
    async def handle_client_message(self, websocket: WebSocket, message: dict, client_id: str):
        """Обработка сообщений от клиентов (поддержка payload в поле data и на верхнем уровне)"""
        msg_type = message.get('type')
        # Нормализуем данные сообщения
        payload = message.get('data') if isinstance(message.get('data'), dict) else message
        
        if msg_type == 'register':
            # Клиент уже зарегистрирован, отправляем подтверждение
            response = {
                "type": "registration_success",
                "client_id": client_id,
                "message": "Клиент успешно зарегистрирован"
            }
            await websocket.send_text(json.dumps(response))
            
        elif msg_type == 'heartbeat':
            # Обновляем время последнего heartbeat
            if client_id in self.client_info:
                self.client_info[client_id]['last_heartbeat'] = datetime.now().isoformat()
                
        elif msg_type == 'command_result':
            # Результат выполнения команды
            command_id = (payload or {}).get('command_id')
            result = (payload or {}).get('result')
            success = (payload or {}).get('success', False)
            error = (payload or {}).get('error')
            # Если результат пришёл строкой, попробуем распарсить JSON
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    result = parsed
                except Exception:
                    pass
            # Если внутри результата есть поле output как строка JSON — распарсим и его
            if isinstance(result, dict):
                output_value = result.get('output')
                if isinstance(output_value, str):
                    try:
                        parsed_output = json.loads(output_value)
                        result['output'] = parsed_output
                    except Exception:
                        pass
            
            # Сохраняем результат
            self.command_results[command_id] = {
                'success': success,
                'result': result,
                'error': error,
                'client_id': client_id,
                'timestamp': datetime.now().isoformat()
            }
            
            logger.info(f"📋 Результат команды {command_id}: {result if success else error}")
            
        elif msg_type == 'error':
            # Ошибка от клиента
            error = message.get('error')
            logger.error(f"❌ Ошибка от клиента {client_id}: {error}")
    
    async def websocket_handler(self, websocket: WebSocket):
        """Обработчик WebSocket соединений"""
        await websocket.accept()
        client_id = None
        
        try:
            logger.info(f"🔌 Новое WebSocket подключение: {websocket.client}")
            
            while True:
                try:
                    # Пытаемся получить текстовое сообщение напрямую
                    try:
                        message = await websocket.receive_text()
                    except WebSocketDisconnect:
                        raise
                    except Exception:
                        # Фолбэк: читаем универсально и фильтруем служебные
                        evt = await websocket.receive()
                        if isinstance(evt, dict):
                            evt_type = evt.get('type')
                            if evt_type in ('websocket.disconnect', 'websocket.close'):
                                raise WebSocketDisconnect()
                            if evt_type not in ('websocket.receive',):
                                logger.debug(f"🔧 Служебное сообщение WebSocket: {evt_type}")
                                continue
                        if 'text' in evt and evt['text'] is not None:
                            message = evt['text']
                        elif 'bytes' in evt and evt['bytes'] is not None:
                            try:
                                message = evt['bytes'].decode('utf-8', errors='replace')
                            except Exception:
                                logger.error("❌ Невозможно декодировать бинарное сообщение как UTF-8")
                                continue
                        else:
                            logger.debug("🔧 Пустое или служебное сообщение, продолжаем")
                            continue

                    logger.info(f"📨 Получено сырое сообщение: {message}")
                    data = json.loads(message)
                    logger.info(f"📨 Получено сообщение: {data}")
                    # Нормализуем структуру: поддерживаем и {type, data:{...}}, и плоский вариант
                    msg_type = data.get('type')
                    payload = data.get('data') if isinstance(data.get('data'), dict) else data
                    
                    # Если это регистрация, регистрируем клиента
                    if msg_type == 'register':
                        logger.info(f"🔐 Регистрация клиента: {payload}")
                        client_id = await self.register_client(websocket, payload)
                        logger.info(f"✅ Клиент зарегистрирован с ID: {client_id}")
                    
                    # Обрабатываем сообщение
                    if client_id:
                        await self.handle_client_message(websocket, {"type": msg_type, "data": payload}, client_id)
                        
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Неверный JSON от клиента: {e}")
                    # Продолжаем слушать, не рвём соединение из-за разового мусора
                    continue
                except WebSocketDisconnect:
                    # Корректный выход из цикла
                    break
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки сообщения: {e}")
                    # Не рвём цикл, ждём следующее сообщение
                    continue
                    
        except WebSocketDisconnect:
            logger.info(f"🔌 WebSocket отключен: {websocket.client}")
        except Exception as e:
            logger.error(f"❌ Ошибка WebSocket соединения: {e}")
        finally:
            if client_id:
                await self.unregister_client(client_id)
    
    async def send_command_to_client(self, client_id: str, command: Any) -> str:
        """Отправка команды клиенту (строкой или структурой)"""
        if client_id not in self.clients:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        
        # Генерируем ID команды
        command_id = f"cmd_{int(time.time())}_{client_id}"
        
        # Создаем сообщение команды
        if isinstance(command, dict):
            command_msg = dict(command)
            command_msg["command_id"] = command_id
            command_msg.setdefault("type", "command")
        else:
            command_msg = {
                "type": "command",
                "command": str(command),
                "command_id": command_id
            }
        
        try:
            # Отправляем команду
            await self.clients[client_id].send_text(json.dumps(command_msg))
            
            # Добавляем в историю
            self.command_history.append({
                'command_id': command_id,
                'client_id': client_id,
                'command': command,
                'timestamp': datetime.now().isoformat(),
                'status': 'sent'
            })
            
            logger.info(f"📤 Команда отправлена клиенту {client_id}: {command}")
            return command_id
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки команды: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка отправки команды: {e}")
    
    def get_clients_list(self) -> list:
        """Получение списка клиентов"""
        return [info for info in self.client_info.values() if info['status'] == 'connected']
    
    def get_client_info(self, client_id: str) -> Optional[dict]:
        """Получение информации о клиенте"""
        return self.client_info.get(client_id)
    
    def get_command_history(self, limit: int = 100) -> list:
        """Получение истории команд"""
        return self.command_history[-limit:]
    
    def get_command_result(self, command_id: str) -> Optional[dict]:
        """Получение результата команды"""
        return self.command_results.get(command_id)

# Создаем экземпляр сервера
server = UnifiedServer()

# Создаем FastAPI приложение
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    logger.info("🚀 Запуск единого сервера...")
    yield
    logger.info("🛑 Остановка сервера...")

app = FastAPI(
    title="Remote Client Manager",
    description="Единый сервер для управления удаленными клиентами",
    version="1.0.0",
    lifespan=lifespan
)

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint для клиентов"""
    await server.websocket_handler(websocket)

# REST API endpoints
@app.get("/")
async def root():
    """Главная страница"""
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Remote Client Manager</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 800px; margin: 0 auto; }
            .client { border: 1px solid #ddd; padding: 10px; margin: 10px 0; border-radius: 5px; }
            .command { background: #f5f5f5; padding: 10px; margin: 10px 0; border-radius: 5px; }
            button { background: #007bff; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
            button:hover { background: #0056b3; }
            input[type="text"] { padding: 8px; border: 1px solid #ddd; border-radius: 4px; width: 300px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Remote Client Manager</h1>
            <div id="clients"></div>
            <div class="command">
                <h3>Отправить команду</h3>
                <input type="text" id="command" placeholder="Введите команду">
                <button onclick="sendCommand()">Отправить</button>
            </div>
            <div id="results"></div>
        </div>
        <script>
            async function loadClients() {
                const response = await fetch('/api/clients');
                const data = await response.json();
                const clientsDiv = document.getElementById('clients');
                clientsDiv.innerHTML = '<h2>Подключенные клиенты (' + data.count + ')</h2>';
                data.clients.forEach(client => {
                    clientsDiv.innerHTML += `
                        <div class="client">
                            <strong>${client.hostname}</strong> (${client.id})<br>
                            IP: ${client.ip}:${client.port}<br>
                            Подключен: ${new Date(client.connected_at).toLocaleString()}<br>
                            Последний heartbeat: ${new Date(client.last_heartbeat).toLocaleString()}
                        </div>
                    `;
                });
            }
            
            async function sendCommand() {
                const command = document.getElementById('command').value;
                if (!command) return;
                
                const response = await fetch('/api/commands', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: command })
                });
                
                const result = await response.json();
                document.getElementById('results').innerHTML = `
                    <div class="command">
                        <strong>Команда:</strong> ${command}<br>
                        <strong>Результат:</strong> ${result.result || result.error}<br>
                        <strong>Статус:</strong> ${result.success ? 'Успешно' : 'Ошибка'}
                    </div>
                `;
            }
            
            // Загружаем клиентов при загрузке страницы
            loadClients();
            setInterval(loadClients, 5000); // Обновляем каждые 5 секунд
        </script>
    </body>
    </html>
    """)

@app.get("/api/clients", response_model=dict)
async def get_clients():
    """Получение списка подключенных клиентов"""
    clients = server.get_clients_list()
    return {
        "clients": clients,
        "count": len(clients)
    }

@app.get("/api/clients/{client_id}", response_model=ClientInfo)
async def get_client(client_id: str):
    """Получение информации о конкретном клиенте"""
    client_info = server.get_client_info(client_id)
    if not client_info:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return client_info

@app.post("/api/commands", response_model=CommandResponse)
async def send_command(command_request: CommandRequest):
    """Отправка команды клиенту"""
    if command_request.client_id:
        # Отправляем конкретному клиенту
        # Сборка WS-сообщения: поддерживаем плоский и модульный режимы
        payload = None
        if command_request.name:
            payload = {
                "type": "command",
                "name": command_request.name,
                "params": command_request.params or {},
            }
        else:
            payload = {
                "type": "command",
                "command": command_request.command or "",
            }
        command_id = await server.send_command_to_client(command_request.client_id, payload)
        
        # Ждем результат (с таймаутом)
        for _ in range(30):  # Ждем до 30 секунд
            await asyncio.sleep(1)
            result = server.get_command_result(command_id)
            if result:
                return CommandResponse(
                    success=result['success'],
                    result=result['result'],
                    error=result['error'],
                    client_id=result['client_id']
                )
        
        return CommandResponse(
            success=False,
            error="Таймаут ожидания результата",
            client_id=command_request.client_id
        )
    else:
        # Отправляем всем подключенным клиентам
        clients = server.get_clients_list()
        if not clients:
            raise HTTPException(status_code=404, detail="Нет подключенных клиентов")
        
        results = []
        for client in clients:
            try:
                if command_request.name:
                    payload = {
                        "type": "command",
                        "name": command_request.name,
                        "params": command_request.params or {},
                    }
                else:
                    payload = {
                        "type": "command",
                        "command": command_request.command or "",
                    }
                command_id = await server.send_command_to_client(client['id'], payload)
                results.append({
                    'client_id': client['id'],
                    'command_id': command_id
                })
            except Exception as e:
                results.append({
                    'client_id': client['id'],
                    'error': str(e)
                })
        
        return CommandResponse(
            success=True,
            result=f"Команда отправлена {len(results)} клиентам",
            client_id="all"
        )

@app.get("/api/commands/history")
async def get_command_history(limit: int = 100):
    """Получение истории команд"""
    return server.get_command_history(limit)

@app.get("/api/commands/{command_id}")
async def get_command_result(command_id: str):
    """Получение результата команды"""
    result = server.get_command_result(command_id)
    if not result:
        raise HTTPException(status_code=404, detail="Результат команды не найден")
    return result

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Единый сервер для управления клиентами')
    parser.add_argument('--host', default='0.0.0.0', help='Хост для привязки')
    parser.add_argument('--port', type=int, default=10000, help='Порт для привязки')
    
    args = parser.parse_args()
    
    logger.info(f"🚀 Запуск единого сервера на http://{args.host}:{args.port}")
    logger.info(f"📡 WebSocket: ws://{args.host}:{args.port}/ws")
    logger.info(f"🌐 REST API: http://{args.host}:{args.port}/api")
    
    uvicorn.run(app, host=args.host, port=args.port)
