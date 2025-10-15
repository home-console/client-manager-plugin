#!/usr/bin/env python3
"""
Тестовый WebSocket клиент для проверки сервера
"""

import asyncio
import json
import websockets
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_websocket():
    """Тестирование WebSocket соединения"""
    uri = "ws://localhost:10000/ws"
    
    try:
        async with websockets.connect(uri) as websocket:
            logger.info("✅ Подключен к WebSocket серверу")
            
            # Отправляем сообщение регистрации
            register_msg = {
                "type": "register",
                "client_id": "test_client_123",
                "hostname": "test-hostname",
                "capabilities": ["exec", "file", "monitoring"]
            }
            
            logger.info(f"📤 Отправляем регистрацию: {register_msg}")
            await websocket.send(json.dumps(register_msg))
            
            # Ждем ответ
            response = await websocket.recv()
            logger.info(f"📥 Получен ответ: {response}")
            
            # Отправляем heartbeat
            heartbeat_msg = {
                "type": "heartbeat",
                "timestamp": 1234567890
            }
            
            logger.info(f"📤 Отправляем heartbeat: {heartbeat_msg}")
            await websocket.send(json.dumps(heartbeat_msg))
            
            # Ждем еще немного
            logger.info("⏳ Ждем 10 секунд...")
            await asyncio.sleep(10)
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_websocket())
