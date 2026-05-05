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

### Режимы развёртывания

- **Встроенный плагин** — загрузка через `PluginManager` ядра HomeConsole (общий процесс с runtime, прокси к сервисам ядра).
- **Отдельный контейнер / sidecar** — этот каталог собирается в **самодостаточный** образ: без копирования всего `core-runtime-service` и без обязательного сопряжения с ядром в одном процессе (интеграция по HTTP/WebSocket/API с внешним core при необходимости).
- **SDK** — зависимость `home-console-sdk` для протоколов/клиентов; отдельный «режим SDK» в образе не нужен: контейнер уже может жить автономно, а SDK подключается как библиотека.

### Docker (standalone-образ)

Контекст сборки — **каталог плагина** (не корень монорепы):

```bash
cd plugins/client-manager-plugin
docker build -t client-manager-plugin .
docker run --rm -p 10000:10000 client-manager-plugin
```

Из корня репозитория `core-runtime-service`:

```bash
docker build -f plugins/client-manager-plugin/Dockerfile -t client-manager-plugin plugins/client-manager-plugin
```

### Через Docker Compose

Если в корне монорепы есть сервис `client_manager`, укажите в compose `build` с `context: plugins/client-manager-plugin` и `dockerfile: Dockerfile` (или путь к этому `Dockerfile`).

### Локально (без Docker)

```bash
cd plugins/client-manager-plugin
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
plugins/client-manager-plugin/
├── app/
│   ├── main.py           # FastAPI приложение
│   ├── config.py         # Конфигурация
│   ├── core/             # WebSocket handler, команды
│   ├── routes/           # API роуты
│   └── schemas/          # Pydantic схемы
├── run_server.py         # Скрипт запуска
├── requirements.txt
└── Dockerfile            # образ только из этого каталога
```

---

**Happy coding! 🚀**
