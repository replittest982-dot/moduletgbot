import os
from dotenv import load_dotenv
from datetime import timedelta
import pytz

load_dotenv()

# =========================================================================
# I. CORE НАСТРОЙКИ
# =========================================================================

# --- AIOGRAM BOT ---
BOT_TOKEN = os.getenv("BOT_TOKEN") 

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID"))
except (TypeError, ValueError):
    ADMIN_ID = None 

# --- TELETHON CLIENT ---
try:
    API_ID = int(os.getenv("API_ID"))
except (TypeError, ValueError):
    API_ID = None 

API_HASH = os.getenv("API_HASH") 

# =========================================================================
# II. НАСТРОЙКИ ФУНКЦИОНАЛА
# =========================================================================

DB_NAME = "bot_database.db"
SESSION_DIR = "sessions" 

TIMEZONE_MSK = pytz.timezone("Europe/Moscow") 

TARGET_CHANNEL_URL = os.getenv("TARGET_CHANNEL_URL")
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME")

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
