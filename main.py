import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# ✅ ИМПОРТЫ ПРОЕКТА
from config import BOT_TOKEN, ADMIN_ID
from handlers import user_router, admin_router, router # router включает errors_handler
from db import AsyncDatabase
from telethon_manager import TelethonManager

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    # --- 1. ИНИЦИАЛИЗАЦИЯ КЛЮЧЕВЫХ ОБЪЕКТОВ ---
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    db = AsyncDatabase()
    tm = TelethonManager()
    
    # Инициализация подключения к БД
    await db.init()
    logger.info("Database initialized successfully.")
    
    # --- 2. ПЕРЕДАЧА ГЛОБАЛЬНЫХ ОБЪЕКТОВ ---
    # Передаем объекты БД, Telethon и Admin ID в контекст диспетчера.
    # Они будут доступны в хендлерах через аргументы `db`, `tm`, `admin_id` и `bot`.
    dp["db"] = db
    dp["tm"] = tm
    dp["admin_id"] = ADMIN_ID 
    
    # --- 3. РЕГИСТРАЦИЯ РОУТЕРОВ ---
    # Важно: router, содержащий errors_handler, регистрируется последним (или в любом порядке, 
    # если это роутер ошибок), но его присутствие критично.
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(router)
    
    logger.info("Starting bot...")
    
    # --- 4. СТАРТ ПОЛЛИНГА И ГРАЦИОЗНОЕ ЗАВЕРШЕНИЕ ---
    try:
        # dp.start_polling запускает бота
        await dp.start_polling(bot)
    finally:
        # Обязательно закрываем подключение к БД при завершении работы
        await db.close()
        # Закрываем сессию бота
        await bot.session.close()
        logger.info("Bot stopped and database connection closed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown by user.")
    except Exception as e:
        logger.critical(f"Critical error during bot startup/runtime: {e}")
