import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware 

from config import BOT_TOKEN, ADMIN_ID, API_ID, API_HASH, TEMP_DIR, TARGET_CHANNEL_URL, SUPPORT_BOT_USERNAME
from handlers import user_router, admin_router, drop_router
from telethon_manager import TelethonManager
from db import AsyncDatabase
import set_commands

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DependencyMiddleware(BaseMiddleware):
    def __init__(self, **data):
        self.data = data
        super().__init__()

    async def __call__(self, handler, event, data):
        data.update(self.data)
        return await handler(event, data)

async def on_startup(bot: Bot, db: AsyncDatabase):
    await db.init() 
    await set_commands.set_my_commands(bot, ADMIN_ID) 
    logger.info("✅ Bot, DB, and Commands ready")

async def on_shutdown(db: AsyncDatabase):
    await db.close()
    logger.info("❌ Database connection closed. Bot stopped.")

async def main():
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set!")
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
    
    default_properties = DefaultBotProperties(parse_mode='Markdown') 
    bot = Bot(token=BOT_TOKEN, default=default_properties)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    dp.startup.register(on_startup) 
    dp.shutdown.register(on_shutdown)
    
    # ✅ ИСПРАВЛЕНИЕ 2: Удалено store=tm.store
    middleware = DependencyMiddleware(db=db, tm=tm, bot=bot, config=config)
    dp.update.outer_middleware(middleware)
    
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(drop_router)
    
    logger.info("Bot is starting...")
    # ✅ ИСПРАВЛЕНИЕ 6: Удален не поддерживаемый аргумент db=db
    await dp.start_polling(bot) 

if __name__ == "__main__":
    try:
        asyncio.run(main()) 
    except KeyboardInterrupt:
        logger.warning("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
