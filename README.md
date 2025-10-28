# 🚀 Сервис управления удаленными клиентами

Единый сервер на FastAPI + WebSocket для подключения клиентов и отправки им команд.

## ✨ Возможности

### Основные:
- 📡 **WebSocket**: регистрация клиентов, heartbeat, доставка команд
- 🌐 **REST API**: управление клиентами и командами
- 🎨 **Встроенная веб-страница**: простая страница на `/` для быстрого управления
- 📋 **История команд** и получение результатов

### Безопасность:
- 🔐 **JWT Authentication**: токены с ролями и правами доступа
- 🛡️ **Command Validation**: whitelist команд + защита от injection
- 🚦 **Rate Limiting**: защита от spam (10 requests/60s)
- 🔒 **E2E Encryption**: AES-GCM шифрование сообщений

### Мониторинг:
- 📊 **Health Checks**: `/health`, `/health/ready`, `/health/live`
- 📈 **Prometheus Metrics**: `/metrics/prometheus`
- 🔍 **Structured Logging**: JSON логи с correlation IDs
- 📚 **API Documentation**: Swagger UI на `/docs`

### Performance:
- ⚡ **Event-Driven**: asyncio.Event вместо polling (10-20x быстрее)
- 💾 **Memory Management**: TTL + cleanup для истории команд
- 🏗️ **Dependency Injection**: testable и scalable архитектура

## 🚀 Быстрый старт

### 1. Установка зависимостей
```bash
# Активировать virtual environment
source .venv/bin/activate  # или аналог для вашей ОС

# Установить зависимости
pip install -r requirements.txt
```

### 2. Конфигурация (опционально)
```bash
# Скопировать example конфигурации
cp .env.example .env

# Отредактировать .env файл
nano .env
```

**Основные настройки:**
```bash
# Логирование
LOG_LEVEL=INFO        # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT=json       # json или text

# JWT
JWT_SECRET_KEY=your-secret-key-here
JWT_EXPIRE_MINUTES=60

# Шифрование
SERVER_ENCRYPTION_KEY=your-encryption-key-here
```

### 3. Запуск сервера
```bash
# Запуск через run_server.py (рекомендуется)
python run_server.py

# Или напрямую через uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 10000 \
  --ssl-keyfile server.key --ssl-certfile server.crt
```

### 4. Проверка работоспособности
```bash
# Health check
curl -k https://localhost:10000/health

# Swagger UI
open https://localhost:10000/docs

# Список клиентов
curl -k https://localhost:10000/api/clients
```

## 📡 API Endpoints

### REST API

#### Клиенты
- `GET /api/clients` — список всех подключенных клиентов
- `GET /api/clients/{client_id}` — информация о конкретном клиенте
- `DELETE /api/clients/{client_id}` — отключить клиента

#### Команды
- `POST /api/commands/{client_id}` — отправить команду клиенту
- `POST /api/commands/{client_id}/cancel` — отменить команду
- `GET /api/commands/{command_id}/status` — статус команды (active/result)
- `GET /api/commands/history` — история команд
- `GET /api/commands/{command_id}` — результат команды

#### Мониторинг
- `GET /health` — простой health check
- `GET /health/ready` — readiness probe (для Kubernetes)
- `GET /health/live` — liveness probe
- `GET /metrics` — метрики в JSON формате
- `GET /metrics/prometheus` — метрики для Prometheus

#### Документация
- `GET /docs` — Swagger UI (интерактивная документация)
- `GET /redoc` — ReDoc (альтернативная UI)
- `GET /openapi.json` — OpenAPI схема

### WebSocket
- **Endpoint**: `wss://localhost:10000/ws`
- **Protocol**: JSON messages
- **Authentication**: JWT token после регистрации

## 💻 Примеры использования

### REST API

#### Получить список клиентов
```bash
curl -k https://localhost:10000/api/clients
```

**Ответ:**
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

#### Отправить команду клиенту
```bash
curl -k -X POST https://localhost:10000/api/commands/client-123 \
  -H "Content-Type: application/json" \
  -d '{
    "command": "ls -la"
  }'
```

**Ответ:**
```json
{
  "command_id": "cmd_1234567890_client-123",
  "status": "sent",
  "result": "..."
}
```

#### Проверить статус команды
```bash
curl -k "https://localhost:10000/api/commands/cmd_123/status"
```

Вариант 1: команда активна
```json
{
  "command_id": "cmd_123",
  "client_id": "client-123",
  "status": "running",
  "started_at": "2025-01-15T10:35:00",
  "timeout": 30
}
```

Вариант 2: команда завершена (возвращается сохранённый результат)
```json
{
  "command_id": "cmd_123",
  "client_id": "client-123",
  "success": true,
  "result": "...",
  "error": null,
  "timestamp": "2025-01-15T10:35:10"
}
```

#### Отменить команду
```bash
curl -k -X POST "https://localhost:10000/api/commands/client-123/cancel" \
  -H "Content-Type: application/json" \
  -d '{"command_id":"cmd_123"}'
```

Поведение:
- Сервер отправляет `command_cancel` клиенту и ждёт результата отмены.
- Клиент присылает `command_cancel_ack` и итоговый `command_result` с `error: "cancelled"`.
- При отсутствии ответа в пределах таймаута вернётся HTTP 408.

#### Проверить здоровье сервера
```bash
# Простой health check
curl -k https://localhost:10000/health

# Readiness (готовность принимать запросы)
curl -k https://localhost:10000/health/ready

# Метрики
curl -k https://localhost:10000/metrics | jq
```

### WebSocket Client

См. примеры в `home-project_remote-client/` для полноценного Go клиента.

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
```

## 📊 Kubernetes Integration

### Deployment Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: client-manager
spec:
  replicas: 1
  selector:
    matchLabels:
      app: client-manager
  template:
    metadata:
      labels:
        app: client-manager
    spec:
      containers:
      - name: client-manager
        image: client-manager:latest
        ports:
        - containerPort: 10000
          name: https
        
        env:
        - name: LOG_FORMAT
          value: "json"
        - name: LOG_LEVEL
          value: "INFO"
        
        # Liveness probe
        livenessProbe:
          httpGet:
            path: /health/live
            port: 10000
            scheme: HTTPS
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 5
        
        # Readiness probe
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 10000
            scheme: HTTPS
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 3
        
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

### Service Example

```yaml
apiVersion: v1
kind: Service
metadata:
  name: client-manager
spec:
  selector:
    app: client-manager
  ports:
  - port: 10000
    targetPort: 10000
    name: https
  type: LoadBalancer
```

## 📈 Prometheus Monitoring

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'client-manager'
    scheme: https
    tls_config:
      insecure_skip_verify: true
    static_configs:
      - targets: ['client-manager:10000']
    metrics_path: /metrics/prometheus
    scrape_interval: 15s
```

### Available Metrics

- `websocket_connections_active` - Активные WebSocket соединения (gauge)
- `websocket_connections_total` - Всего соединений (counter)
- `commands_total` - Всего команд (counter)
- `commands_successful` - Успешные команды (counter)
- `commands_failed` - Неудачные команды (counter)
- `commands_blocked` - Заблокированные команды (counter)
- `rate_limiter_blocked` - Заблокировано rate limiter (counter)
- `server_uptime_seconds` - Время работы сервера (gauge)

### Grafana Dashboard

Импортируйте метрики из Prometheus и создайте dashboard с:
- Graph: Active WebSocket connections (time series)
- Stat: Total commands executed
- Gauge: Success rate (successful / total)
- Table: Top clients by command count

## 🔒 Security Best Practices

### Production Configuration

```bash
# .env для production
LOG_LEVEL=WARNING
LOG_FORMAT=json

# Используйте сильные ключи
JWT_SECRET_KEY=$(openssl rand -hex 32)
SERVER_ENCRYPTION_KEY=$(openssl rand -hex 32)

# Ограничьте CORS
CORS_ORIGINS=https://yourdomain.com

# Настройте rate limiting
# (в коде: rate_limiter.py)
```

### SSL Certificates

Для production используйте настоящие SSL сертификаты (Let's Encrypt):

```bash
# Certbot для Let's Encrypt
certbot certonly --standalone -d yourdomain.com

# Обновите пути в .env
SSL_CERTFILE=/etc/letsencrypt/live/yourdomain.com/fullchain.pem
SSL_KEYFILE=/etc/letsencrypt/live/yourdomain.com/privkey.pem
```

### Command Validation

Whitelist разрешенных команд настраивается в `app/core/security/command_validator.py`:

```python
COMMAND_PATTERNS = {
    SecurityLevel.SAFE: [
        r'^ls(\s+-[a-zA-Z]+)*(\s+.*)?$',
        r'^pwd$',
        r'^whoami$',
    ],
    # ...
}
```

## 🚀 Performance Tuning

### Uvicorn Workers

```bash
# Запуск с несколькими workers
uvicorn app.main:app \
  --workers 4 \
  --host 0.0.0.0 \
  --port 10000 \
  --ssl-keyfile server.key \
  --ssl-certfile server.crt
```

### Rate Limiting

Настройки в `app/core/security/rate_limiter.py`:

```python
self.max_requests = 10       # Увеличить для production
self.time_window = 60        # Уменьшить window
self.block_duration = 300    # Увеличить block time
```

### Memory Management

TTL для истории команд в `app/core/command_handler.py`:

```python
self.command_ttl = 3600      # 1 час (можно уменьшить)
self.max_history_size = 1000 # Максимум команд в памяти
```

## 📚 Documentation

### Generated Docs

- **Swagger UI**: https://localhost:10000/docs - интерактивная документация
- **ReDoc**: https://localhost:10000/redoc - альтернативная UI
- **OpenAPI Schema**: https://localhost:10000/openapi.json - JSON схема

## 🤝 Contributing

### Code Style

```bash
# Black для форматирования
black app/

# Pylint для linting
pylint app/ --errors-only

# Type checking (если используете)
mypy app/
```

### Testing

```bash
# Unit tests
pytest tests/

# Coverage
pytest --cov=app tests/

# Integration tests
pytest tests/integration/
```

## 📄 License

MIT License - см. LICENSE file (если есть)

## 🎉 Credits

Developed with ❤️ by GitHub Copilot

---

**Happy coding! 🚀**
}
```

## 🐛 Troubleshooting

### Сервер не запускается

**Проблема:** `ModuleNotFoundError: No module named 'pyjwt'`

```bash
# Решение: установить зависимости
pip install -r requirements.txt
```

**Проблема:** `Address already in use` на порту 10000

```bash
# Решение: найти и убить процесс
lsof -ti:10000 | xargs kill -9

# Или изменить порт в .env
SERVER_PORT=10001
```

### Health check возвращает 503

**Проблема:** `{"status":"not_ready","reason":"Handler not initialized"}`

```bash
# Решение: подождать несколько секунд после запуска
# Или проверить логи на ошибки инициализации
```

### Логи не в JSON формате

**Проблема:** Логи выводятся в обычном текстовом формате

```bash
# Решение: установить переменную окружения
export LOG_FORMAT=json
python run_server.py

# Или в .env файле
echo "LOG_FORMAT=json" >> .env
```

### SSL сертификат не найден

**Проблема:** `FileNotFoundError: server.key`

```bash
# Решение: сгенерировать self-signed сертификат
openssl req -x509 -newkey rsa:4096 -keyout server.key \
  -out server.crt -days 365 -nodes \
  -subj "/CN=localhost"
```

### Больше информации

- **API Docs:** https://localhost:10000/docs
- **GitHub Issues:** (если есть репозиторий)

## 📂 Структура проекта

```
client_manager/
├── app/
│   ├── main.py                          # FastAPI приложение
│   ├── config.py                        # Конфигурация
│   ├── dependencies.py                  # Dependency Injection
│   │
│   ├── core/
│   │   ├── websocket_handler.py         # WebSocket handler
│   │   ├── command_handler.py           # Обработка команд (event-driven)
│   │   ├── client_manager.py            # Управление клиентами
│   │   ├── models.py                    # Pydantic модели
│   │   │
│   │   ├── security/
│   │   │   ├── auth_service.py          # JWT аутентификация
│   │   │   ├── command_validator.py     # Валидация команд
│   │   │   └── rate_limiter.py          # Rate limiting
│   │   │
│   │   ├── connection/                  # WebSocket connection logic
│   │   ├── messaging/                   # Message routing
│   │   └── ...
│   │
│   ├── routes/
│   │   ├── clients.py                   # API для управления клиентами
│   │   ├── commands.py                  # API для команд
│   │   └── health.py                    # Health checks и метрики
│   │
│   ├── utils/
│   │   ├── structured_logger.py         # JSON логирование
│   │   └── encryption.py                # E2E шифрование
│   │
│   └── schemas/                         # Request/Response схемы
│
├── run_server.py                        # Скрипт запуска
├── requirements.txt                     # Python зависимости
├── .env.example                         # Пример конфигурации
├── server.key / server.crt              # SSL сертификаты
└── README.md                            # Эта документация
```
