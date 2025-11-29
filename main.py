import asyncio
import logging
import os
import sys
from typing import Dict, Any # 🟢 ИСПРАВЛЕНИЕ: Используем Dict из typing

# --- AIOGRAM ---
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.client.default import DefaultBotProperties

# --- LOCAL IMPORTS ---
from telethon_manager import TelethonManager, GlobalStorage
from db import AsyncDatabase
from handlers import user_router, admin_router, drop_router, RateLimitMiddleware, DependencyInjectorMiddleware
from config import BOT_TOKEN, ADMIN_ID, API_ID, API_HASH, DB_NAME, RATE_LIMIT_TIME
from set_commands import set_default_commands

# =========================================================================
# I. НАСТРОЙКА ЛОГИРОВАНИЯ И ИНИЦИАЛИЗАЦИИ
# =========================================================================

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация глобального хранилища и БД
store = GlobalStorage()
db_path = os.path.join('data', DB_NAME)
db = AsyncDatabase(db_path)

storage = MemoryStorage() 
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='Markdown')) 
dp = Dispatcher(storage=storage)

# Передаем bot, store, db в менеджер Telethon
tm = TelethonManager(bot, store, db) 

# 🟢 СОЗДАЕМ СЛОВАРЬ ЗАВИСИМОСТЕЙ
DI_DATA: Dict[str, Any] = {
    "db": db, 
    "tm": tm, 
    "store": store
}

# =========================================================================
# II. STARTUP И ЗАПУСК
# =========================================================================

async def on_startup(_): # 🟢 ИСПРАВЛЕНИЕ: on_startup принимает только один аргумент (Dispatcher), но мы игнорируем его
    global bot # 🟢 ИСПРАВЛЕНИЕ: Доступ к глобальному объекту bot
    logger.info("Bot starting up...")
    
    await set_default_commands(bot, ADMIN_ID)
    
    os.makedirs('data', exist_ok=True)
    os.makedirs('sessions', exist_ok=True)
    await db.init() 
    
    # Запуск активных воркеров
    active_users = await db.get_active_telethon_users() 
    for uid in active_users:
        if await db.check_subscription(uid): 
            asyncio.create_task(tm.start_client_task(uid)) 
        else:
            await db.set_telethon_status(uid, False) 
    
    if active_users:
        logger.info(f"Successfully initiated startup for {len(active_users)} active workers.")
    else:
        logger.info("No active Telethon workers found on startup.")

async def main():
    if not all([BOT_TOKEN, API_ID, API_HASH]):
        logger.critical("❌ One or more essential variables are missing. Check your config.py/ .env file.")
        sys.exit(1)

    # 🟢 РЕГИСТРАЦИЯ MIDDLEWARE ДЛЯ ВНЕДРЕНИЯ ЗАВИСИМОСТЕЙ (ДО ВСЕХ РОУТЕРОВ)
    dp.message.outer_middleware(DependencyInjectorMiddleware(DI_DATA))
    dp.callback_query.outer_middleware(DependencyInjectorMiddleware(DI_DATA))
    
    # Регистрация Middleware для RateLimit (после DI Middleware, чтобы иметь доступ к store)
    rate_middleware = RateLimitMiddleware(store, limit=RATE_LIMIT_TIME)
    dp.message.outer_middleware(rate_middleware)
    dp.callback_query.outer_middleware(rate_middleware)
    
    # Регистрация роутеров
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(drop_router) 
    
    dp.startup.register(on_startup) 
    
    try:
        bot_info = await bot.get_me()
        logger.info(f"Bot connected successfully. @{bot_info.username}. Admin ID: {ADMIN_ID}")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Telegram. Check BOT_TOKEN: {e}")
        sys.exit(1)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Starting polling...")
    # 🟢 ИСПРАВЛЕНИЕ: Запускаем без **DI_DATA
    await dp.start_polling(bot)

if __name__ == '__main__':
    if sys.platform == 'win32': 
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"Critical error in main loop: {e}", exc_info=True)
