import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.client.default import DefaultBotProperties

# АБСОЛЮТНЫЕ ИМПОРТЫ
from config import BOT_TOKEN, DB_PATH, SESSIONS_DIR, DATA_DIR, TEMP_DIR, ADMIN_ID
from db import AsyncDatabase
from utils import GlobalStorage, DependencyInjectorMiddleware
from telethon_manager import TelethonManager
from handlers import user_router, admin_router, drop_router
from set_commands import set_default_commands

logger = logging.getLogger(__name__)

async def on_startup(*args, **kwargs):
    logger.info("Starting up...")
    
    # Извлечение зависимостей (переданы через DI)
    # Но так как on_startup вызывается через lambda, 
    # аргументы будут в kwargs, если мы их туда передадим,
    # или мы можем использовать замыкание (как сделано ниже в main).
    # Здесь просто логируем.
    
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

async def start_services(bot, db, tm):
    await db.init()
    await set_default_commands(bot)
    user_ids = await db.get_active_telethon_users()
    if user_ids:
        logger.info(f"Found {len(user_ids)} active sessions.")
        for uid in user_ids:
            asyncio.create_task(tm.start_client_task(uid))

async def on_shutdown(*args, **kwargs):
    logger.info("Shutting down...")

async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    
    db = AsyncDatabase(DB_PATH)
    store = GlobalStorage()
    tm = TelethonManager(bot, store, db)

    injector = DependencyInjectorMiddleware(data={'db': db, 'tm': tm, 'store': store, 'bot': bot})
    
    # Глобальная регистрация middleware
    dp.message.outer_middleware(injector)
    dp.callback_query.outer_middleware(injector)

    dp.include_router(admin_router)
    dp.include_router(user_router)
    dp.include_router(drop_router)
    
    # Ручной запуск сервисов
    await on_startup()
    await start_services(bot, db, tm)

    try:
        await dp.start_polling(bot)
    finally:
        await tm.stop_all_workers()
        if db.conn: await db.conn.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
