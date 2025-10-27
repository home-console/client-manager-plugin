"""
Rate Limiter для защиты от спама команд
"""

import time
import logging
from typing import Dict, Optional, Tuple
from collections import deque, defaultdict

logger = logging.getLogger(__name__)


class RateLimiter:
    """Rate Limiter с алгоритмом скользящего окна"""
    
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        """
        Args:
            max_requests: Максимальное количество запросов
            window_seconds: Размер окна в секундах
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        
        # История запросов для каждого клиента
        self.requests: Dict[str, deque] = defaultdict(lambda: deque())
        
        # Статистика блокировок
        self.blocked_count: Dict[str, int] = defaultdict(int)
        self.total_blocked = 0
    
    def check_rate_limit(self, client_id: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка rate limit для клиента
        
        Returns:
            (allowed, error_message)
        """
        current_time = time.time()
        
        # Получаем историю запросов клиента
        client_requests = self.requests[client_id]
        
        # Удаляем устаревшие запросы (за пределами окна)
        while client_requests and current_time - client_requests[0] > self.window_seconds:
            client_requests.popleft()
        
        # Проверяем лимит
        if len(client_requests) >= self.max_requests:
            self.blocked_count[client_id] += 1
            self.total_blocked += 1
            
            # Вычисляем время до разблокировки
            oldest_request = client_requests[0]
            retry_after = int(self.window_seconds - (current_time - oldest_request)) + 1
            
            error_msg = f"Rate limit exceeded. Retry after {retry_after} seconds"
            logger.warning(f"🚫 Rate limit для клиента {client_id}: {len(client_requests)}/{self.max_requests} запросов")
            
            return False, error_msg
        
        # Добавляем текущий запрос
        client_requests.append(current_time)
        
        return True, None
    
    def reset_client(self, client_id: str):
        """Сброс лимита для клиента"""
        if client_id in self.requests:
            del self.requests[client_id]
        if client_id in self.blocked_count:
            del self.blocked_count[client_id]
        
        logger.info(f"🔄 Rate limit сброшен для клиента {client_id}")
    
    def get_client_stats(self, client_id: str) -> dict:
        """Получить статистику для клиента"""
        current_time = time.time()
        client_requests = self.requests[client_id]
        
        # Очищаем устаревшие
        while client_requests and current_time - client_requests[0] > self.window_seconds:
            client_requests.popleft()
        
        return {
            "current_requests": len(client_requests),
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "blocked_count": self.blocked_count.get(client_id, 0),
            "available_requests": max(0, self.max_requests - len(client_requests))
        }
    
    def get_global_stats(self) -> dict:
        """Получить глобальную статистику"""
        return {
            "total_clients": len(self.requests),
            "total_blocked": self.total_blocked,
            "clients_blocked": len([c for c, count in self.blocked_count.items() if count > 0])
        }
    
    async def cleanup_inactive_clients(self, active_client_ids: set):
        """Очистка данных неактивных клиентов"""
        inactive_clients = []
        
        for client_id in list(self.requests.keys()):
            if client_id not in active_client_ids:
                inactive_clients.append(client_id)
        
        for client_id in inactive_clients:
            if client_id in self.requests:
                del self.requests[client_id]
            if client_id in self.blocked_count:
                del self.blocked_count[client_id]
        
        if inactive_clients:
            logger.info(f"🧹 Очищены данные rate limiter для {len(inactive_clients)} неактивных клиентов")


# Глобальный экземпляр
rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
