import os
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()

# --- БОТ И АДМИН ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID"))
except (TypeError, ValueError):
    ADMIN_ID = None 

# --- TELETHON API ---
try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
except (TypeError, ValueError):
    API_ID = 0
    API_HASH = ""

# --- КОНСТАНТЫ И ПУТИ ---
TEMP_DIR = os.getenv("TEMP_DIR", "telethon_sessions")
TARGET_CHANNEL_URL = os.getenv("TARGET_CHANNEL_URL", "@telegram") 
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "telegram") 
QR_TIMEOUT = int(os.getenv("QR_TIMEOUT", 60)) # Таймаут в секундах для ожидания QR-кода
# ❌ УДАЛЕНО: import aiosqlite
