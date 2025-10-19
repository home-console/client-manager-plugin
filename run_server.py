#!/usr/bin/env python3
"""
Запуск модульного сервера
"""

import logging
import uvicorn

from app.main import app

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("🚀 Запуск модульного сервера...")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=10000,
        reload=True,
        ssl_keyfile="server.key",
        ssl_certfile="server.crt"
    )
