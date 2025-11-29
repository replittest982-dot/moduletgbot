import os
import pytz
from dotenv import load_dotenv

# Загрузка переменных окружения из .env
load_dotenv()

# --- AIOGRAM BOT ---
BOT_TOKEN = os.getenv("7868097991:AAEieED31N93hsrJIQnC6omaXuAZ3uA3hdk") 
try:
    ADMIN_ID = int(os.getenv("6256576302"))
except (TypeError, ValueError):
    ADMIN_ID = None # Важно, чтобы был None, если не установлен

# --- TELETHON CLIENT ---
API_ID = int(os.getenv("37185453"))
API_HASH = os.getenv("7e40ac0357454dddbfbbfce511c9ddf1")

# --- НАСТРОЙКИ ---
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "suppor_tstatpro1bot")
TARGET_CHANNEL_URL = os.getenv("TARGET_CHANNEL_URL") # Для проверки подписки
DB_NAME = 'bot_database.db'
TIMEZONE_MSK = pytz.timezone('Europe/Moscow')
RATE_LIMIT_TIME = 0.5
