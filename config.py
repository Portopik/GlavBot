import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота
BOT_TOKEN = os.getenv("8057838212:AAGXJcxc4hEk5qzVjK37IocVDPC_hxj8nwA")

# Настройки по умолчанию
DEFAULT_WARN_LIMIT = 3
DEFAULT_ANTIFLOOD_COUNT = 5  # сообщений
DEFAULT_ANTIFLOOD_SECONDS = 10  # за сколько секунд
DEFAULT_SLOWMODE_SECONDS = 5

# Тексты по умолчанию
DEFAULT_WELCOME_MESSAGE = "👋 Добро пожаловать, {name}!\nПожалуйста, ознакомься с правилами: /rules"
DEFAULT_RULES = """📋 Правила чата:
1. Уважайте друг друга
2. Не спамить
3. Запрещены оскорбления
4. Не рекламировать
5. Администратор всегда прав 😉"""

# Путь к БД
DATABASE_PATH = "bot_database.db"
