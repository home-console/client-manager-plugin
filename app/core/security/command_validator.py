"""
Валидатор команд с защитой от injection и whitelist
"""

import logging
import os
import re
import shlex
from typing import List, Optional, Dict, Set
from enum import Enum

logger = logging.getLogger(__name__)


class CommandSecurityLevel(Enum):
    """Уровни безопасности команд"""
    SAFE = "safe"           # Безопасные команды (чтение)
    MODERATE = "moderate"   # Умеренно опасные (запись файлов)
    DANGEROUS = "dangerous" # Опасные (удаление, системные операции)
    RESTRICTED = "restricted" # Полностью запрещены


class CommandValidator:
    """Валидатор команд с whitelist подходом"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        
        # Whitelist команд по уровням безопасности
        self.command_whitelist = self._load_command_whitelist()
        
        # Blacklist опасных паттернов
        self.dangerous_patterns = self._load_dangerous_patterns()
        
        # Разрешенные спецсимволы
        self.allowed_special_chars = set(".-_/=:,@")
        
        # Максимальная длина команды
        self.max_command_length = int(os.getenv("MAX_COMMAND_LENGTH", "1000"))
        
        # Режим валидации (strict, permissive или disabled)
        validation_mode = os.getenv("COMMAND_VALIDATION_MODE", "strict")
        if validation_mode.lower() in ["disabled", "off", "none"]:
            self.validation_mode = "disabled"
        else:
            self.validation_mode = validation_mode.lower()
    
    def _load_command_whitelist(self) -> Dict[CommandSecurityLevel, Set[str]]:
        """Загрузка whitelist команд"""
        return {
            CommandSecurityLevel.SAFE: {
                # Информационные команды
                "ls", "cat", "head", "tail", "more", "less",
                "pwd", "whoami", "id", "hostname", "uname",
                "date", "uptime", "w", "who", "finger",
                "env", "printenv", "echo",
                
                # Поиск и фильтрация
                "grep", "find", "locate", "which", "whereis",
                "awk", "sed", "cut", "sort", "uniq", "wc",
                "tr", "diff", "cmp",
                
                # Системная информация
                "ps", "top", "htop", "df", "du", "free",
                "vmstat", "iostat", "mpstat", "sar",
                
                # Сетевая информация
                "ping", "traceroute", "netstat", "ss", "ip",
                "ifconfig", "route", "arp", "nslookup", "dig", "host",

                # Windows safe/info
                "echo", "type", "ver", "whoami", "hostname", "ipconfig", "tasklist",
            },
            
            CommandSecurityLevel.MODERATE: {
                # Файловые операции
                "touch", "mkdir", "cp", "mv",
                
                # Архивирование
                "tar", "gzip", "gunzip", "zip", "unzip",
                "bzip2", "bunzip2", "xz", "unxz",
                
                # Сетевые команды (разрешаем базовые загрузки без пайпа в shell)
                "curl", "wget", "scp", "rsync",
            },
            
            CommandSecurityLevel.DANGEROUS: {
                # Удаление (с ограничениями)
                "rm", "rmdir",
                
                # Изменение прав
                "chmod", "chown", "chgrp",
                
                # Управление процессами
                "kill", "killall", "pkill",
                
                # Управление сервисами
                "systemctl", "service", "initctl",
                
                # Сетевые универсальные утилиты (опасные по умолчанию)
                "nc", "netcat", "telnet",
                
                # Windows dangerous
                "taskkill", "sc", "net", "powershell",
            },
            
            CommandSecurityLevel.RESTRICTED: {
                # Полностью запрещены
                "dd", "mkfs", "fdisk", "parted", "gdisk",
                "shutdown", "reboot", "halt", "poweroff", "init",
                # Windows critical
                "shutdown", "format", "diskpart", "bcdedit", "vssadmin", "wevtutil", "reg", "cipher",
                "wmic",
                "su", "sudo", "passwd", "chpasswd",
                "useradd", "userdel", "usermod",
                "groupadd", "groupdel", "groupmod",
                "visudo", "crontab",
            }
        }
    
    def _load_dangerous_patterns(self) -> List[re.Pattern]:
        """Загрузка опасных паттернов"""
        patterns = [
            # Command injection через различные разделители
            r'[;&|`$]',
            r'\$\(',
            r'\$\{',
            r'>\s*/dev/',
            r'<\s*/dev/',
            
            # Попытки обхода через экранирование
            r'\\x[0-9a-fA-F]{2}',
            r'\\[0-7]{1,3}',
            
            # Опасные пути
            r'/etc/shadow',
            r'/etc/passwd',
            r'/proc/.*mem',
            r'/dev/sd[a-z]',
            
            # Рекурсивное удаление корня
            r'rm\s+(-[rfRF]+\s+)*[/]+\s*$',
            r'rm\s+.*\s+[/]+\s*$',

            # Windows опасности
            r'format\s+\w:',
            r'diskpart',
            r'shutdown\s+/(s|r|p)',
            r'vssadmin',
            r'wevtutil',
            r'bcdedit',
            r'reg(\s+add|\s+delete|\s+import)',
            r'cipher\s+/w',
            r'del\s+/s\s+[A-Za-z]:\\',
            r'powershell\s+.*(Invoke-Expression|Invoke-WebRequest|IEX)'
        ]
        
        return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    
    def validate_command(self, command: str, client_id: str = None, 
                        allowed_level: CommandSecurityLevel = CommandSecurityLevel.MODERATE) -> tuple[bool, Optional[str]]:
        """
        Валидация команды
        
        Returns:
            (is_valid, error_message)
        """
        # Проверка длины
        if len(command) > self.max_command_length:
            return False, f"Команда превышает максимальную длину ({self.max_command_length} символов)"
        
        # Проверка на пустоту
        if not command or not command.strip():
            return False, "Пустая команда"
        
        # Парсинг команды
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return False, f"Ошибка парсинга команды: {e}"
        
        if not parts:
            return False, "Невалидная команда"
        
        base_command = parts[0]
        
        # В disabled режиме проверяем только самые критичные паттерны
        if self.validation_mode == "disabled":
            # Только критичные паттерны, которые могут разрушить систему
            critical_patterns = [
                re.compile(r'rm\s+(-[rfRF]+\s+)*[/]+\s*$', re.IGNORECASE),  # rm -rf /
                re.compile(r'rm\s+.*\s+[/]+\s*$', re.IGNORECASE),           # rm что-то /
                re.compile(r':\(\)\{\s*:\|:&\s*\};:', re.IGNORECASE),        # Fork bomb
            ]
            for pattern in critical_patterns:
                if pattern.search(command):
                    logger.warning(f"🚫 Критичный опасный паттерн обнаружен в команде от {client_id}: {command}")
                    return False, f"Обнаружен критичный опасный паттерн в команде"
            # В disabled режиме разрешаем все остальное, включая Restricted
            logger.debug(f"✅ Команда разрешена в disabled режиме: {base_command}")
            return True, None
        
        # Проверка на опасные паттерны (в strict и permissive режимах)
        for pattern in self.dangerous_patterns:
            if pattern.search(command):
                logger.warning(f"🚫 Опасный паттерн обнаружен в команде от {client_id}: {command}")
                return False, f"Обнаружен опасный паттерн в команде"
        
        # Проверка whitelist
        command_level = self._get_command_level(base_command)
        
        # В strict и permissive режимах блокируем Restricted команды
        if command_level == CommandSecurityLevel.RESTRICTED and self.validation_mode != "disabled":
            logger.warning(f"🚫 Запрещенная команда от {client_id}: {base_command}")
            return False, f"Команда '{base_command}' запрещена"
        
        # Проверка уровня безопасности
        if command_level.value == CommandSecurityLevel.DANGEROUS.value and allowed_level != CommandSecurityLevel.DANGEROUS:
            logger.warning(f"🚫 Опасная команда без разрешения от {client_id}: {base_command}")
            return False, f"Команда '{base_command}' требует повышенных привилегий"
        
        # Проверка команды не в whitelist (в strict режиме)
        if self.validation_mode == "strict" and command_level is None:
            logger.warning(f"🚫 Команда не в whitelist от {client_id}: {base_command}")
            return False, f"Команда '{base_command}' не разрешена"
        
        # Дополнительные проверки для опасных команд
        if command_level == CommandSecurityLevel.DANGEROUS:
            is_safe, error = self._validate_dangerous_command(command, parts)
            if not is_safe:
                return False, error
        
        logger.debug(f"✅ Команда валидна: {base_command} (уровень: {command_level})")
        return True, None
    
    def _get_command_level(self, base_command: str) -> Optional[CommandSecurityLevel]:
        """Определение уровня безопасности команды"""
        # Убираем путь если есть (например, /usr/bin/ls -> ls)
        base_command = os.path.basename(base_command)
        
        for level, commands in self.command_whitelist.items():
            if base_command in commands:
                return level
        
        return None
    
    def _validate_dangerous_command(self, command: str, parts: List[str]) -> tuple[bool, Optional[str]]:
        """Дополнительная валидация опасных команд"""
        base_command = parts[0]
        
        # Специальные проверки для rm
        if base_command == "rm":
            # Запрет рекурсивного удаления корня
            if "-r" in parts or "-R" in parts or "-rf" in parts or "-fr" in parts:
                for part in parts[1:]:
                    if part in ["/", "/*", "/.", "/.."]:
                        return False, "Запрещено рекурсивное удаление корня системы"
            
            # Требуем явное указание путей (не wildcards в опасных местах)
            for part in parts[1:]:
                if not part.startswith("-") and ("*" in part or "?" in part):
                    if part.startswith("/etc") or part.startswith("/usr") or part.startswith("/var"):
                        return False, "Запрещено использование wildcards в системных директориях"
        
        # Проверки для chmod/chown
        if base_command in ["chmod", "chown", "chgrp"]:
            # Запрет изменения системных файлов
            for part in parts:
                if part.startswith("/etc") or part.startswith("/usr/bin") or part.startswith("/bin"):
                    return False, "Запрещено изменение системных файлов"
        
        # Проверки для systemctl
        if base_command == "systemctl":
            dangerous_actions = ["disable", "mask", "stop"]
            if any(action in parts for action in dangerous_actions):
                critical_services = ["sshd", "network", "systemd"]
                if any(service in " ".join(parts) for service in critical_services):
                    return False, "Запрещено останавливать критичные сервисы"
        
        return True, None
    
    def sanitize_command(self, command: str) -> str:
        """Санитизация команды (удаление опасных символов)"""
        # Удаляем управляющие символы
        sanitized = "".join(char for char in command if char.isprintable())
        
        # Убираем множественные пробелы
        sanitized = " ".join(sanitized.split())
        
        return sanitized
    
    def get_allowed_commands(self, level: CommandSecurityLevel = None) -> Set[str]:
        """Получить список разрешенных команд"""
        if level:
            return self.command_whitelist.get(level, set())
        
        # Возвращаем все кроме RESTRICTED
        allowed = set()
        for cmd_level, commands in self.command_whitelist.items():
            if cmd_level != CommandSecurityLevel.RESTRICTED:
                allowed.update(commands)
        
        return allowed
    
    def add_allowed_command(self, command: str, level: CommandSecurityLevel = CommandSecurityLevel.MODERATE):
        """Добавить команду в whitelist"""
        if level in self.command_whitelist:
            self.command_whitelist[level].add(command)
            logger.info(f"➕ Команда '{command}' добавлена в whitelist (уровень: {level.value})")
    
    def remove_allowed_command(self, command: str):
        """Удалить команду из whitelist"""
        for level, commands in self.command_whitelist.items():
            if command in commands:
                commands.remove(command)
                logger.info(f"➖ Команда '{command}' удалена из whitelist")
                break


# Глобальный экземпляр (будет заменен на DI)
command_validator = CommandValidator()
