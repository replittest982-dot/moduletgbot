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

# --- LOCAL IMPORTS ---
from telethon_manager import TelethonManager, GlobalStorage, SESSION_DIR
from db import AsyncDatabase
from handlers import user_router, admin_router, RateLimitMiddleware
from config import BOT_TOKEN, ADMIN_ID, API_ID, API_HASH, DB_NAME

# =========================================================================
# I. КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# =========================================================================

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация глобального хранилища и БД
store = GlobalStorage()
db = AsyncDatabase(os.path.join('data', DB_NAME))
tm = TelethonManager(None, store, db) # Инициализируем tm с заглушкой для bot

# Инициализация бота и диспетчера
storage = MemoryStorage() 
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='Markdown'))
dp = Dispatcher(storage=storage)

# Передаем объекты в модули
tm.bot = bot
user_router.db = db
user_router.tm = tm
user_router.store = store
admin_router.db = db
admin_router.store = store


# =========================================================================
# II. ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК
# =========================================================================

@dp.error()
async def global_error_handler(event: ErrorEvent):
    exception = event.exception
    
    with suppress():
        if isinstance(exception, TelegramConflictError):
            logger.critical("🚨 TelegramConflictError: Another bot instance is running!")
            return True

    logger.critical(f"🚨 КРИТИЧЕСКАЯ ОШИБКА: {exception.__class__.__name__}: {exception}", exc_info=True)
    
    if ADMIN_ID:
        error_msg = (
            f"🔥 **BOT CRASH** 🔥\n"
            f"❌ Тип: `{exception.__class__.__name__}`\n"
            f"📄 Ошибка: `{str(exception)[:100]}`\n" 
            f"📍 Трейсбек:\n`{traceback.format_exc()[:1500]}`"
        )
        try:
            await bot.send_message(ADMIN_ID, error_msg, parse_mode='Markdown')
        except: pass
            
    return True

# =========================================================================
# III. STARTUP И ЗАПУСК
# =========================================================================

async def on_startup(dispatcher: Dispatcher):
    logger.info("Bot starting...")
    # Создание папок и инициализация БД
    if not os.path.exists(SESSION_DIR): os.makedirs(SESSION_DIR)
    if not os.path.exists('data'): os.makedirs('data')
    await db.init() 
    
    # Запуск активных воркеров
    active_users = await db.get_active_telethon_users()
    for uid in active_users:
        if await db.check_subscription(uid): asyncio.create_task(tm.start_client_task(uid))

async def main():
    dp.message.middleware(RateLimitMiddleware(store))
    dp.callback_query.middleware(RateLimitMiddleware(store))
    
    dp.include_router(user_router)
    dp.include_router(admin_router)
    
    dp.startup.register(on_startup)
    
    # ПРОВЕРКА ТОКЕНА
    try:
        await bot.get_me()
        logger.info(f"Bot connected successfully. Admin ID: {ADMIN_ID}")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Telegram: {e}")
        # Если токен невалиден, бот упадет ниже на delete_webhook. 
        # Дополнительная проверка не обязательна, но информативна.

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Polling started...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Critical error in main loop: {e}", exc_info=True)
