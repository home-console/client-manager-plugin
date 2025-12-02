FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Установим системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

# Копируем только зависимости для кеша
# requirements.txt лежит в корне репозитория
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Код монтируется томом в dev. На проде можно раскомментировать COPY:
COPY . /app

EXPOSE 10000

# Запускаем uvicorn через импорт приложения из пакета `app`
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]

