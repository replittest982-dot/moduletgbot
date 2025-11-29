import os
import logging
from dotenv import load_dotenv

# Инициализация .env (для безопасности токена)
load_dotenv()

# --- 1. КРИТИЧЕСКИЕ НАСТРОЙКИ ---
# ✅ ИСПРАВЛЕНИЕ 4: Получаем токен из .env файла с резервом
BOT_TOKEN = os.getenv("BOT_TOKEN", "7868097991:AAEieED31N93hsrJIQnC6omaXuAZ3uA3hdk") 
API_ID = int(os.getenv("API_ID", 37185453))
API_HASH = os.getenv("API_HASH", "7e40ac0357454dddbfbbfce511c9ddf1")

ADMIN_ID = int(os.getenv("ADMIN_ID", 6256576302)) 
if not ADMIN_ID:
    logging.warning("ADMIN_ID is not set. Admin functions are disabled.")

# --- 2. ПУТИ И ДИРЕКТОРИИ ---
DATA_DIR = os.path.join(os.getcwd(), "data")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
TEMP_DIR = SESSIONS_DIR # ✅ ИСПРАВЛЕНИЕ 5: Устранение дублирования

# ✅ ИСПРАВЛЕНИЕ 2: Создание папок при импорте
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

# --- 3. НАСТРОЙКИ БОТА И ЛОГИКИ ---
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "suppor_tstatpro1bot")
TARGET_CHANNEL_URL_RAW = os.getenv("TARGET_CHANNEL_URL", "https://t.me/STAT_PRO1")

# ✅ ИСПРАВЛЕНИЕ 3: Обработка URL канала
TARGET_CHANNEL_URL = TARGET_CHANNEL_URL_RAW.lstrip('https://t.me/').lstrip('@')
if not TARGET_CHANNEL_URL.startswith('@'):
    TARGET_CHANNEL_URL = f"@{TARGET_CHANNEL_URL}"

QR_TIMEOUT = int(os.getenv("QR_TIMEOUT", 60))
