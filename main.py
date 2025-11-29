import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# 💡 ВАЖНО: Убедитесь, что вы импортируете все нужные файлы
from config import BOT_TOKEN, ADMIN_ID, API_ID, API_HASH, TEMP_DIR 
from handlers import user_router, admin_router, drop_router
from telethon_manager import TelethonManager

# --- Заглушки для типов (УДАЛИТЕ И ЗАМЕНИТЕ НА РЕАЛЬНЫЕ ИМПОРТЫ!) ---
class AsyncDatabase: 
    async def init(self): pass
    async def get_subscription_status(self, uid, admin_id): return (True, "Активна")
    async def get_user(self, uid): return {'telethon_active': 0}
    async def update_user(self, uid, **kwargs): pass
    async def create_promo_code(self, code, days, max_uses): return True
    async def apply_promo_code(self, uid, code): return (True, "Промокод активирован.")
    async def update_chat_pc_mapping(self, *args): pass

class GlobalStorage: 
    def __init__(self):
        self.active_workers = {}
        self.process_progress = {}
        self.temp_data = {}
        self.drop_mapping = {}
# ------------------------------------------------------------------------

# 1. Настройка логирования и ОПРЕДЕЛЕНИЕ logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__) # <-- ИСПРАВЛЕНИЕ: Переменная logger теперь определена

async def main():
    # 2. Инициализация объектов
    db = AsyncDatabase() 
    await db.init()
    store = GlobalStorage()
    
    # Создание объекта конфигурации для TelethonManager
    class Config:
        API_ID = API_ID
        API_HASH = API_HASH
        TEMP_DIR = TEMP_DIR 
    
    config = Config()

    # Инициализация TelethonManager
    tm = TelethonManager(db, store, config) 
    
    # Инициализация Aiogram
    default_properties = DefaultBotProperties(parse_mode='Markdown') 
    bot = Bot(token=BOT_TOKEN, default=default_properties)
    
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # 3. Инжекция зависимостей и роутеры
    dp.workflow_data.update(db=db, tm=tm, store=store, config=config, bot=bot)

    dp.include_router(user_router)
    
    admin_router.message.filter(F.from_user.id == ADMIN_ID)
    admin_router.callback_query.filter(F.from_user.id == ADMIN_ID)
    dp.include_router(admin_router)
    dp.include_router(drop_router)
    
    # 4. Запуск
    logger.info("Bot is starting...") # <-- Используем logger
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
