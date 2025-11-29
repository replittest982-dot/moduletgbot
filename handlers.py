import asyncio
import textwrap
import re
from typing import Dict, Optional, Any
import logging

from aiogram import Router, F, Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

# --- LOCAL IMPORTS ---
# Здесь предполагается, что db, tm, store будут внедрены в main.py
# from db import AsyncDatabase
# from telethon_manager import TelethonManager, GlobalStorage
from telethon_manager import SESSION_DIR, TelethonAuth 

logger = logging.getLogger(__name__)

# Инициализация роутеров (используем F.func для инъекции)
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
        
        if uid in self.last_request and now - self.last_request[uid] < self.limit:
            return 

        self.last_request[uid] = now
        
        return await handler(event, data)

def get_user_id_from_update(update: Update) -> Optional[int]:
    """Извлекает ID пользователя из различных типов обновлений Aiogram."""
    if hasattr(update, 'from_user') and update.from_user:
        return update.from_user.id
    if hasattr(update, 'message') and update.message and update.message.from_user:
        return update.message.from_user.id
    if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.from_user:
         return update.callback_query.from_user.id
    return None

def check_valid_phone(phone: str) -> Optional[str]:
    """Проверяет формат номера телефона."""
    # Удаляем все, кроме цифр и плюса
    phone = re.sub(r'[^\d+]', '', phone) 
    # Телефон должен начинаться с + и иметь 10-15 цифр
    if re.fullmatch(r'^\+\d{10,15}$', phone):
        return phone
    return None

# =========================================================================
# I. ОБЩИЕ КОМАНДЫ И НАВИГАЦИЯ
# =========================================================================

@user_router.message(Command("start"))
async def cmd_start(message: Message, db, tm, store):
    uid = message.from_user.id
    
    # 1. Проверка подписки (заглушка, полная логика будет в db.py)
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
            "Вы можете приобрести подписку или использовать промокод.\n"
        )
    
    text = (
        f"🤖 Привет, **{message.from_user.full_name}**!\n"
        f"Это бот для управления Telethon-сессиями.\n\n"
        f"{status_text}\n"
        f"Доступные команды: /login, /logout, /promo"
    )
    
    await message.answer(text, parse_mode='Markdown')

@user_router.message(Command("logout"))
async def cmd_logout(message: Message, tm):
    await tm.stop_worker(message.from_user.id)
    # Фактическая остановка и удаление сессии происходит в telethon_manager.py


# =========================================================================
# II. ЛОГИКА АВТОРИЗАЦИИ TELETHON (/login)
# =========================================================================

@user_router.message(Command("login"))
async def cmd_login(message: Message, tm, state: FSMContext, db):
    uid = message.from_user.id
    
    if not await db.check_subscription(uid):
        return await message.answer("❌ У вас нет активной подписки. Используйте /start.")
        
    await state.clear()
    await state.set_state(TelethonAuth.phone)
    
    await message.answer(
        "☎️ **Начало авторизации Telethon**\n"
        "Пожалуйста, введите номер телефона вашего аккаунта Telegram "
        "в международном формате (начиная с **+**)."
    )

@user_router.message(TelethonAuth.phone)
async def auth_get_phone(message: Message, tm, state: FSMContext):
    phone = check_valid_phone(message.text)
    
    if not phone:
        return await message.answer("❌ **Неверный формат номера.** Попробуйте еще раз, начиная с + (например, `+79001234567`).")

    await message.answer("🔍 **Отправка кода...** Пожалуйста, проверьте Telegram (придет в личные сообщения или в официальный канал 'Telegram').")

    # Отправка кода через TelethonManager
    phone_code_hash = await tm.send_code(message.from_user.id, phone)
    
    if phone_code_hash is None:
        return await message.answer("❌ **Ошибка авторизации.** Попробуйте /login еще раз.")
    elif phone_code_hash.startswith("ERROR_"):
        await state.clear()
        if "FLOOD_WAIT" in phone_code_hash:
            seconds = phone_code_hash.split(":")[1]
            return await message.answer(f"❌ **Слишком много попыток.** Повторите через {seconds} секунд. Авторизация отменена.")
        elif "INVALID_PHONE" in phone_code_hash:
            return await message.answer("❌ **Неверный номер телефона.** Попробуйте /login еще раз. Авторизация отменена.")
        return await message.answer("❌ **Неизвестная ошибка.** Попробуйте /login еще раз.")

    # Сохраняем данные и переходим к следующему состоянию
    await state.update_data(phone=phone, phone_code_hash=phone_code_hash)
    await state.set_state(TelethonAuth.code)
    
    await message.answer(
        "🔑 **Код получен.**\n"
        "Теперь введите **код**, который пришел вам в Telegram."
    )

@user_router.message(TelethonAuth.code)
async def auth_get_code(message: Message, tm, state: FSMContext):
    code = message.text.strip()
    
    if not code.isdigit():
        return await message.answer("❌ **Код должен состоять только из цифр.** Пожалуйста, введите код.")
    
    data = await state.get_data()
    phone = data['phone']
    phone_hash = data['phone_code_hash']
    
    await message.answer("⏳ **Проверка кода...**")
    
    success, result_msg = await tm.sign_in(message.from_user.id, phone, code, phone_hash)
    
    if success:
        await state.clear()
        return await message.answer(result_msg)
    
    if result_msg == "PASSWORD_NEEDED":
        await state.set_state(TelethonAuth.password)
        return await message.answer("🔒 **Требуется пароль 2FA.**\nПожалуйста, введите ваш пароль от облака Telegram.")
        
    # Все остальные ошибки (неверный код, RPC и т.д.)
    await state.clear()
    await message.answer(result_msg)
    return await message.answer("⚠️ Авторизация не удалась. Попробуйте /login еще раз.")

@user_router.message(TelethonAuth.password)
async def auth_get_password(message: Message, tm, state: FSMContext):
    password = message.text.strip()
    
    await message.answer("⏳ **Проверка пароля...**")
    
    success, result_msg = await tm.sign_in_password(message.from_user.id, password)
    
    await state.clear()
    await message.answer(result_msg)
    
    if not success:
        await message.answer("⚠️ Авторизация не удалась. Попробуйте /login еще раз.")

# =========================================================================
# III. ПРОМОКОДЫ (Заглушка)
# =========================================================================

@user_router.message(Command("promo"))
async def cmd_promo(message: Message, db):
    # Логика обработки промокода будет реализована после подтверждения работы авторизации
    await message.answer("🔑 Введите ваш промокод для активации подписки:")
    # Здесь можно было бы перейти в состояние FSM
    
# ... Здесь могут быть другие роутеры, включая admin_router и drop_router ...
