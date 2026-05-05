# Standalone / sidecar образ Client Manager (FastAPI + WebSocket).
# Не тянет весь core-runtime-service: только код и зависимости плагина.
#
# Сборка (контекст — эта директория):
#   cd plugins/client-manager-plugin && docker build -t client-manager-plugin .
# Из корня монорепы:
#   docker build -f plugins/client-manager-plugin/Dockerfile plugins/client-manager-plugin
#
# Встроенный режим в ядре HomeConsole — отдельно (PluginManager); этот образ для
# независимого процесса без сопряжения с runtime ядра в том же процессе.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10000/health/live', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
