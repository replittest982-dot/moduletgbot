import asyncio
import textwrap
import re
from typing import Dict, Optional, Any
import logging

from aiogram import Router, F
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.filters.state import StateFilter

from telethon_manager import SESSION_DIR, TelethonAuth 
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# Инициализация роутеров
user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router")

# --- MIDDLEWARE & HELPERS ---
class RateLimitMiddleware(BaseMiddleware):
    # ... (Оставлено как в предыдущем ответе) ...
    pass 

def get_user_id_from_update(update: Update) -> Optional[int]:
    # ... (Оставлено как в предыдущем ответе) ...
    pass

def check_valid_phone(phone: str) -> Optional[str]:
    # ... (Оставлено как в предыдущем ответе) ...
    pass

# =========================================================================
# I. ОБЩИЕ КОМАНДЫ И НАВИГАЦИЯ
# =========================================================================

@user_router.message(Command("start"))
async def cmd_start(message: Message, db, tm, store):
    # ... (Оставлено как в предыдущем ответе) ...
    pass

@user_router.message(Command("logout"))
async def cmd_logout(message: Message, tm):
    await tm.stop_worker(message.from_user.id)


# =========================================================================
# II. ЛОГИКА АВТОРИЗАЦИИ TELETHON (/login)
# =========================================================================

# (Весь код cmd_login, auth_get_phone, auth_get_code, auth_get_password остается здесь)
# ...

# =========================================================================
# III. ПРОМОКОДЫ
# =========================================================================

class PromoState(StatesGroup):
    waiting_for_code = State()

@user_router.message(Command("promo"))
async def cmd_promo(message: Message, state: FSMContext):
    await state.set_state(PromoState.waiting_for_code)
    await message.answer("🔑 **Введите ваш промокод** для активации подписки:")

@user_router.message(PromoState.waiting_for_code)
async def process_promo_code(message: Message, db, state: FSMContext):
    code = message.text.strip().upper() # Промокоды хранятся в upper case
    await state.clear()
    
    success, result_msg = await db.apply_promo_code(message.from_user.id, code)
    
    await message.answer(result_msg, parse_mode='Markdown')
    if success:
        await message.answer("Для запуска воркера используйте команду /login.")


# =========================================================================
# IV. АДМИН-ПАНЕЛЬ
# =========================================================================

class AdminState(StatesGroup):
    creating_promo_code = State()

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, tm):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ **Доступ запрещен.**")
        
    text = (
        "👑 **Админ-панель**\n"
        "Выберите действие:\n"
        "/create_promo - Создать новый промокод\n"
        "/stats - Показать статистику (заглушка)\n"
    )
    await message.answer(text, parse_mode='Markdown')

@admin_router.message(Command("create_promo"))
async def cmd_create_promo(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminState.creating_promo_code)
    await message.answer("🔑 Введите данные для нового промокода в формате:\n"
                         "`КОД ДНИ_СУБС. МАКС_ИСПОЛЬЗОВАНИЙ`\n"
                         "Пример: `TEST20 20 50`")

@admin_router.message(AdminState.creating_promo_code)
async def process_create_promo(message: Message, db, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    await state.clear()
    
    parts = message.text.split()
    if len(parts) != 3:
        return await message.answer("❌ Неверный формат. Используйте: `КОД ДНИ_СУБС. МАКС_ИСПОЛЬЗОВАНИЙ`")
        
    code, days_str, max_uses_str = parts
    code = code.upper() # Код в верхнем регистре
    
    try:
        days = int(days_str)
        max_uses = int(max_uses_str)
        if days <= 0 or max_uses <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Дни и макс. использования должны быть положительными числами.")
        
    success = await db.create_promo_code(code, days, max_uses)
    
    if success:
        await message.answer(f"✅ Промокод **`{code}`** успешно создан!\n"
                             f"Дней: {days}, Лимит: {max_uses}.", parse_mode='Markdown')
    else:
        await message.answer("❌ Промокод с таким кодом уже существует.")
