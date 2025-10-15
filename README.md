# 🚀 Сервис управления удаленными клиентами

Единый сервер на FastAPI + WebSocket для подключения клиентов и отправки им команд.

## ✨ Возможности

- 📡 **WebSocket**: регистрация клиентов, heartbeat, доставка команд
- 🌐 **REST API**: управление клиентами и командами
- 🎨 **Встроенная веб-страница**: простая страница на `/` для быстрого управления
- 📋 **История команд** и получение результатов

## 🚀 Быстрый старт

### 1. Установка зависимостей
```bash
pip install -r requirements.txt
```

### 2. Запуск единого сервера
```bash
python unified_server.py --host 0.0.0.0 --port 10000
# или через uvicorn
uvicorn unified_server:app --host 0.0.0.0 --port 10000
```

### 3. Открытие веб-интерфейса
Откройте браузер: `http://localhost:10000/`

## 📡 Точки подключения

- **WebSocket**: `ws://<host>:10000/ws`
- **REST API** (префикс `/api`):
  - `GET /api/clients` — список подключенных
  - `GET /api/clients/{client_id}` — информация о клиенте
  - `POST /api/commands` — отправка команды одному клиенту (`client_id`) или всем
  - `GET /api/commands/history` — история команд
  - `GET /api/commands/{command_id}` — результат команды

## 💻 Примеры использования

### Отправка команды всем клиентам
```bash
curl -X POST http://localhost:10000/api/commands \
  -H "Content-Type: application/json" \
  -d '{
    "command": "ls -la"
  }'
```

### Получение списка клиентов
```bash
curl http://localhost:10000/api/clients
```

### Получение результата по `command_id`
```bash
curl http://localhost:10000/api/commands/<command_id>
```

## 🧪 Тестовый WebSocket клиент
См. `test_websocket.py` — по умолчанию подключается к `ws://localhost:10000/ws` и выполняет регистрацию + heartbeat.

## 📝 Протокол WebSocket (минимум)

Сообщение регистрации от клиента:
```json
{
  "type": "register",
  "client_id": "client_123",
  "hostname": "my-computer",
  "capabilities": ["exec", "file", "monitoring"]
}
```

Ответ сервера об успешной регистрации:
```json
{
  "type": "registration_success",
  "client_id": "client_123",
  "message": "Клиент успешно зарегистрирован"
}
```

Пульс (heartbeat) от клиента:
```json
{
  "type": "heartbeat"
}
```

Команда от сервера клиенту:
```json
{
  "type": "command",
  "command_id": "cmd_1700000000_client_123",
  "command": "ls -la",
  "timeout": 30
}
```

Результат выполнения команды от клиента:
```json
{
  "type": "command_result",
  "data": {
    "command_id": "cmd_1700000000_client_123",
    "success": true,
    "result": "...stdout...",
    "error": null
  }
}
```

## 🐛 Отладка
- Логи сервера пишутся в консоль; подробности включаются флагами uvicorn (`--log-level debug`).
- Убедитесь, что порт `10000` свободен.

## 📂 Структура каталога (релевантная)
```
client_manager/
├── unified_server.py      # Единый сервер FastAPI + WebSocket
├── requirements.txt       # Зависимости
├── test_websocket.py      # Простой WebSocket-клиент для проверки
└── README.md              # Эта документация
```
