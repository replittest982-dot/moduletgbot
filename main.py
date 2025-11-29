import asyncio
import logging
import os
import sys
from contextlib import suppress

# --- AIOGRAM ---
from aiogram import Bot, Dispatcher, Router, types
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ErrorEvent
from aiogram.exceptions import TelegramConflictError
from aiogram.dispatcher.middlewares.base import BaseMiddleware 

# --- LOCAL IMPORTS ---
# 🟢 ИМПОРТЫ TelethonManager, GlobalStorage
from telethon_manager import TelethonManager, GlobalStorage, SESSION_DIR
# 🟢 ИМПОРТ AsyncDatabase 
from db import AsyncDatabase
from handlers import user_router, admin_router, drop_router, RateLimitMiddleware 
from config import BOT_TOKEN, ADMIN_ID, API_ID, API_HASH, DB_NAME, RATE_LIMIT_TIME

# =========================================================================
# I. КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# =========================================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация глобального хранилища и БД
store = GlobalStorage()
db_path = os.path.join('data', DB_NAME)
db = AsyncDatabase(db_path) # <-- Здесь db создается первым

tm = TelethonManager(bot, store, db) # <-- Здесь tm получает ссылку на db

# ... (остальной код main.py, инъекция зависимостей, и т.д.) ...
