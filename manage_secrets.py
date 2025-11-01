# #!/usr/bin/env python3
# """
# CLI утилита для управления секретами сервера
# """

# import sys
# import argparse
# import secrets
# import base64
# import getpass
# from app.utils.secrets_manager import get_secrets_manager


# def generate_key(length: int = 32) -> str:
#     """Генерация случайного ключа"""
#     return secrets.token_hex(length)


# def generate_salt() -> str:
#     """Генерация случайной соли"""
#     salt = secrets.token_bytes(32)
#     return base64.b64encode(salt).decode('ascii')


# def set_secret(args):
#     """Установка секрета"""
#     manager = get_secrets_manager()
    
#     if not manager.is_secure_storage_available():
#         print("❌ Безопасное хранилище недоступно!")
#         print("💡 Установите: pip install keyring")
#         print("   Или настройте Vault через USE_VAULT=true")
#         return 1
    
#     key = args.key
#     value = args.value
    
#     # Если значение не указано, запрашиваем интерактивно
#     if not value:
#         if args.generate:
#             if key == "encryption_salt":
#                 value = generate_salt()
#                 print(f"🔑 Сгенерирована соль: {value}")
#             else:
#                 value = generate_key()
#                 print(f"🔑 Сгенерирован ключ: {value}")
#         else:
#             value = getpass.getpass(f"Введите значение для {key}: ")
    
#     if not value:
#         print("❌ Значение не может быть пустым")
#         return 1
    
#     # Сохраняем секрет
#     if manager._set_secret(key, value):
#         print(f"✅ Секрет '{key}' успешно сохранен")
#         return 0
#     else:
#         print(f"❌ Ошибка сохранения секрета '{key}'")
#         return 1


# def get_secret(args):
#     """Получение секрета"""
#     manager = get_secrets_manager()
#     key = args.key
    
#     # Карта ключей к методам
#     getters = {
#         "encryption_key": manager.get_encryption_key,
#         "encryption_salt": lambda: base64.b64encode(manager.get_encryption_salt()).decode() if manager.get_encryption_salt() else None,
#         "jwt_secret": manager.get_jwt_secret,
#     }
    
#     getter = getters.get(key)
#     if not getter:
#         print(f"❌ Неизвестный ключ: {key}")
#         print(f"💡 Доступные ключи: {', '.join(getters.keys())}")
#         return 1
    
#     value = getter()
    
#     if value:
#         if args.show:
#             print(f"🔑 {key}: {value}")
#         else:
#             print(f"✅ Секрет '{key}' установлен (используйте --show для отображения)")
#         return 0
#     else:
#         print(f"❌ Секрет '{key}' не найден")
#         return 1


# def delete_secret(args):
#     """Удаление секрета"""
#     manager = get_secrets_manager()
#     key = args.key
    
#     # Подтверждение
#     if not args.force:
#         confirm = input(f"⚠️  Удалить секрет '{key}'? (yes/no): ")
#         if confirm.lower() != "yes":
#             print("Отменено")
#             return 0
    
#     if manager.delete_secret(key):
#         print(f"✅ Секрет '{key}' удален")
#         return 0
#     else:
#         print(f"❌ Ошибка удаления секрета '{key}'")
#         return 1


# def list_secrets(args):
#     """Список секретов"""
#     manager = get_secrets_manager()
    
#     print("\n🔒 Информация о хранилище секретов:")
#     info = manager.get_storage_info()
#     print(f"   Тип: {info['storage_type']}")
#     print(f"   Местоположение: {info['location']}")
#     print(f"   Безопасное хранилище: {'✅ Доступно' if info['secure_storage_available'] else '❌ Недоступно'}")
    
#     print("\n📋 Установленные секреты:")
#     secrets_list = manager.list_secrets()
    
#     if secrets_list:
#         for secret in secrets_list:
#             print(f"   ✅ {secret}")
#     else:
#         print("   (нет установленных секретов)")
    
#     # Проверка валидности
#     print("\n🔍 Проверка обязательных секретов:")
#     valid, missing = manager.validate_secrets()
    
#     if valid:
#         print("   ✅ Все обязательные секреты установлены")
#     else:
#         print("   ❌ Отсутствуют секреты:")
#         for secret in missing:
#             print(f"      - {secret}")
#         print(f"\n💡 Установите: python manage_secrets.py set {missing[0]}")
    
#     return 0 if valid else 1


# def validate_secrets(args):
#     """Валидация секретов"""
#     manager = get_secrets_manager()
#     valid, missing = manager.validate_secrets()
    
#     if valid:
#         print("✅ Все обязательные секреты установлены")
#         return 0
#     else:
#         print("❌ Отсутствуют обязательные секреты:")
#         for secret in missing:
#             print(f"   - {secret}")
#         return 1


# def generate_command(args):
#     """Генерация ключей"""
#     key_type = args.type
    
#     if key_type == "encryption_key":
#         key = generate_key(32)
#         print(f"🔑 Encryption Key (32 bytes):")
#         print(key)
#     elif key_type == "encryption_salt":
#         salt = generate_salt()
#         print(f"🧂 Encryption Salt (32 bytes, base64):")
#         print(salt)
#     elif key_type == "jwt_secret":
#         secret = generate_key(64)
#         print(f"🔐 JWT Secret (64 bytes):")
#         print(secret)
#     elif key_type == "all":
#         print("🔑 Encryption Key:")
#         print(generate_key(32))
#         print("\n🧂 Encryption Salt:")
#         print(generate_salt())
#         print("\n🔐 JWT Secret:")
#         print(generate_key(64))
#     else:
#         print(f"❌ Неизвестный тип: {key_type}")
#         print("💡 Доступные типы: encryption_key, encryption_salt, jwt_secret, all")
#         return 1
    
#     return 0


# def main():
#     parser = argparse.ArgumentParser(
#         description="Управление секретами для Remote Client Server",
#         formatter_class=argparse.RawDescriptionHelpFormatter,
#         epilog="""
# Примеры использования:

#   # Генерация и сохранение ключа шифрования
#   python manage_secrets.py set encryption_key --generate

#   # Установка секрета вручную
#   python manage_secrets.py set encryption_key --value "my-secret-key"

#   # Установка секрета интерактивно (безопасно)
#   python manage_secrets.py set jwt_secret

#   # Просмотр секрета
#   python manage_secrets.py get encryption_key --show

#   # Список всех секретов
#   python manage_secrets.py list

#   # Валидация секретов
#   python manage_secrets.py validate

#   # Удаление секрета
#   python manage_secrets.py delete encryption_key

#   # Генерация всех ключей
#   python manage_secrets.py generate all
#         """
#     )
    
#     subparsers = parser.add_subparsers(dest='command', help='Команды')
    
#     # Set command
#     set_parser = subparsers.add_parser('set', help='Установить секрет')
#     set_parser.add_argument('key', choices=['encryption_key', 'encryption_salt', 'jwt_secret'],
#                            help='Ключ секрета')
#     set_parser.add_argument('--value', help='Значение секрета')
#     set_parser.add_argument('--generate', action='store_true',
#                            help='Сгенерировать случайное значение')
#     set_parser.set_defaults(func=set_secret)
    
#     # Get command
#     get_parser = subparsers.add_parser('get', help='Получить секрет')
#     get_parser.add_argument('key', choices=['encryption_key', 'encryption_salt', 'jwt_secret'],
#                            help='Ключ секрета')
#     get_parser.add_argument('--show', action='store_true',
#                            help='Показать значение')
#     get_parser.set_defaults(func=get_secret)
    
#     # Delete command
#     delete_parser = subparsers.add_parser('delete', help='Удалить секрет')
#     delete_parser.add_argument('key', choices=['encryption_key', 'encryption_salt', 'jwt_secret'],
#                               help='Ключ секрета')
#     delete_parser.add_argument('--force', action='store_true',
#                               help='Без подтверждения')
#     delete_parser.set_defaults(func=delete_secret)
    
#     # List command
#     list_parser = subparsers.add_parser('list', help='Список секретов')
#     list_parser.set_defaults(func=list_secrets)
    
#     # Validate command
#     validate_parser = subparsers.add_parser('validate', help='Проверить секреты')
#     validate_parser.set_defaults(func=validate_secrets)
    
#     # Generate command
#     generate_parser = subparsers.add_parser('generate', help='Сгенерировать ключи')
#     generate_parser.add_argument('type', 
#                                 choices=['encryption_key', 'encryption_salt', 'jwt_secret', 'all'],
#                                 help='Тип ключа для генерации')
#     generate_parser.set_defaults(func=generate_command)
    
#     args = parser.parse_args()
    
#     if not args.command:
#         parser.print_help()
#         return 1
    
#     return args.func(args)


# if __name__ == '__main__':
#     sys.exit(main())
