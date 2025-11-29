import os
import pytz
from dotenv import load_dotenv

load_dotenv()

# =========================================================================
# I. КОНСТАНТЫ И НАСТРОЙКИ
# =========================================================================

# --- AIOGRAM BOT ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))

# --- TELETHON CLIENT ---
API_ID: int = int(os.getenv("API_ID", "0"))
API_HASH: str = os.getenv("API_HASH", "")

# --- НАСТРОЙКИ ---
SUPPORT_BOT_USERNAME: str = os.getenv("SUPPORT_BOT_USERNAME", "support_bot")
TARGET_CHANNEL_URL: str = os.getenv("TARGET_CHANNEL_URL", "@default_channel")

# --- PATHS & TIME ---
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")
DB_NAME: str = os.getenv("DB_NAME", "bot_database.db")
SESSIONS_DIR: str = os.getenv("SESSIONS_DIR", "sessions")
DATA_DIR: str = os.getenv("DATA_DIR", "data")
TEMP_DIR: str = os.path.join(DATA_DIR, 'temp')
DB_PATH: str = os.path.join(DATA_DIR, DB_NAME)

# --- TELETHON SETTINGS ---
QR_TIMEOUT: int = 180 
FLOOD_TASK_TIMEOUT: int = 5

# --- TIMEZONE OBJECT ---
MOSCOW_TZ = pytz.timezone(TIMEZONE)

# Проверка, что основные константы установлены
if not all([BOT_TOKEN, API_ID, API_HASH, ADMIN_ID]):
    raise ValueError("Необходимо заполнить BOT_TOKEN, API_ID, API_HASH и ADMIN_ID в .env")
