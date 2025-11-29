import asyncio
import logging
import os
import sys

# Импорт из сторонних библиотек
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage 
from aiogram.client.default import DefaultBotProperties

# Импорт из локальных модулей
from .config import BOT_TOKEN, DB_PATH, SESSIONS_DIR, DATA_DIR, TEMP_DIR, ADMIN_ID
from .db import AsyncDatabase
from .utils import GlobalStorage, DependencyInjectorMiddleware
from .telethon_manager import TelethonManager
from .handlers import user_router, admin_router, drop_router
from .set_commands import set_default_commands

logger = logging.getLogger(__name__)

async def on_startup(bot: Bot, db: AsyncDatabase, tm: TelethonManager):
    logger.info("Starting up...")
    
    # Создание папок
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # Инициализация БД
    await db.init()
    
    # Установка команд бота
    await set_default_commands(bot)

    # Автозапуск Telethon-воркеров
    user_ids = await db.get_active_telethon_users()
    if user_ids:
        logger.info(f"Found {len(user_ids)} active Telethon sessions. Starting workers...")
        start_tasks = [tm.start_client_task(uid) for uid in user_ids]
        await asyncio.gather(*start_tasks)

async def on_shutdown(db: AsyncDatabase, tm: TelethonManager):
    logger.info("Shutting down...")
    
    # Остановка всех Telethon-воркеров
    stop_tasks = [tm.stop_worker(uid) for uid in list(tm.store.active_workers.keys())]
    await asyncio.gather(*stop_tasks)
    
    # Закрытие соединения с БД
    if db.conn:
        await db.conn.close()

async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Инициализация
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    
    # Пользовательские классы
    db = AsyncDatabase()
    store = GlobalStorage()
    tm = TelethonManager(bot, store, db)

    # Middleware для внедрения зависимостей
    injector = DependencyInjectorMiddleware(data={'db': db, 'tm': tm, 'store': store})
    user_router.message.middleware(injector)
    user_router.callback_query.middleware(injector)
    admin_router.message.middleware(injector)
    admin_router.callback_query.middleware(injector)
    drop_router.message.middleware(injector)

    # Регистрация роутеров
    dp.include_router(admin_router)
    dp.include_router(user_router)
    dp.include_router(drop_router)
    
    # Хендлеры старта/шатадауна
    dp.startup.register(lambda _: on_startup(bot, db, tm))
    dp.shutdown.register(lambda _: on_shutdown(db, tm))

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ОШИБКА: Заполните BOT_TOKEN в .env")
        sys.exit(1)
        
    try:
        # Для корректного запуска модулей (если main.py не в корне)
        # Если вы запускаете python main.py из корня проекта,
        # замените "from .x import y" на "from x import y"
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
