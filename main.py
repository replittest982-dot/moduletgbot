import asyncio
import logging
import os
import sys
import traceback
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

# Инициализация бота и диспетчера
storage = MemoryStorage() 
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='Markdown'))
dp = Dispatcher(storage=storage)

# Инициализация TelethonManager с заглушкой для bot, который будет установлен ниже
tm = TelethonManager(bot, store, db) 

# Передаем объекты в роутеры и другие модули (для обеспечения круговой зависимости)
user_router.db = db
user_router.tm = tm
user_router.store = store

admin_router.db = db
admin_router.tm = tm # Также нужен для запуска воркера
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
            f"📍 Трейсбек:\n```\n{traceback.format_exc()[:1500]}\n```" # Используем тройной бэктик для форматирования
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
    
    # 1. Создание папок
    if not os.path.exists(SESSION_DIR): os.makedirs(SESSION_DIR)
    if not os.path.exists('data'): os.makedirs('data')
    
    # 2. Инициализация БД
    await db.init() 
    
    # 3. Запуск активных воркеров
    active_users = await db.get_active_telethon_users()
    for uid in active_users:
        # Проверяем подписку перед запуском
        if await db.check_subscription(uid): 
            asyncio.create_task(tm.start_client_task(uid))
        else:
            # Если подписка истекла, но статус "активный", исправляем в БД
            await db.set_telethon_status(uid, False) 

async def main():
    # Регистрация Middleware
    rate_limit_middleware = RateLimitMiddleware(store)
    dp.message.middleware(rate_limit_middleware)
    dp.callback_query.middleware(rate_limit_middleware)
    
    # Регистрация роутеров
    dp.include_router(user_router)
    dp.include_router(admin_router)
    
    # Регистрация startup hook
    dp.startup.register(on_startup)
    
    # ПРОВЕРКА ТОКЕНА
    try:
        bot_info = await bot.get_me()
        logger.info(f"Bot connected successfully. @{bot_info.username}. Admin ID: {ADMIN_ID}")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Telegram. Check BOT_TOKEN: {e}")
        # Если бот не может подключиться, нет смысла продолжать
        sys.exit(1)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Polling started...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"Critical error in main loop: {e}", exc_info=True)
