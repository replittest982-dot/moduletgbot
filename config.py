import os
import pytz
from dotenv import load_dotenv

load_dotenv()

# =========================================================================
# I. КОНСТАНТЫ И НАСТРОЙКИ
# =========================================================================

# --- AIOGRAM BOT ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
try:
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

# --- TELETHON CLIENT ---
try:
    API_ID: int = int(os.getenv("API_ID", "0"))
except ValueError:
    API_ID = 0
    
API_HASH: str = os.getenv("API_HASH", "")

# --- НАСТРОЙКИ ---
SUPPORT_BOT_USERNAME: str = os.getenv("SUPPORT_BOT_USERNAME", "support_bot")
TARGET_CHANNEL_URL: str = os.getenv("TARGET_CHANNEL_URL", "@default_channel")

# --- PATHS & TIME ---
TIMEZONE: str = "Europe/Moscow"
DB_NAME: str = "bot_database.db"
SESSIONS_DIR: str = "sessions"
DATA_DIR: str = "data"
TEMP_DIR: str = os.path.join(DATA_DIR, 'temp')
DB_PATH: str = os.path.join(DATA_DIR, DB_NAME)

# --- TELETHON SETTINGS ---
QR_TIMEOUT: int = 180 
FLOOD_TASK_TIMEOUT: int = 5

# --- TIMEZONE OBJECT ---
MOSCOW_TZ = pytz.timezone(TIMEZONE)

# Проверка
if not all([BOT_TOKEN, API_ID, API_HASH, ADMIN_ID]):
    print("⚠️ ПРЕДУПРЕЖДЕНИЕ: Не все переменные окружения заполнены в .env")
