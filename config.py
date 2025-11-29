import os
from dotenv import load_dotenv
from datetime import timedelta
import pytz

# Загрузка переменных окружения из .env файла
load_dotenv()

# =========================================================================
# I. CORE НАСТРОЙКИ
# =========================================================================

# --- AIOGRAM BOT ---
# Исправлено: Загрузка по имени "BOT_TOKEN"
BOT_TOKEN = os.getenv("BOT_TOKEN") 

# Исправлено: Загрузка по имени "ADMIN_ID"
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID"))
except (TypeError, ValueError):
    ADMIN_ID = None 

# --- TELETHON CLIENT ---
# Исправлено: Загрузка по имени "API_ID"
try:
    API_ID = int(os.getenv("API_ID"))
except (TypeError, ValueError):
    API_ID = None 

# Исправлено: Загрузка по имени "API_HASH"
API_HASH = os.getenv("API_HASH") 

# =========================================================================
# II. НАСТРОЙКИ ФУНКЦИОНАЛА
# =========================================================================

# Добавлено: Имя базы данных
DB_NAME = "bot_database.db"
SESSION_DIR = "sessions" 

# Часовой пояс
TIMEZONE_MSK = pytz.timezone("Europe/Moscow") 

# Настройки чекера подписки
TARGET_CHANNEL_URL = os.getenv("TARGET_CHANNEL_URL")
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME")

# Добавлено/Исправлено: Более жесткий лимит для RateLimitMiddleware
RATE_LIMIT_TIME = 0.5 
RATE_LIMIT_COUNT = 5 

TELETHON_TIMEOUT = 10 

# =========================================================================
# III. НАСТРОЙКИ DROP-СИСТЕМЫ
# =========================================================================

DROP_SESSION_TIMEOUT = timedelta(hours=2)
try:
    DROP_LOG_CHAT_ID = int(os.getenv("DROP_LOG_CHAT_ID", ADMIN_ID))
except:
    DROP_LOG_CHAT_ID = ADMIN_ID
