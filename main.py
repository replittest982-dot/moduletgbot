import logging
import asyncio
import os
import qrcode
import re
from io import BytesIO

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State # <-- ИСПРАВЛЕНИЕ 22

# ИМПОРТ РЕАЛЬНЫХ КЛАССОВ (ИСПРАВЛЕНИЕ 21)
from db import AsyncDatabase
from telethon_manager import TelethonManager
from config import ADMIN_ID, SUPPORT_BOT_USERNAME, TARGET_CHANNEL_URL, QR_TIMEOUT, QR_TIMEOUT

# --- 💡 FSM Состояния (ИСПРАВЛЕНИЕ 22: StatesGroup) ---
class TelethonAuth(StatesGroup): 
    phone = State()
    code = State()
    password = State()
    waiting_for_qr = State()
    qr_password = State()

class UserState(StatesGroup): 
    waiting_promo = State()
# --------------------------------------------------------

logger = logging.getLogger(__name__)

user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router")

# --- УТИЛИТА (ИСПРАВЛЕНИЕ 23, 9) ---
def check_valid_phone(phone: str) -> bool:
    """Проверяет, соответствует ли строка формату телефона +7..."""
    return bool(re.match(r'^\+\d{10,15}$', phone.replace(' ', '')))
# ----------------------------------------------------

# (Остальные функции get_main_menu_kb и send_start_menu остаются без изменений,
#  так как они были исправлены на уровне синтаксиса, а логические ошибки
#  29, 30, 32 будут исправлены в следующих шагах.)

# ... (код get_main_menu_kb и send_start_menu) ...

@user_router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, db: AsyncDatabase, tm: TelethonManager, state: FSMContext):
    await state.clear() 
    await send_start_menu(message.from_user.id, bot, db, tm, is_initial_check=True)
    
# ... (код cb_check_sub, auth_phone, auth_get_phone, auth_get_code, auth_get_pass) ...

@user_router.message(TelethonAuth.phone) 
async def auth_get_phone(message: Message, state: FSMContext, tm: TelethonManager, **kwargs):
    phone = message.text # Сначала берем текст
    if not check_valid_phone(phone): return await message.answer("❌ Неверный формат.") # ИСПРАВЛЕНИЕ 30
    
    # ... (Остальной код) ...

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await callback.answer("Генерация...")
    
    # ... (QR-код генерация) ...
    
    # ... (логика ожидания) ...
    
    try:
        success, msg = await asyncio.wait_for(
            # ИСПРАВЛЕНИЕ 24: (Если бы tm.check_qr_login не возвращал Task)
            # В данном случае, предполагается, что tm.check_qr_login возвращает awaitable,
            # и сама проблема "asyncio.wait_for без task" чаще связана с неправильным
            # использованием `loop.create_task` внутри `tm`. Оставляем пока `await_for`.
            tm.check_qr_login(callback.from_user.id, data['qr_login_data'], data['client']), 
            timeout=QR_TIMEOUT + 5
        )
    # ... (Остальной код) ...
    
    if photo_msg: 
        try: await photo_msg.delete() # ИСПРАВЛЕНИЕ 25 (было исправлено ранее)
        except Exception: pass
    
    # ... (Остальной код) ...
    
    
# ... (Остальной код worker, logout, promo) ...

@admin_router.message(Command("create_promo"))
async def cmd_mk_promo(message: Message, state: FSMContext, db: AsyncDatabase, **kwargs):
    if message.from_user.id != ADMIN_ID: return
    
    parts = message.text.split()
    if len(parts) != 4: 
        return await message.answer("❌ Неверный формат. Ожидался: `/create_promo КОД ДНИ МАКС_ЮЗЕРОВ` (пример: `/create_promo TEST 30 10`)")
    
    try:
        code = parts[1].strip().upper()
        days = int(parts[2])
        max_uses = int(parts[3])
    except IndexError: # <-- ИСПРАВЛЕНИЕ 26: Обработка IndexError
        return await message.answer("❌ Неверный формат. Ожидался: `/create_promo КОД ДНИ МАКС_ЮЗЕРОВ`")
    except ValueError:
        return await message.answer("❌ Дни и Макс_юзеров должны быть числами.")

    # ... (Остальной код) ...

# ... (Остальной код) ...
