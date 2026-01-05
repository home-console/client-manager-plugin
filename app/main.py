"""
Главное приложение FastAPI
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Depends, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .core.websocket_handler import WebSocketHandler
from .core.security.auth_service import AuthService
from .dependencies import set_websocket_handler, get_websocket_handler

# Импорт cloud_services с проверкой
try:
    from .core.cloud_services import cloud_manager
    CLOUD_SERVICES_AVAILABLE = True
except ImportError as e:
    CLOUD_SERVICES_AVAILABLE = False
    cloud_manager = None
    print(f"⚠️  Облачные сервисы недоступны: {e}")
from .routes import (
    clients,
    commands,
    health,
    files,
    secrets,
    enrollments,
    universal_commands,
    installations,
    cloud,
    # terminal router
    terminal,
    audit_queue,
)
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
    # Инициализация AuthService для валидации JWT в админских эндпойнтах
    try:
        auth_service = AuthService()
        # Привязываем к handler чтобы другие части могли использовать
        handler.auth_service = auth_service
        logger.info("AuthService инициализирован для админских WS")
    except Exception as e:
        logger.warning(f"AuthService не инициализирован: {e}")
    # Запуск фоновых задач обработчика (монитор core, флешер очереди аудита)
    try:
        await handler.start_background_tasks()
        logger.info("Background tasks started: core monitor and audit flusher")
    except Exception as e:
        logger.warning(f"Не удалось запустить фоновые задачи обработчика: {e}")
    # Инициализация облачных сервисов
    logger.info("Облачные сервисы инициализированы")
    
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

    # Админский WebSocket endpoint (plaintext JSON, защищён JWT)
    @app.websocket("/admin/ws")
    async def admin_websocket_endpoint(websocket: WebSocket):
        """Админский WebSocket endpoint. Ожидает JWT в query param `token`.
        Подходит для браузерного админ-интерфейса (React)."""
        handler = get_websocket_handler()
        if not handler:
            logger.error("WebSocket обработчик не инициализирован (admin)")
            await websocket.close(code=1011, reason="Server not ready")
            return

        # Достаём токен из query params или из Sec-WebSocket-Protocol (subprotocol)
        # Поддерживаем передачу токена в subprotocol, т.е. клиент может вызвать
        # new WebSocket(url, [`Bearer ${token}`]) и сервер получит его в заголовке.
        token = websocket.query_params.get('token')
        if not token:
            # Starlette предоставляет заголовки в websocket.headers
            sp = websocket.headers.get('sec-websocket-protocol')
            if sp:
                # Возможен список протоколов через запятую — берём первый, убираем префикс Bearer 
                token_candidate = sp.split(',')[0].strip()
                if token_candidate.lower().startswith('bearer '):
                    token = token_candidate[7:]
                else:
                    token = token_candidate

        # Примитивная проверка токена через handler.auth_service
        try:
            await websocket.accept()
        except Exception:
            return

        if not token:
            await websocket.send_text('{"type":"auth_required","message":"Token required"}')
            await websocket.close(code=1008, reason="Auth required")
            return

        auth_svc = getattr(handler, 'auth_service', None)
        if not auth_svc:
            await websocket.send_text('{"type":"auth_unavailable","message":"Auth service unavailable"}')
            await websocket.close(code=1011, reason="Auth service unavailable")
            return

        payload = auth_svc.verify_token(token)
        if not payload:
            await websocket.send_text('{"type":"auth_failed","message":"Invalid token"}')
            await websocket.close(code=1008, reason="Invalid token")
            return

        # Авторизация успешна — создаём простое admin client id
        admin_id = f"admin:{payload.get('client_id', 'unknown')}"
        # Регистрируем соединение в менеджере (metadata содержит разрешения)
        await handler.websocket_manager.connect(websocket, admin_id, metadata={"admin": True, "permissions": payload.get('permissions', [])})

        # Отправляем начальный список клиентов
        try:
            import json
            clients = handler.get_all_clients()
            await websocket.send_text(json.dumps({"type": "client_list", "data": clients}))
        except Exception as e:
            logger.warning(f"Ошибка при отправке списка клиентов админу: {e}")

        # Простая реализация: поддерживаем цикл получения команд от админа и периодически шлём refresh
        try:
            import asyncio, json
            async def periodic_refresh():
                prev_snapshot = None
                while True:
                    await asyncio.sleep(5)
                    try:
                        clients = handler.get_all_clients()
                        if json.dumps(clients) != prev_snapshot:
                            prev_snapshot = json.dumps(clients)
                            await websocket.send_text(json.dumps({"type": "client_list_refresh", "data": clients}))
                    except Exception:
                        break

            refresh_task = asyncio.create_task(periodic_refresh())

            while True:
                text = await websocket.receive_text()
                # Ожидаем простые JSON команды от админа
                try:
                    msg = json.loads(text)
                except Exception:
                    await websocket.send_text('{"type":"error","message":"Invalid JSON"}')
                    continue

                if msg.get('type') == 'get_clients':
                    await websocket.send_text(json.dumps({"type": "client_list", "data": handler.get_all_clients()}))
                elif msg.get('type') == 'ping':
                    await websocket.send_text('{"type":"pong"}')
                else:
                    await websocket.send_text(json.dumps({"type": "unknown_command", "received": msg.get('type')}))

        except WebSocketDisconnect:
            logger.info(f"Админский WS отключён: {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка в admin websocket loop: {e}")
        finally:
            try:
                refresh_task.cancel()
            except Exception:
                pass
            await handler.websocket_manager.disconnect(admin_id)
    
    # Подключение роутов
    app.include_router(clients.router, prefix="/api", tags=["Clients"])
    app.include_router(commands.router, prefix="/api", tags=["Commands"])
    app.include_router(health.router)
    app.include_router(files.router, prefix="/api", tags=["Files"])
    app.include_router(secrets.router, prefix="/api", tags=["Secrets"])
    app.include_router(enrollments.router, prefix="/api", tags=["Enrollments"])
    app.include_router(installations.router, prefix="/api", tags=["Installations"])
    app.include_router(universal_commands.router, prefix="/api", tags=["Universal Commands"])
    app.include_router(cloud.router, prefix="/api/cloud", tags=["Cloud Services"])
    app.include_router(terminal.router, prefix="/api", tags=["Terminal"])
    app.include_router(audit_queue.router, prefix="/api", tags=["Audit"])
    # internal admin messaging
    try:
        from .routes import admin_messages
        app.include_router(admin_messages.router, prefix="/api", tags=["Admin"])
    except Exception:
        pass

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
