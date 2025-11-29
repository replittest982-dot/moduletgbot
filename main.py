import asyncio
import logging
import os
import sys
from typing import Dict, Any 

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

# 🟢 СОЗДАЕМ СЛОВАРЬ ЗАВИСИМОСТЕЙ ДЛЯ ВНЕДРЕНИЯ
DI_DATA: Dict[str, Any] = {
    "db": db, 
    "tm": tm, 
    "store": store
}

# =========================================================================
# II. STARTUP, SHUTDOWN И ЗАПУСК
# =========================================================================

async def on_startup(*args, **kwargs): 
    global bot 
    logger.info("Bot starting up...")
    
    db = kwargs.get('db')
    tm = kwargs.get('tm')

    await set_default_commands(bot, ADMIN_ID)
    
    os.makedirs('data', exist_ok=True)
    os.makedirs('sessions', exist_ok=True)
    
    if db:
        await db.init() 
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


async def on_shutdown(*args, **kwargs):
    global bot
    
    await bot.session.close() 
    logger.info("Bot session closed.")
    
    tm = kwargs.get('tm')
    if tm:
        await tm.stop_all_workers()
        logger.info("All Telethon workers stopped.")
        
    logger.info("Bot shutting down.")


async def main():
    if not all([BOT_TOKEN, API_ID, API_HASH]):
        logger.critical("❌ One or more essential variables are missing. Check your config.py/ .env file.")
        sys.exit(1)

    # 1. РЕГИСТРАЦИЯ MIDDLEWARE ДЛЯ ВНЕДРЕНИЯ ЗАВИСИМОСТЕЙ
    di_middleware = DependencyInjectorMiddleware(DI_DATA)
    dp.message.outer_middleware(di_middleware)
    dp.callback_query.outer_middleware(di_middleware)
    
    # 2. Регистрация Middleware для RateLimit 
    rate_middleware = RateLimitMiddleware(store, limit=RATE_LIMIT_TIME)
    dp.message.outer_middleware(rate_middleware)
    dp.callback_query.outer_middleware(rate_middleware)
    
    # Регистрация роутеров
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(drop_router) 
    
    # Регистрация обработчиков жизненного цикла
    dp.startup.register(on_startup) 
    dp.shutdown.register(on_shutdown) 

    try:
        bot_info = await bot.get_me()
        logger.info(f"Bot connected successfully. @{bot_info.username}. Admin ID: {ADMIN_ID}")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Telegram. Check BOT_TOKEN: {e}")
        sys.exit(1)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Starting polling...")
    await dp.start_polling(bot, **DI_DATA)

if __name__ == '__main__':
    if sys.platform == 'win32': 
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"Critical error in main loop: {e}", exc_info=True)
    
