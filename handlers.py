import asyncio
import logging
import os
import re
import random
import string
from datetime import datetime
from typing import Dict, Optional, List, Union, Any, Callable, Awaitable
from functools import wraps
from contextlib import suppress
import traceback

# --- AIOGRAM ---
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, Update, Bot 
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.dispatcher.middlewares.base import BaseMiddleware 

# --- LOCAL IMPORTS ---
from config import ADMIN_ID, SUPPORT_BOT_USERNAME, RATE_LIMIT_TIME
from telethon_manager import TelethonAuth, SESSION_DIR

logger = logging.getLogger(__name__)

# =========================================================================
# I. FSM СОСТОЯНИЯ И РОУТЕРЫ
# =========================================================================

user_router = Router(name="user_router")
admin_router = Router(name="admin_router")

# --- FSM States ---
class PromoStates(StatesGroup):
    waiting_for_code = State()

class AdminStates(StatesGroup):
    waiting_for_user_id_for_sub = State() 
    waiting_for_sub_days = State()
    waiting_for_promo_days = State()
    waiting_for_promo_uses = State()

# =========================================================================
# II. MIDDLEWARE И UTILS
# =========================================================================

def get_user_id_from_update(update: Union[Update, Message, CallbackQuery]) -> Optional[int]:
    """Безопасное извлечение ID пользователя из объекта обновления."""
    # Обработка стандартного объекта Update
    if isinstance(update, Update):
        if update.from_user: return update.from_user.id
        if update.message: return update.message.from_user.id
        if update.callback_query: return update.callback_query.from_user.id
    
    # Обработка прямых объектов Message или CallbackQuery 
    elif isinstance(update, (Message, CallbackQuery)):
        return update.from_user.id
        
    return None

class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, store, limit: float = RATE_LIMIT_TIME):
        self.limit = limit
        self.lock = asyncio.Lock()
        self.store = store
        # Добавляем ссылку на db для доступа к TIMEZONE_MSK
        self.db = user_router.db if hasattr(user_router, 'db') else None
        super().__init__()

    async def __call__(self, handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]], event: Update, data: Dict[str, Any]) -> Any:
        user_id = get_user_id_from_update(event)
        if not user_id: return await handler(event, data)

        # Пытаемся получить время из db, если оно есть
        now = self.db.get_current_time_msk() if self.db else datetime.now() 

        async with self.lock:
            last = self.store.last_user_request.get(user_id)
            if last and (now - last).total_seconds() < self.limit:
                return 
            self.store.last_user_request[user_id] = now
        
        return await handler(event, data)

def generate_promo_code(length=8):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

async def update_menu_after_action(user_id: int, state: FSMContext, bot_instance: Bot, callback_data: str = "profile_menu", edit_message: Optional[Message] = None):
    """Обновляет меню после успешного действия, используя фиктивный CallbackQuery."""
    await state.clear()
    
    # 1. Получаем/создаем сообщение для редактирования
    if not edit_message:
        try:
            edit_message = await bot_instance.send_message(user_id, "🔄 Обновление меню...")
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.warning(f"Failed to send menu update message to user {user_id}.")
            return

    # 2. Создаем фиктивный CallbackQuery
    fake_call = types.CallbackQuery( 
        id=f'fake_update_{callback_data}_{random.randint(1000, 9999)}', 
        from_user=types.User(id=user_id, is_bot=False, first_name="User"), 
        message=edit_message,
        data=callback_data 
    )
    
    # 3. Вызываем целевой хендлер
    if callback_data == "worker_menu":
        await account_menu(fake_call, state)
    elif callback_data == "profile_menu":
        await profile_menu(fake_call, state)
    elif callback_data == "start_menu":
        await cmd_start_from_update(fake_call, state)
    elif callback_data == "admin_stats":
        await admin_main_menu(fake_call, state)
    else:
        await profile_menu(fake_call, state)


# =========================================================================
# III. KEYBOARDS
# =========================================================================

def get_main_menu_keyboard(user_id: int, is_subscribed: bool, session_exists: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile_menu"))
    
    if is_subscribed:
        text = "⚙️ Управление аккаунтом" if session_exists else "🚪 Войти в аккаунт"
        builder.row(InlineKeyboardButton(text=text, callback_data="worker_menu"))
    
    if not is_subscribed:
        builder.row(InlineKeyboardButton(text="🔑 Активировать подписку", callback_data="enter_promo"))
    
    builder.row(InlineKeyboardButton(text="❓ Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}"))
    
    if user_id == ADMIN_ID:
        builder.row(InlineKeyboardButton(text="📊 Админ-панель", callback_data="admin_stats"))
        
    return builder.as_markup()

def get_account_menu_keyboard(is_worker_active: bool, session_exists: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    if is_worker_active:
        builder.row(InlineKeyboardButton(text="🛑 Остановить Аккаунт", callback_data="stop_worker"))
    elif session_exists: 
        builder.row(InlineKeyboardButton(text="▶️ Запустить Аккаунт", callback_data="start_worker"))
        
    builder.row(InlineKeyboardButton(text="🚪 Сменить аккаунт/Авторизация", callback_data="auth_method_menu"))
        
    builder.row(InlineKeyboardButton(text="🔙 Профиль", callback_data="profile_menu"))
    return builder.as_markup()

def get_admin_promo_menu_keyboard(codes_list: List[Dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo"))
    
    for promo in codes_list:
        code = promo['code']
        uses = promo['uses_left']
        days = promo['days']
        uses_display = "∞" if uses == -1 else uses
        
        text = f"🔑 {code} ({days} дн. | {uses_display} исп.)"
        builder.row(
            InlineKeyboardButton(text=text, callback_data=f"admin_view_promo_{code}"),
        )
        
    builder.row(InlineKeyboardButton(text="🔙 Админ-панель", callback_data="admin_stats"))
    return builder.as_markup()


# =========================================================================
# IV. HANDLERS (USER)
# =========================================================================

# --- START (MESSAGE) ---
@user_router.message(Command('start'))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await user_router.db.get_user(user_id) 
    is_subscribed = await user_router.db.check_subscription(user_id)
    session_exists = await asyncio.to_thread(os.path.exists, os.path.join(SESSION_DIR, f'session_{user_id}.session'))

    await message.answer("👋 Добро пожаловать в систему STATPRO.", reply_markup=get_main_menu_keyboard(user_id, is_subscribed, session_exists))

# --- START (CALLBACK/FAKE) ---
@user_router.callback_query(F.data == "start_menu")
async def cmd_start_from_update(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    user_id = call.from_user.id
    await state.clear()
    
    is_subscribed = await user_router.db.check_subscription(user_id)
    session_exists = await asyncio.to_thread(os.path.exists, os.path.join(SESSION_DIR, f'session_{user_id}.session'))

    await call.message.edit_text("👋 Добро пожаловать в систему STATPRO.", reply_markup=get_main_menu_keyboard(user_id, is_subscribed, session_exists))


# --- PROFILE MENU ---
@user_router.callback_query(F.data == "profile_menu")
async def profile_menu(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    message_to_edit = call.message
    await call.answer()
    
    await state.clear()
    
    user_data = await user_router.db.get_user(user_id)
    is_subscribed = await user_router.db.check_subscription(user_id)
    is_worker_active = user_data.get('telethon_active', False)
    session_exists = await asyncio.to_thread(os.path.exists, os.path.join(SESSION_DIR, f'session_{user_id}.session'))
    
    end_date_str = user_data.get('subscription_end_date')
    end_date_info = user_router.db.to_msk_aware(end_date_str).strftime('%d.%m.%Y %H:%M MSK') if is_subscribed and end_date_str else "Не активна"
    
    auth_status = "✅ Авторизован" if session_exists else "❌ Не авторизован"
    active_status = "Да" if is_worker_active else "Нет"
    
    text = (
        f"👤 **Ваш Профиль**\n\n"
        f"🔹 **ID:** `{user_id}`\n"
        f"✅ **Подписка:** {'Да' if is_subscribed else 'Нет'}\n"
        f"🗓️ **До:** `{end_date_info}`\n"
        f"🔗 **Статус авторизации:** {auth_status}\n"
        f"🚀 **Аккаунт запущен:** {active_status}"
    )
    
    builder = InlineKeyboardBuilder()
    if is_subscribed: 
        text_btn = "⚙️ Управление аккаунтом" if session_exists else "🚪 Войти в аккаунт"
        builder.row(InlineKeyboardButton(text=text_btn, callback_data="worker_menu"))
    else: 
        builder.row(InlineKeyboardButton(text="🔑 Активировать подписку", callback_data="enter_promo"))
        
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="start_menu"))
    
    await message_to_edit.edit_text(text, reply_markup=builder.as_markup())

# --- УПРАВЛЕНИЕ АККАУНТОМ ---
@user_router.callback_query(F.data == "worker_menu")
async def account_menu(call: types.CallbackQuery, state: FSMContext): 
    user_id = call.from_user.id
    message_to_edit = call.message 
    await call.answer()
    await state.clear()

    if not await user_router.db.check_subscription(user_id):
        await call.answer("❌ Ваша подписка истекла.", show_alert=True)
        return await update_menu_after_action(user_id, state, call.bot, callback_data="profile_menu", edit_message=message_to_edit) 

    user_data = await user_router.db.get_user(user_id)
    is_worker_active = user_data.get('telethon_active', False)
    session_exists = await asyncio.to_thread(os.path.exists, os.path.join(SESSION_DIR, f'session_{user_id}.session'))

    status_text = "✅ **Аккаунт запущен**." if is_worker_active else "❌ **Аккаунт остановлен**."
    
    text = f"⚙️ **Управление аккаунтом**\n\n{status_text}\n\n*Для смены аккаунта используйте кнопку 'Сменить аккаунт/Авторизация'.*"
    
    await message_to_edit.edit_text(text, reply_markup=get_account_menu_keyboard(is_worker_active, session_exists))


# --- WORKER ACTIONS ---
@user_router.callback_query(F.data == "stop_worker")
async def stop_worker_handler(call: types.CallbackQuery, state: FSMContext):
    await call.answer("Остановка аккаунта...", show_alert=False)
    await user_router.tm.stop_worker(call.from_user.id)
    return await account_menu(call, state)

@user_router.callback_query(F.data == "start_worker")
async def start_worker_handler(call: types.CallbackQuery, state: FSMContext):
    session_exists = await asyncio.to_thread(os.path.exists, os.path.join(SESSION_DIR, f'session_{call.from_user.id}.session'))
    if not session_exists:
        await call.answer("❌ Сессия не найдена. Сначала авторизуйтесь.", show_alert=True)
        return await account_menu(call, state)
        
    await call.answer("Запуск аккаунта...", show_alert=False)
    await user_router.tm.start_client_task(call.from_user.id)
    await asyncio.sleep(1) 
    return await account_menu(call, state)

# --- AUTH FLOW ---
@user_router.callback_query(F.data == "auth_method_menu")
async def auth_method_menu(call: types.CallbackQuery, state: FSMContext):
    await user_router.tm.stop_worker(call.from_user.id) 
    # Удаление временной сессии, если она осталась
    temp_path = os.path.join(SESSION_DIR, f'temp_{call.from_user.id}.session')
    final_path = os.path.join(SESSION_DIR, f'session_{call.from_user.id}.session')
    with suppress(FileNotFoundError): 
        await asyncio.to_thread(os.remove, temp_path)
        await asyncio.to_thread(os.remove, final_path) # Удаляем старую сессию
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📲 По номеру телефона", callback_data="auth_by_phone"))
    builder.row(InlineKeyboardButton(text="🔙 Управление аккаунтом", callback_data="worker_menu")) 
    
    await call.message.edit_text("🚪 **Выберите способ авторизации**:", reply_markup=builder.as_markup())
    await call.answer()

@user_router.callback_query(F.data == "auth_by_phone")
async def auth_by_phone_step1(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(TelethonAuth.PHONE)
    await call.message.edit_text("📲 Введите **номер телефона** (например, `+79001234567`):", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Отмена", callback_data="worker_menu")).as_markup())
    await call.answer()

@user_router.message(TelethonAuth.PHONE, F.text)
async def auth_by_phone_step2_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    if not re.match(r'^\+\d{10,15}$', phone):
        return await message.answer("❌ Неверный формат. Пример: `+79001234567`")
    
    await state.update_data(phone=phone, original_message=message) 
    path = os.path.join(SESSION_DIR, f'temp_{user_id}')
    
    client = user_router.tm.store.temp_auth_clients.get(user_id)
    if client and client.is_connected():
        await client.disconnect()
        
    client = user_router.tm.client(path, user_router.tm.API_ID, user_router.tm.API_HASH) # Использование TelethonClient из manager.py
    async with user_router.store.lock: user_router.store.temp_auth_clients[user_id] = client
        
    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)
        await state.update_data(phone=phone, sent_code=sent_code)
        await state.set_state(TelethonAuth.CODE)
        await message.answer(f"✅ Код отправлен на **{phone}**. Введите код:")
    except PhoneNumberInvalidError:
        await client.disconnect()
        await state.clear()
        await message.answer("❌ Неверный номер.")
    except Exception as e:
        logger.error(f"Auth Error: {e}", exc_info=True)
        await client.disconnect()
        await state.clear()
        await message.answer("❌ Ошибка отправки кода.")

@user_router.message(TelethonAuth.CODE)
async def auth_by_phone_step3_sign_in(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    code = message.text.strip()
    data = await state.get_data()
    client = user_router.tm.store.temp_auth_clients.get(user_id)
    if not client:
        await state.clear()
        return await message.answer("❌ Сессия истекла. Начните снова с меню.")
        
    try:
        if not client.is_connected(): await client.connect()
        # ВНИМАНИЕ: sign_in не принимает phone_code_hash в aiogram 3+ напрямую, 
        # используем `sign_in(phone, code, code_hash=data['sent_code'].phone_code_hash)`
        user_info = await client.sign_in(phone=data['phone'], code=code, phone_code_hash=data['sent_code'].phone_code_hash)
        await user_router.tm._finalize_auth(user_id, data['original_message'], state, user_info)
    except SessionPasswordNeededError:
        await state.set_state(TelethonAuth.PASSWORD)
        await message.answer("⚠️ **Введите облачный пароль (2FA):**")
    except Exception as e:
        logger.error(f"Sign-in Code Error: {e}")
        await message.answer("❌ Неверный код. Попробуйте снова.")

@user_router.message(TelethonAuth.PASSWORD)
async def auth_by_phone_step4_password(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    password = message.text.strip()
    data = await state.get_data()
    client = user_router.tm.store.temp_auth_clients.get(user_id)
    
    try:
        if not client.is_connected(): await client.connect()
        user_info = await client.sign_in(password=password)
        await user_router.tm._finalize_auth(user_id, data['original_message'], state, user_info)
    except PasswordHashInvalidError:
        await message.answer("❌ Неверный пароль.")
    except Exception as e:
        logger.error(f"Sign-in Password Error: {e}", exc_info=True)
        await state.clear()
        if client: await client.disconnect()
        await message.answer("❌ Ошибка входа.")

# --- PROMO ACTIVATION ---
@user_router.callback_query(F.data == "enter_promo")
async def enter_promo(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_for_code)
    await call.message.edit_text("🔑 **Введите промокод**:", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Отмена", callback_data="profile_menu")).as_markup())
    await call.answer()

@user_router.message(PromoStates.waiting_for_code, F.text)
async def process_promo_activation(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    days = await user_router.db.activate_promo_code(message.from_user.id, code)
    
    if days:
        await message.answer(f"🎉 **Промокод активирован!** +{days} дней.")
        
        await update_menu_after_action(message.from_user.id, state, message.bot, callback_data="profile_menu", edit_message=message)
        
    else:
        await state.clear()
        await message.answer("❌ **Неверный код или истек лимит.**", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Профиль", callback_data="profile_menu")).as_markup())


# =========================================================================
# V. HANDLERS (ADMIN)
# =========================================================================

# --- ADMIN MAIN MENU ---
@admin_router.callback_query(F.data == "admin_stats")
async def admin_main_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.answer()
    await state.clear()
    total_users = await admin_router.db.get_all_users_count()
    active_subs = await admin_router.db.get_active_subs_count()
    
    text = (
        f"📊 **Админ-панель**\n\n"
        f"👤 Всего пользователей: {total_users}\n"
        f"✅ Активных подписок: {active_subs}"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔑 Промокоды", callback_data="admin_promo_menu"))
    builder.row(InlineKeyboardButton(text="➕ Выдать подписку", callback_data="admin_give_sub"))
    builder.row(InlineKeyboardButton(text="⬅️ Главное меню", callback_data="start_menu"))
    
    await call.message.edit_text(text, reply_markup=builder.as_markup())

# --- ADMIN SUB MENU (PROMO) ---
@admin_router.callback_query(F.data == "admin_promo_menu")
async def admin_promo_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.answer()
    await state.clear()
    
    codes = await admin_router.db.get_all_promo_codes()
    
    text = f"🔑 **Управление промокодами**\n\nВсего активных кодов: {len(codes)}"
    
    await call.message.edit_text(text, reply_markup=get_admin_promo_menu_keyboard(codes))

# --- ADMIN CREATE PROMO (STEP 1: DAYS) ---
@admin_router.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_step1(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.answer()
    await state.set_state(AdminStates.waiting_for_promo_days)
    
    builder = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_promo_menu"))
    await call.message.edit_text("🗓️ **Введите количество дней** подписки для этого промокода (число):", reply_markup=builder.as_markup())

@admin_router.message(AdminStates.waiting_for_promo_days)
async def admin_create_promo_step2(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        days = int(message.text.strip())
        if days <= 0: raise ValueError
        await state.update_data(promo_days=days)
        await state.set_state(AdminStates.waiting_for_promo_uses)
        
        await message.answer(
            "🧮 **Введите количество использований** (число):\n\n"
            "0 - для **бесконечного** использования.",
            reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_promo_menu")).as_markup()
        )
    except ValueError:
        await message.answer("❌ Введите корректное число дней (больше 0).")

@admin_router.message(AdminStates.waiting_for_promo_uses)
async def admin_create_promo_step3(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        uses = int(message.text.strip())
        if uses < 0: raise ValueError
        
        data = await state.get_data()
        days = data['promo_days']
        
        new_code = generate_promo_code(10)
        success = await admin_router.db.create_promo_code(new_code, days, uses)
        
        if success:
            await state.clear()
            uses_display = "бесконечно" if uses == 0 else f"{uses} раз"
            
            text = (
                f"🎉 **Промокод создан!**\n\n"
                f"🔑 Код: `{new_code}`\n"
                f"🗓️ Дней: **{days}**\n"
                f"🧮 Использований: **{uses_display}**"
            )
            await message.answer(text, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 К промокодам", callback_data="admin_promo_menu")).as_markup())
        else:
            await message.answer("❌ Ошибка БД. Промокод с таким кодом уже существует или ошибка записи.") 
            await state.clear()

    except ValueError:
        await message.answer("❌ Введите корректное число использований (0 или больше).")

# --- ADMIN VIEW/DELETE PROMO ---
@admin_router.callback_query(F.data.startswith("admin_view_promo_"))
async def admin_view_promo(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.answer()
    
    code = call.data.split('_')[-1]
    promo = await admin_router.db.get_promo_code(code)
    
    if not promo:
        await call.message.edit_text("❌ Промокод не найден.", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 К промокодам", callback_data="admin_promo_menu")).as_markup())
        return

    uses_display = "Бесконечно" if promo['uses_left'] == -1 else promo['uses_left']
    
    text = (
        f"🔑 **Информация о промокоде: {promo['code']}**\n\n"
        f"🗓️ Дней подписки: **{promo['days']}**\n"
        f"🧮 Осталось использований: **{uses_display}**\n"
        f"⏳ Создан: {promo['created_at']} MSK"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_delete_promo_{code}"))
    builder.row(InlineKeyboardButton(text="🔙 К промокодам", callback_data="admin_promo_menu"))
    
    await call.message.edit_text(text, reply_markup=builder.as_markup())

@admin_router.callback_query(F.data.startswith("admin_delete_promo_"))
async def admin_delete_promo(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    code = call.data.split('_')[-1]
    await call.answer(f"Промокод {code} удален.", show_alert=True)
    
    await admin_router.db.delete_promo_code(code)
    
    await admin_promo_menu(call, state)


# --- ADMIN GIVE SUB ---
@admin_router.callback_query(F.data == "admin_give_sub")
async def admin_give_sub(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.answer()
    await state.set_state(AdminStates.waiting_for_user_id_for_sub)
    await call.message.edit_text("👤 **Введите ID пользователя**, которому нужно выдать подписку (число):", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_stats")).as_markup())

@admin_router.message(AdminStates.waiting_for_user_id_for_sub)
async def admin_give_sub_id(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        uid = int(message.text.strip())
        await admin_router.db.get_user(uid) 
        await state.update_data(target_uid=uid)
        await state.set_state(AdminStates.waiting_for_sub_days)
        await message.answer(f"🗓️ Пользователь `{uid}` выбран. Введите **количество дней** подписки (число):")
    except: await message.answer("❌ ID должен быть числом. Попробуйте снова.")

@admin_router.message(AdminStates.waiting_for_sub_days)
async def admin_give_sub_days(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        days = int(message.text.strip())
        if days <= 0: raise ValueError
        
        data = await state.get_data()
        uid = data['target_uid']
        
        user = await admin_router.db.get_user(uid)
        end = admin_router.db._calculate_new_end_date(user.get('subscription_end_date') if user else None, days)
        await admin_router.db.set_subscription_status(uid, True, end)
        
        # Запускаем/перезапускаем воркер, если есть сессия и подписка
        if await admin_router.db.check_subscription(uid):
            await admin_router.tm.start_client_task(uid)

        await message.answer(
            f"✅ **Успешно!** Выдано **{days}** дней пользователю `{uid}`. Действует до {end} MSK."
        )
        await state.clear()
        
    except ValueError: 
        await message.answer("❌ Количество дней должно быть положительным числом.")
    except Exception as e:
        logger.error(f"Admin sub error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e.__class__.__name__}. Попробуйте снова.")
        await state.clear()
