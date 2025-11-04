FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Установим системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

# Копируем только зависимости для кеша
COPY client_manager/requirements.txt /app/client_manager/requirements.txt
RUN pip install --no-cache-dir -r /app/client_manager/requirements.txt

# Код монтируется томом в dev. На проде можно раскомментировать COPY:
# COPY client_manager /app/client_manager

EXPOSE 10000

CMD ["python", "-m", "uvicorn", "client_manager.app.main:app", "--host", "0.0.0.0", "--port", "10000"]

