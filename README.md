# 🚀 Client Manager Service

Сервер на FastAPI + WebSocket для подключения и управления удалёнными клиентами (агентами).

## ✨ Возможности

- 📡 **WebSocket**: регистрация клиентов, heartbeat, доставка команд
- 🌐 **REST API**: управление клиентами и командами
- 🔐 **JWT Authentication**: токены с ролями и правами доступа
- 🛡️ **Command Validation**: whitelist команд + защита от injection
- 📊 **Health Checks**: `/health`, `/health/ready`, `/health/live`
- 📈 **Prometheus Metrics**: `/metrics/prometheus`

## 🚀 Быстрый старт

### Через Docker Compose (рекомендуется)

```bash
# В корне проекта NewHomeConsole
docker-compose up -d client_manager
```

### Standalone

```bash
cd client-manager-service
pip install -r requirements.txt
python run_server.py
```

## 📡 API Endpoints

- `GET /api/clients` — список клиентов
- `POST /api/commands/{client_id}` — отправить команду
- `GET /health` — health check
- `GET /docs` — Swagger UI

## 📂 Структура

```
client-manager-service/
├── app/
│   ├── main.py           # FastAPI приложение
│   ├── config.py         # Конфигурация
│   ├── core/             # WebSocket handler, команды
│   ├── routes/           # API роуты
│   └── schemas/          # Pydantic схемы
├── run_server.py         # Скрипт запуска
└── requirements.txt
```

---

**Happy coding! 🚀**
