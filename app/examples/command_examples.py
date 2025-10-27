"""
Примеры использования продвинутого обработчика команд

Этот файл содержит примеры того, как использовать новую систему обработки команд
для различных сценариев управления удаленными клиентами.
"""

import asyncio
import json
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class CommandExamples:
    """Примеры использования команд"""
    
    def __init__(self, websocket_handler):
        self.handler = websocket_handler
    
    async def example_system_monitoring(self, client_id: str):
        """Пример мониторинга системы"""
        commands = [
            "uptime",                    # Время работы системы
            "free -h",                   # Использование памяти
            "df -h",                     # Использование дисков
            "ps aux --sort=-%cpu | head -10",  # Топ процессов по CPU
            "netstat -tuln",            # Сетевые соединения
            "iostat -x 1 3"             # Статистика I/O
        ]
        
        results = {}
        
        for cmd in commands:
            try:
                command_id = f"monitor_{int(time.time())}"
                success = await self.handler.send_command_to_client(
                    client_id, cmd, command_id, timeout=60
                )
                
                if success:
                    # Ждем результат
                    await asyncio.sleep(2)
                    result = self.handler.command_handler.get_command_result(command_id)
                    if result:
                        results[cmd] = {
                            "success": result.success,
                            "output": result.result,
                            "error": result.error
                        }
                
            except Exception as e:
                logger.error(f"Ошибка выполнения команды {cmd}: {e}")
                results[cmd] = {"success": False, "error": str(e)}
        
        return results
    
    async def example_file_operations(self, client_id: str, file_path: str):
        """Пример файловых операций"""
        commands = [
            f"ls -la {file_path}",      # Список файлов
            f"file {file_path}",        # Тип файла
            f"stat {file_path}",        # Статистика файла
            f"du -sh {file_path}",      # Размер файла/директории
            f"find {file_path} -name '*.log' -mtime -1"  # Поиск логов за последний день
        ]
        
        results = {}
        
        for cmd in commands:
            try:
                command_id = f"file_ops_{int(time.time())}"
                success = await self.handler.send_command_to_client(
                    client_id, cmd, command_id, timeout=30
                )
                
                if success:
                    await asyncio.sleep(1)
                    result = self.handler.command_handler.get_command_result(command_id)
                    if result:
                        results[cmd] = {
                            "success": result.success,
                            "output": result.result,
                            "error": result.error
                        }
                
            except Exception as e:
                logger.error(f"Ошибка выполнения команды {cmd}: {e}")
                results[cmd] = {"success": False, "error": str(e)}
        
        return results
    
    async def example_network_diagnostics(self, client_id: str, target_host: str):
        """Пример сетевой диагностики"""
        commands = [
            f"ping -c 4 {target_host}",           # Ping тест
            f"traceroute {target_host}",          # Трассировка маршрута
            f"nslookup {target_host}",            # DNS запрос
            f"curl -I http://{target_host}",     # HTTP заголовки
            f"nc -zv {target_host} 22",           # Проверка SSH порта
            f"nc -zv {target_host} 80",           # Проверка HTTP порта
            f"nc -zv {target_host} 443"           # Проверка HTTPS порта
        ]
        
        results = {}
        
        for cmd in commands:
            try:
                command_id = f"network_{int(time.time())}"
                success = await self.handler.send_command_to_client(
                    client_id, cmd, command_id, timeout=60
                )
                
                if success:
                    await asyncio.sleep(2)
                    result = self.handler.command_handler.get_command_result(command_id)
                    if result:
                        results[cmd] = {
                            "success": result.success,
                            "output": result.result,
                            "error": result.error
                        }
                
            except Exception as e:
                logger.error(f"Ошибка выполнения команды {cmd}: {e}")
                results[cmd] = {"success": False, "error": str(e)}
        
        return results
    
    async def example_service_management(self, client_id: str, service_name: str):
        """Пример управления сервисами"""
        commands = [
            f"systemctl status {service_name}",   # Статус сервиса
            f"systemctl is-active {service_name}", # Активен ли сервис
            f"systemctl is-enabled {service_name}", # Включен ли автозапуск
            f"journalctl -u {service_name} --no-pager -n 20"  # Последние логи
        ]
        
        results = {}
        
        for cmd in commands:
            try:
                command_id = f"service_{int(time.time())}"
                success = await self.handler.send_command_to_client(
                    client_id, cmd, command_id, timeout=30
                )
                
                if success:
                    await asyncio.sleep(1)
                    result = self.handler.command_handler.get_command_result(command_id)
                    if result:
                        results[cmd] = {
                            "success": result.success,
                            "output": result.result,
                            "error": result.error
                        }
                
            except Exception as e:
                logger.error(f"Ошибка выполнения команды {cmd}: {e}")
                results[cmd] = {"success": False, "error": str(e)}
        
        return results
    
    async def example_batch_commands(self, client_ids: List[str], command: str):
        """Пример выполнения команды на нескольких клиентах"""
        results = {}
        
        for client_id in client_ids:
            try:
                command_id = f"batch_{int(time.time())}_{client_id}"
                success = await self.handler.send_command_to_client(
                    client_id, command, command_id, timeout=60
                )
                
                if success:
                    await asyncio.sleep(2)
                    result = self.handler.command_handler.get_command_result(command_id)
                    if result:
                        results[client_id] = {
                            "success": result.success,
                            "output": result.result,
                            "error": result.error
                        }
                    else:
                        results[client_id] = {
                            "success": False,
                            "error": "Результат не получен"
                        }
                
            except Exception as e:
                logger.error(f"Ошибка выполнения команды на {client_id}: {e}")
                results[client_id] = {"success": False, "error": str(e)}
        
        return results
    
    async def example_long_running_command(self, client_id: str, command: str):
        """Пример выполнения длительной команды с мониторингом"""
        try:
            command_id = f"long_{int(time.time())}"
            success = await self.handler.send_command_to_client(
                client_id, command, command_id, timeout=300
            )
            
            if not success:
                return {"success": False, "error": "Не удалось отправить команду"}
            
            # Мониторим выполнение команды
            start_time = time.time()
            while True:
                await asyncio.sleep(5)  # Проверяем каждые 5 секунд
                
                # Проверяем, есть ли результат
                result = self.handler.command_handler.get_command_result(command_id)
                if result:
                    return {
                        "success": result.success,
                        "output": result.result,
                        "error": result.error,
                        "execution_time": time.time() - start_time
                    }
                
                # Проверяем таймаут
                if time.time() - start_time > 300:
                    # Отменяем команду
                    await self.handler.cancel_command(client_id, command_id)
                    return {
                        "success": False,
                        "error": "Команда превысила таймаут",
                        "execution_time": time.time() - start_time
                    }
                
                # Проверяем, активна ли команда
                active_commands = self.handler.command_handler.get_active_commands()
                if command_id not in active_commands:
                    return {
                        "success": False,
                        "error": "Команда была отменена или завершилась с ошибкой",
                        "execution_time": time.time() - start_time
                    }
        
        except Exception as e:
            logger.error(f"Ошибка выполнения длительной команды: {e}")
            return {"success": False, "error": str(e)}


# Примеры использования API

class APIExamples:
    """Примеры использования REST API"""
    
    def __init__(self, base_url: str = "http://localhost:10000"):
        self.base_url = base_url
    
    async def example_send_command_via_api(self, client_id: str, command: str):
        """Пример отправки команды через REST API"""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "command": command,
                "timeout": 60,
                "client_id": client_id
            }
            
            async with session.post(
                f"{self.base_url}/api/commands/send",
                json=payload
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    error = await response.text()
                    return {"success": False, "error": error}
    
    async def example_get_command_result_via_api(self, command_id: str):
        """Пример получения результата команды через REST API"""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/api/commands/results/{command_id}"
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    error = await response.text()
                    return {"success": False, "error": error}
    
    async def example_get_all_clients_via_api(self):
        """Пример получения списка всех клиентов через REST API"""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/api/clients") as response:
                if response.status == 200:
                    clients = await response.json()
                    return clients
                else:
                    error = await response.text()
                    return {"success": False, "error": error}
    
    async def example_get_server_stats_via_api(self):
        """Пример получения статистики сервера через REST API"""
        import aiohttp
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/api/stats") as response:
                if response.status == 200:
                    stats = await response.json()
                    return stats
                else:
                    error = await response.text()
                    return {"success": False, "error": error}


# Примеры интеграции с внешними системами

class IntegrationExamples:
    """Примеры интеграции с внешними системами"""
    
    @staticmethod
    async def example_prometheus_metrics(websocket_handler):
        """Пример экспорта метрик для Prometheus"""
        stats = websocket_handler.get_server_stats()
        
        metrics = {
            "remote_client_connections_total": stats["server_stats"]["total_connections"],
            "remote_client_connections_active": stats["server_stats"]["active_connections"],
            "remote_client_commands_total": stats["command_stats"]["stats"]["total_commands"],
            "remote_client_commands_successful": stats["command_stats"]["stats"]["successful_commands"],
            "remote_client_commands_failed": stats["command_stats"]["stats"]["failed_commands"],
            "remote_client_server_uptime_seconds": stats["uptime"]
        }
        
        return metrics
    
    @staticmethod
    async def example_webhook_notification(websocket_handler, webhook_url: str):
        """Пример отправки уведомлений через webhook"""
        import aiohttp
        
        stats = websocket_handler.get_server_stats()
        
        notification = {
            "event": "server_stats_update",
            "timestamp": time.time(),
            "data": {
                "active_clients": stats["client_count"],
                "total_commands": stats["command_stats"]["stats"]["total_commands"],
                "uptime": stats["uptime"]
            }
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=notification) as response:
                return response.status == 200
    
    @staticmethod
    async def example_database_logging(websocket_handler, db_connection):
        """Пример логирования в базу данных"""
        try:
            # Получаем статистику
            stats = websocket_handler.get_server_stats()
            
            # Логируем в базу данных
            query = """
                INSERT INTO server_stats 
                (timestamp, active_clients, total_commands, uptime) 
                VALUES (?, ?, ?, ?)
            """
            
            await db_connection.execute(
                query,
                (
                    time.time(),
                    stats["client_count"],
                    stats["command_stats"]["stats"]["total_commands"],
                    stats["uptime"]
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка логирования в БД: {e}")
            return False


# Примеры использования в production

class ProductionExamples:
    """Примеры использования в production среде"""
    
    @staticmethod
    async def example_health_check(websocket_handler):
        """Пример проверки здоровья системы"""
        try:
            stats = websocket_handler.get_server_stats()
            clients = websocket_handler.get_all_clients()
            
            # Проверяем основные метрики
            health_status = {
                "status": "healthy",
                "checks": {
                    "server_uptime": stats["uptime"] > 0,
                    "active_connections": stats["client_count"] >= 0,
                    "command_handler_active": True,
                    "websocket_manager_active": True
                },
                "metrics": {
                    "uptime": stats["uptime"],
                    "active_clients": stats["client_count"],
                    "total_commands": stats["command_stats"]["stats"]["total_commands"]
                }
            }
            
            # Проверяем, есть ли проблемы
            if stats["client_count"] == 0:
                health_status["status"] = "warning"
                health_status["message"] = "Нет активных клиентов"
            
            return health_status
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e)
            }
    
    @staticmethod
    async def example_auto_scaling(websocket_handler, min_clients: int = 5):
        """Пример автоматического масштабирования"""
        try:
            clients = websocket_handler.get_all_clients()
            client_count = len(clients)
            
            if client_count < min_clients:
                # Запускаем дополнительные инстансы
                logger.warning(f"Количество клиентов ({client_count}) меньше минимума ({min_clients})")
                # Здесь можно добавить логику запуска дополнительных сервисов
                
                return {
                    "action": "scale_up",
                    "current_clients": client_count,
                    "min_clients": min_clients
                }
            
            return {
                "action": "no_action",
                "current_clients": client_count,
                "min_clients": min_clients
            }
            
        except Exception as e:
            logger.error(f"Ошибка проверки масштабирования: {e}")
            return {"action": "error", "error": str(e)}
