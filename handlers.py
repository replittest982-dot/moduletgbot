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

# --- LOCAL IMPORTS ---
from telethon_manager import SESSION_DIR, TelethonAuth 
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# Инициализация роутеров
user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router")

# --- MIDDLEWARE & HELPERS ---
class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, store, limit=0.5):
        self.store = store
        self.limit = limit
        self.last_request: Dict[int, float] = {} 
        super().__init__()

    async def __call__(self, handler, event: Update, data):
        uid = get_user_id_from_update(event) 
        if uid is None: return await handler(event, data)
        now = asyncio.get_event_loop().time()
        if uid in self.last_request and now - self.last_request[uid] < self.limit: return 
        self.last_request[uid] = now
        return await handler(event, data)

def get_user_id_from_update(update: Update) -> Optional[int]:
    if hasattr(update, 'from_user') and update.from_user: return update.from_user.id
    if hasattr(update, 'message') and update.message and update.message.from_user: return update.message.from_user.id
    if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.from_user: return update.callback_query.from_user.id
    return None

def check_valid_phone(phone: str) -> Optional[str]:
    phone = re.sub(r'[^\d+]', '', phone) 
    if re.fullmatch(r'^\+\d{10,15}$', phone): return phone
    return None

# =========================================================================
# I. ОБЩИЕ КОМАНДЫ И НАВИГАЦИЯ
# =========================================================================

@user_router.message(Command("start"))
async def cmd_start(message: Message, db, tm, store):
    uid = message.from_user.id
    is_subscribed = await db.check_subscription(uid)
    
    status_text = ""
    if is_subscribed:
        user = await db.get_user(uid)
        end_date = user.get('subscription_end_date', 'N/A')
        status_text = (
            "✅ **Подписка активна!**\n"
            f"Истекает: `{end_date}`\n"
        )
        if uid in tm.store.active_clients:
            status_text += "🟢 **Воркер Telethon запущен.**"
        else:
            status_text += "🔴 **Воркер Telethon остановлен.** Используйте /login для запуска."
    else:
        status_text = (
            "⚠️ **Подписка не активна.**\n"
            "Вы можете приобрести подписку или использовать промокод."
        )
    
    text = (
        f"🤖 Привет, **{message.from_user.full_name}**!\n"
        f"{status_text}\n"
        f"Доступные команды: /login, /logout, /promo"
    )
    await message.answer(text, parse_mode='Markdown')

@user_router.message(Command("logout"))
async def cmd_logout(message: Message, tm):
    await tm.stop_worker(message.from_user.id)


# =========================================================================
# II. ЛОГИКА АВТОРИЗАЦИИ TELETHON (/login) (Код оставлен как в предыдущем сообщении)
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
    code = message.text.strip()
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
    waiting_for_user_id = State()

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, tm):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ **Доступ запрещен.**")
        
    # 🟢 ЗАГЛУШКА УДАЛЕНА: Базовое меню админа
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
    
    try:
        days = int(days_str)
        max_uses = int(max_uses_str)
        if days <= 0 or max_uses <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Дни и макс. использования должны быть положительными числами.")
        
    success = await db.create_promo_code(code.upper(), days, max_uses)
    
    if success:
        await message.answer(f"✅ Промокод **`{code.upper()}`** успешно создан!\n"
                             f"Дней: {days}, Лимит: {max_uses}.")
    else:
        await message.answer("❌ Промокод с таким кодом уже существует.")

# ... (Остальной код handlers.py)
