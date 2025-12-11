# Client Manager Service — Полная архитектура и документация

Этот документ — максимально подробное техническое описание `client-manager-service`: архитектура, компоненты, форматы сообщений, последовательности, требования окружения, CI/Release рекомендации, runbook и отладочные инструкции.

Целевая аудитория: разработчики, DevOps-инженеры, интеграторы и операторы.

---

## Краткое назначение

`client-manager-service` — серверная часть системы управления удалёнными агентами (remote-client). Основные обязанности:

- Приём WebSocket-соединений от агентов и поддержание lifecycle (registration, heartbeat).
- Доставка команд агентам и сбор результатов (chunking, resume).
- Управление файловыми трансферами (chunked uploads, resume, verification).
- Удалённая установка/обновление агента через SSH (инсталлятор).
- Expose admin REST/WS API (JWT protected) для UI и автоматизации.

Порт по умолчанию: `10000`.

---

## Архитектура и компоненты

Логическая диаграмма (ASCII):

```
  +------------ Веб-интерфейс администратора / Оператор -----------+
  |                                                         |
  |  (Браузер или скрипт) <--- REST/JWT ---> client-manager |
  |                                      (FastAPI + WebSocket) |
  |                  Админ WebSocket (JWT) / Инсталлятор (SSH) |
  |                                                         |
  +--------------------------+------------------------------+
                             | 
                             | внутренняя маршрутизация
                             v
            +-------------------------------+
            |   WebSocketHandler (вход)     |
            +---------------+---------------+
                            |
                +----------------+----------------+
                |                                 |
    +-----------v-----------+         +-----------v------------+
    | WebSocketManager/     |         |   SSHInstaller         |
    | Router (сокеты,      |         | (paramiko, загрузчик)  |
    | отправка/прием)       |         |                        |
    +-----------+-----------+         +-----------+------------+
                |                                 |
    +-----------v-----------+         +-----------v------------+
    | ClientManager         |         | создание systemd unit  |
    | (состояние)           |         |                        |
    +-----------+-----------+         +------------------------+
                |
- CI должен загружать артефакты выпуска с именами `remote-client-<os>-<arch>` (например `remote-client-linux-amd64`). Инсталлятор ожидает эти имена по умолчанию.

---

## Чек-лист для production

1. Храните секреты в менеджере секретов.
2. Настройте TLS и не включайте downgrade TLS в production.
3. Установите probes и мониторинг.
4. Централизуйте логи и метрики.

---

## Решение проблем и runbook

- Отсутствует JWT: проверьте переменные окружения в контейнере/k8s.
- Readiness не проходит: проверьте БД и ключ шифрования.
- Ошибки WS HMAC: включите `LOG_LEVEL=DEBUG` и сравните версии секретов.
- Инсталлятор не может скачать: проверьте имена артефактов или используйте `download_url`.

Полезные команды:

```bash
docker logs -f client_manager
docker exec -it client_manager /bin/sh
curl -v http://localhost:10000/health/ready
```

---

## Тестирование и отладка

- Модульные тесты: мок paramiko и WS обработчики.
- Интеграционные тесты: запустите сервис + stub агент для тестирования регистрации и команд.
- Нагрузочные тесты: имитируйте много агентов.

---

## Улучшения и дорожная карта

- Добавить эндпоинт `/internal/orchestrator-hints` для описания образа/портов/переменных окружения.
- Добавить проверку контрольной суммы артефакта в SSHInstaller.
- Расширить сохранение состояния для команд и трансферов.

---

- `DATABASE_URL` — DSN для Postgres (если используется очередь аудита)
- `LOG_LEVEL` — `INFO|DEBUG|WARN|ERROR` (рекомендуется `INFO`)
- `LOG_FORMAT` — `json|text`

Dev переменные (только для локальной разработки):

```bash
JWT_SECRET_KEY=dev-secret
SERVER_ENCRYPTION_KEY=dev-encryption-key
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/postgres
LOG_LEVEL=DEBUG
```

---

## HTTP API (основные эндпоинты)

Сводка основных эндпоинтов (смотрите также подробный код в `app`/`routes`):

- `GET /health` — проверка активности сервиса
- `GET /health/ready` — готовность (по умолчанию: проверяет готовность принимать запросы, зависит от БД/шифрования)
- `GET /api/clients` — список подключенных клиентов
- `GET /api/clients/{client_id}` — информация о клиенте
- `DELETE /api/clients/{client_id}` — отключить клиента
- `POST /api/commands/{client_id}` — отправить команду клиенту
- `POST /api/installations/remote-client` — установить `remote-client` через SSH (см. ниже)
- `GET /metrics/prometheus` — метрики Prometheus
- `GET /docs` — веб-интерфейс Swagger

Подробнее: исходники в `app/routes/`.

---

## Протокол WebSocket (кратко)

- Эндпоинт: `wss(s)://<host>:10000/ws`
- Сообщения в формате JSON
- Первичная регистрация: `{"type":"register","data": {...}}` — TOFU/публичный ключ/версия секретов
- После синхронизации — все сообщения шифруются (HMAC + AES)
- Админский WebSocket: `wss://.../admin/ws` — JWT передается как параметр запроса `?token=...` или в заголовке Sec-WebSocket-Protocol

Типы сообщений: `register`, `heartbeat`, `command_request`, `command_result`, `file_chunk`, `file_eof`, `auth`, `key_exchange`, `request_secrets` и др. Смотрите `app/core/messaging`.

---

## Установка `remote-client` через SSH (Инсталлятор)

Эндпоинт: `POST /api/installations/remote-client`

Пример запроса:

```bash
curl -k -X POST https://localhost:10000/api/installations/remote-client \
  -H "Content-Type: application/json" \
  -d '{
    "host": "192.168.1.42",
    "username": "root",
    "password": "s3cr3t",
    "install_dir": "/opt/remote-client",
    "create_service": true,
    "env": {"COMMAND_VALIDATION_MODE": "disabled"}
  }'
```

Как работает инсталлятор:
- Подключается по SSH (пароль или приватный ключ)
- Определяет платформу (вызовом `uname -s` / `uname -m`)
- Формирует имя двоичного файла `remote-client-<os>-<arch>`
- Скачивает из `REMOTE_CLIENT_RELEASE_BASE_URL` или из `REMOTE_CLIENT_REPO` (GitHub releases), либо использует `download_url`, если указан в запросе
- Устанавливает права выполнения (`chmod +x`) и опционально создает systemd unit

Важные замечания:
- Релизные двоичные файлы должны быть опубликованы с именами, которые ожидает инсталлятор (см. `remote-client/docs/README.md`)
- Для тестирования можно передать `download_url` указывающий на тестовый HTTP-сервер

---

## Dev / CI рекомендации

- CI должен собирать образ и публиковать его с тегом `homeconsole-client-manager:<tag>`
- Процесс выпуска должен создавать артефакты; для интеграции с Orchestrator и Core используйте согласованные теги
- Для локальной разработки удобно иметь `docker-compose.dev.yml` (в корне репозитория HomeConsole) с сервисами `postgres`, `core`, `client-manager` и frontends

Пример скрипта локальной сборки:

```bash
# сборка и тегирование
docker build -t homeconsole-client-manager:dev ./client-manager-service
docker tag homeconsole-client-manager:dev homeconsole-client-manager:latest
```

---

## Интеграция с HomeConsole Core / Orchestrator (кратко)

- Рекомендуемый образ: `homeconsole-client-manager:dev` / `homeconsole-client-manager:latest`
- Если Orchestrator использует docker-run, он должен:
  - предоставить переменные `JWT_SECRET_KEY` и `SERVER_ENCRYPTION_KEY` (dev-fallback для локальной разработки)
  - подключить контейнер к сети compose (например, `homeconsole_hc_net`), чтобы core мог разрешить `client_manager:10000`
  - дождаться прохождения проверки `/health/ready` перед запуском зависимых сервисов
- Опционально можно добавить внутренний эндпоинт `GET /internal/orchestrator-hints` с JSON описывающим ожидаемые `ports`, `env_required` и `image`.

---

## Решение проблем (быстрые проверки)

- Сервис падает с `RuntimeError: JWT_SECRET_KEY must be set` — проверьте переменные окружения
- Health check не проходит — проверьте доступность Postgres (если используется) и корректность `DATABASE_URL`
- Инсталлятор не может скачать артефакт — убедитесь что release assets опубликованы с именами `remote-client-<os>-<arch>` или используйте параметр `download_url` в запросе
- Проблемы с WebSocket: включите `LOG_LEVEL=DEBUG` и проверяйте логи (структурированное логирование содержит полезные correlation id)

---
