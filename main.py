import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# ✅ Импортируем config как модуль для TelethonManager
import config 
# Импортируем переменные для удобства
from config import BOT_TOKEN, ADMIN_ID, QR_TIMEOUT 
from handlers import user_router, admin_router, router
from db import AsyncDatabase
from telethon_manager import TelethonManager

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # --- 1. ИНИЦИАЛИЗАЦИЯ КЛЮЧЕВЫХ ОБЪЕКТОВ ---
    db = AsyncDatabase()
    
    # ✅ ФИКС: Передаем обязательные аргументы db и config в TelethonManager
    tm = TelethonManager(db=db, config=config) 
    
    await db.init()
    logger.info("Database initialized successfully.")
    
    # --- 2. ПЕРЕДАЧА ГЛОБАЛЬНЫХ ОБЪЕКТОВ В КОНТЕКСТ DP ---
    # Все объекты и константы доступны в хендлерах через kwargs["dp"]["key"]
    dp["db"] = db
    dp["tm"] = tm
    dp["admin_id"] = ADMIN_ID 
    dp["qr_timeout"] = QR_TIMEOUT 
    
    # --- 3. РЕГИСТРАЦИЯ РОУТЕРОВ ---
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(router)
    
    logger.info("Starting bot...")
    
    # --- 4. СТАРТ ПОЛЛИНГА И ГРАЦИОЗНОЕ ЗАВЕРШЕНИЕ ---
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
