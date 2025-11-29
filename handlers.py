import asyncio
import textwrap
from typing import Dict, Optional, Any
from aiogram import Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Update
from telethon_manager import SESSION_DIR, TelethonAuth

# Инициализация роутеров
user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router")

# --- MIDDLEWARE ---
class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, store, limit=0.5):
        self.store = store
        self.limit = limit
        self.last_request: Dict[int, float] = {} 
        super().__init__()

    async def __call__(self, handler, event: Update, data):
        uid = get_user_id_from_update(event) 
        if uid is None:
            return await handler(event, data)

        now = asyncio.get_event_loop().time()
        
        # Проверка лимита
        if uid in self.last_request and now - self.last_request[uid] < self.limit:
            return 

        self.last_request[uid] = now
        
        return await handler(event, data)

# --- Вспомогательная функция ---
def get_user_id_from_update(update: Update) -> Optional[int]:
    """Извлекает ID пользователя из различных типов обновлений Aiogram."""
    if hasattr(update, 'from_user') and update.from_user:
        return update.from_user.id
    if hasattr(update, 'message') and update.message and update.message.from_user:
        return update.message.from_user.id
    if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.from_user:
         return update.callback_query.from_user.id
    return None

# --- ЗАГЛУШКИ ОБРАБОТЧИКОВ ---
# Здесь будут обработчики команд, которые вы добавите позже.
# Например:
# @user_router.message()
# async def cmd_start(message: types.Message):
#     await message.answer("Бот запущен!")
