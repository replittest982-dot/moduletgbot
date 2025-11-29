import os
import logging
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env (если он используется)
# Если вы передаете эти данные напрямую в код, эта строка не нужна,
# но лучше оставить ее для гибкости.
load_dotenv()

# --- 1. КРИТИЧЕСКИЕ НАСТРОЙКИ ---
BOT_TOKEN = "7868097991:AAEieED31N93hsrJIQnC6omaXuAZ3uA3hdk" # Токен вашего Aiogram бота
API_ID = 37185453 
API_HASH = "7e40ac0357454dddbfbbfce511c9ddf1"

# Ваш ID администратора (для админ-панели и проверки подписки)
ADMIN_ID = 6256576302 
if not ADMIN_ID:
    logging.warning("ADMIN_ID is not set. Admin functions are disabled.")

# --- 2. ПУТИ И ДИРЕКТОРИИ ---
DATA_DIR = os.path.join(os.getcwd(), "data")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions") # Директория для файлов сессий Telethon
TEMP_DIR = SESSIONS_DIR # Используем SESSIONS_DIR как временную директорию

# Создание папок при запуске
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

# --- 3. НАСТРОЙКИ БОТА И ЛОГИКИ ---
SUPPORT_BOT_USERNAME = "suppor_tstatpro1bot"
TARGET_CHANNEL_URL = "https://t.me/STAT_PRO1"

# ✅ Исправление: Обработка URL канала для корректной работы get_chat_member
# Преобразуем URL в формат @username (или username)
TARGET_CHANNEL_URL_CLEAN = TARGET_CHANNEL_URL.lstrip('https://t.me/')
if not TARGET_CHANNEL_URL_CLEAN.startswith('@'):
    TARGET_CHANNEL_URL_CLEAN = f"@{TARGET_CHANNEL_URL_CLEAN}"
    
# Используйте TARGET_CHANNEL_URL_CLEAN в коде (например, в handlers.py)
TARGET_CHANNEL_URL = TARGET_CHANNEL_URL_CLEAN 

QR_TIMEOUT = 60 # Время жизни QR-кода в секундах
