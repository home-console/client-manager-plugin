"""
Главное приложение FastAPI
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .core.websocket_handler import WebSocketHandler
from .routes import clients, commands

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем экземпляр WebSocket обработчика (синглтон)
websocket_handler = WebSocketHandler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    logger.info("🚀 Запуск сервера...")
    yield
    logger.info("🛑 Остановка сервера...")


def create_app() -> FastAPI:
    """Создание FastAPI приложения"""
    app = FastAPI(
        title="Remote Client Manager",
        description="Единый сервер с WebSocket и REST API для управления удаленными клиентами",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # CORS middleware
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
        await websocket_handler.handle_websocket(websocket)
    
    # Подключение роутов
    app.include_router(clients.router)
    app.include_router(commands.router)
    
    return app


# Создание приложения
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=10000,
        reload=True,
        ssl_keyfile="server.key",
        ssl_certfile="server.crt"
    )
