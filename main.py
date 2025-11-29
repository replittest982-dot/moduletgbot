import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, ADMIN_ID, QR_TIMEOUT # QR_TIMEOUT остается в импорте
from handlers import user_router, admin_router, router
from db import AsyncDatabase
from telethon_manager import TelethonManager

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # --- ИНИЦИАЛИЗАЦИЯ КЛЮЧЕВЫХ ОБЪЕКТОВ ---
    db = AsyncDatabase()
    tm = TelethonManager()
    await db.init()
    logger.info("Database initialized successfully.")
    
    # --- ПЕРЕДАЧА ГЛОБАЛЬНЫХ ОБЪЕКТОВ В КОНТЕКСТ DP ---
    dp["db"] = db
    dp["tm"] = tm
    dp["admin_id"] = ADMIN_ID 
    # ✅ ДОБАВЛЕНИЕ QR_TIMEOUT В КОНТЕКСТ ДП
    dp["qr_timeout"] = QR_TIMEOUT 
    
    # --- РЕГИСТРАЦИЯ РОУТЕРОВ ---
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(router)
    
    logger.info("Starting bot...")
    
    # --- СТАРТ ПОЛЛИНГА И ГРАЦИОЗНОЕ ЗАВЕРШЕНИЕ ---
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()
        logger.info("Bot stopped and database connection closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown by user.")
    except Exception as e:
        logger.critical(f"Critical error during bot startup/runtime: {e}")
