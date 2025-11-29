import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware 

# Импорт ваших реальных компонентов
from config import BOT_TOKEN, ADMIN_ID, API_ID, API_HASH, TEMP_DIR, TARGET_CHANNEL_URL, SUPPORT_BOT_USERNAME
from handlers import user_router, admin_router, drop_router
from telethon_manager import TelethonManager
from db import AsyncDatabase
import set_commands # Для установки команд

# ✅ ИСПРАВЛЕНИЕ 38: Настройка логирования до импортов
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Middleware для передачи зависимостей (✅ ИСПРАВЛЕНИЕ 3, 37) ---
class DependencyMiddleware(BaseMiddleware):
    def __init__(self, **data):
        self.data = data
        super().__init__()

    async def __call__(self, handler, event, data):
        data.update(self.data)
        return await handler(event, data)
# ------------------------------------------------------------------

# --- on_startup для инициализации (✅ ИСПРАВЛЕНИЕ 10, 35) ---
async def on_startup(bot: Bot, db: AsyncDatabase):
    await db.init() # 1. Инициализация БД
    await set_commands.set_my_commands(bot, ADMIN_ID) # 2. Установка команд
    logger.info("✅ Bot, DB, and Commands ready")
# ------------------------------------------------------------------

# --- on_shutdown для очистки (✅ ИСПРАВЛЕНИЕ 6, 51) ---
async def on_shutdown(db: AsyncDatabase):
    await db.close()
    logger.info("❌ Database connection closed. Bot stopped.")


async def main():
    # Проверка токена
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set in config.py!")
        return

    db = AsyncDatabase() 
    
    class Config:
        API_ID = API_ID
        API_HASH = API_HASH
        TEMP_DIR = TEMP_DIR 
        TARGET_CHANNEL_URL = TARGET_CHANNEL_URL
        SUPPORT_BOT_USERNAME = SUPPORT_BOT_USERNAME
    config = Config()

    tm = TelethonManager(db, config)
    
    # Инициализация Aiogram
    default_properties = DefaultBotProperties(parse_mode='Markdown') 
    bot = Bot(token=BOT_TOKEN, default=default_properties)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрация startup/shutdown
    dp.startup.register(on_startup) 
    dp.shutdown.register(on_shutdown)
    
    # Инжекция зависимостей
    middleware = DependencyMiddleware(db=db, tm=tm, bot=bot, store=tm.store, config=config)
    dp.update.outer_middleware(middleware)
    
    # --- Регистрация роутеров ---
    dp.include_router(user_router)
    
    # ✅ ИСПРАВЛЕНИЕ 34: Фильтры удалены. Проверка ADMIN_ID будет в хендлерах.
    dp.include_router(admin_router)
    dp.include_router(drop_router) # ✅ ИСПРАВЛЕНИЕ 36: drop_router включен
    
    logger.info("Bot is starting...")
    await dp.start_polling(bot, db=db) # Передаем db для on_startup

if __name__ == "__main__":
    try:
        asyncio.run(main()) 
    except KeyboardInterrupt:
        logger.warning("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
