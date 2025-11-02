"""
Главное приложение FastAPI
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware

from .core.websocket_handler import WebSocketHandler
from .dependencies import set_websocket_handler, get_websocket_handler
from .routes import clients, commands, health, files, secrets
from .config import settings, init_settings
from .utils.structured_logger import setup_logging, get_logger, LoggingMiddleware

# Явная инициализация настроек один раз при запуске приложения
init_settings()

# Настройка structured logging
setup_logging(
    level=settings.log_level,
    json_format=(settings.log_format == "json")
)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    logger.info("Запуск сервера", 
                host=settings.server_host, 
                port=settings.server_port)
    
    # Инициализация WebSocket обработчика
    handler = WebSocketHandler()
    set_websocket_handler(handler)
    logger.info("WebSocket обработчик инициализирован")
    
    yield
    
    # Graceful shutdown
    logger.info("Остановка сервера")
    await handler.cleanup()
    logger.info("Сервер остановлен")


def create_app() -> FastAPI:
    """Создание FastAPI приложения"""
    app = FastAPI(
        title="Remote Client Manager",
        description="Единый сервер с WebSocket и REST API для управления удаленными клиентами",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # Logging middleware (первым!)
    app.add_middleware(LoggingMiddleware)
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    
    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint для клиентов"""
        handler = get_websocket_handler()
        if handler:
            await handler.handle_websocket(websocket)
        else:
            logger.error("WebSocket обработчик не инициализирован")
            await websocket.close(code=1011, reason="Server not ready")
    
    # Подключение роутов
    app.include_router(clients.router)
    app.include_router(commands.router)
    app.include_router(health.router)
    app.include_router(files.router)
    app.include_router(secrets.router)
    
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
