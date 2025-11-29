import asyncio
import logging
import os
import re
import random
import string
import io
from datetime import datetime
from typing import Dict, Optional, List, Union, Any, Callable, Awaitable
from functools import wraps
from contextlib import suppress
import traceback
import csv
import textwrap

# --- AIOGRAM ---
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, Update, Bot, BufferedInputFile
from aiogram.filters import Command, StateFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.dispatcher.middlewares.base import BaseMiddleware 

# --- TELETHON ---
from telethon.tl.types import User
from telethon.errors import SessionPasswordNeededError, PasswordHashInvalidError, PhoneNumberInvalidError

# --- LOCAL IMPORTS ---
from config import ADMIN_ID, SUPPORT_BOT_USERNAME, RATE_LIMIT_TIME, TARGET_CHANNEL_URL
from telethon_manager import TelethonAuth, SESSION_DIR

logger = logging.getLogger(__name__)

# =========================================================================
# I. FSM СОСТОЯНИЯ И РОУТЕРЫ
# =========================================================================

user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router") # Новый роутер для команд Drop-системы

# --- FSM States ---
class PromoStates(StatesGroup):
    waiting_for_code = State()

class AdminStates(StatesGroup):
    waiting_for_user_id_for_sub = State() 
    waiting_for_sub_days = State()
    waiting_for_promo_days = State()
    waiting_for_promo_uses = State()

class DropStates(StatesGroup):
    waiting_for_pc_name = State()

# =========================================================================
# II. MIDDLEWARE И UTILS
# =========================================================================

# (RateLimitMiddleware, generate_promo_code, update_menu_after_action - ОСТАВЛЕНЫ, как в предыдущем ответе)
def get_user_id_from_update(update: Union[Update, Message, CallbackQuery]) -> Optional[int]:
    # ... (как в telethon_manager)
    if isinstance(update, Update):
        if update.from_user: return update.from_user.id
        if update.message: return update.message.from_user.id
        if update.callback_query: return update.callback_query.from_user.id
    elif isinstance(update, (Message, CallbackQuery)):
        return update.from_user.id
    return None

class RateLimitMiddleware(BaseMiddleware):
    # ... (логика RateLimitMiddleware)
    def __init__(self, store, limit: float = RATE_LIMIT_TIME):
        self.limit = limit
        self.lock = asyncio.Lock()
        self.store = store
        self.db = user_router.db if hasattr(user_router, 'db') else None
        super().__init__()

    async def __call__(self, handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]], event: Update, data: Dict[str, Any]) -> Any:
        user_id = get_user_id_from_update(event)
        if not user_id: return await handler(event, data)
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
    # ... (логика обновления меню)
    await state.clear()
    if not edit_message:
        try: edit_message = await bot_instance.send_message(user_id, "🔄 Обновление меню...")
        except: return

    fake_call = types.CallbackQuery( 
        id=f'fake_update_{callback_data}_{random.randint(1000, 9999)}', 
        from_user=types.User(id=user_id, is_bot=False, first_name="User"), 
        message=edit_message, data=callback_data 
    )
    
    if callback_data == "worker_menu": await account_menu(fake_call, state)
    elif callback_data == "profile_menu": await profile_menu(fake_call, state)
    elif callback_data == "start_menu": await cmd_start_from_update(fake_call, state)
    elif callback_data == "admin_stats": await admin_main_menu(fake_call, state)
    else: await profile_menu(fake_call, state)


def format_report_txt(report_data: Dict[str, Any]) -> str:
    """Форматирует данные отчета .чекгруппу в красивый TXT."""
    peer_name = report_data['peer_name']
    user_count = report_data['count']
    scanned_messages = report_data['scanned_messages']
    users = report_data['report_data'].values()

    output = io.StringIO()
    
    # Заголовок
    output.write(f"📊 ОТЧЕТ: {peer_name} ({user_count} пользователей)\n")
    output.write(f"📄 Просканировано: {scanned_messages} сообщений\n\n")

    # Форматирование таблицы
    output.write("Имя | @username | ID\n")
    output.write("-" * 50 + "\n")
    
    for user in users:
        name = user.get('first_name', 'No Name')
        username = user.get('username', 'No Username')
        user_id = user['id']
        
        # Обрезаем, чтобы TXT был читаемым (15 символов)
        name_str = textwrap.shorten(name, width=15, placeholder="...")
        username_str = textwrap.shorten(username, width=15, placeholder="...")
        
        output.write(f"{name_str.ljust(15)} | {('@' + username_str).ljust(17)} | {user_id}\n")

    return output.getvalue()


# =========================================================================
# III. KEYBOARDS
# =========================================================================

def get_main_menu_keyboard(user_id: int, is_subscribed: bool, session_exists: bool) -> InlineKeyboardMarkup:
    # ... (логика главного меню)
    builder = InlineKeyboardBuilder()
    
    if is_subscribed:
        text = "⚙️ Управление аккаунтом" if session_exists else "🚪 Авторизация"
        builder.row(InlineKeyboardButton(text=text, callback_data="worker_menu"))
    else:
        builder.row(InlineKeyboardButton(text="🔑 Активировать Промокод", callback_data="enter_promo"))
        
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile_menu"))
    builder.row(InlineKeyboardButton(text="❓ Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}"))
    
    if user_id == ADMIN_ID:
        builder.row(InlineKeyboardButton(text="📊 Админ-Панель", callback_data="admin_stats"))
        
    return builder.as_markup()

def get_account_menu_keyboard(is_worker_active: bool, session_exists: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    if is_worker_active:
        builder.row(InlineKeyboardButton(text="🛑 Остановить Аккаунт", callback_data="stop_worker"))
    elif session_exists: 
        builder.row(InlineKeyboardButton(text="▶️ Запустить Worker", callback_data="start_worker"))
        
    builder.row(InlineKeyboardButton(text="🚪 Сменить аккаунт/Авторизация", callback_data="auth_method_menu"))
    if session_exists:
        builder.row(InlineKeyboardButton(text="🗑️ Выход (Удалить сессию)", callback_data="delete_session"))
        
    builder.row(InlineKeyboardButton(text="🔙 Профиль", callback_data="profile_menu"))
    return builder.as_markup()

def get_auth_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📲 Вход по Номеру", callback_data="auth_by_phone"))
    builder.row(InlineKeyboardButton(text="📷 Вход по QR", callback_data="auth_by_qr"))
    builder.row(InlineKeyboardButton(text="🔙 Управление аккаунтом", callback_data="worker_menu"))
    return builder.as_markup()
    
def get_check_group_menu_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # Сохраняем chat_id в callback_data для передачи в следующую функцию
    builder.row(InlineKeyboardButton("📊 100-500 пользователей", callback_data=f"checkgroup_{chat_id}_100_500"))
    builder.row(InlineKeyboardButton("📊 500-2000 пользователей", callback_data=f"checkgroup_{chat_id}_500_2000"))
    builder.row(InlineKeyboardButton("📊 2000-10000 пользователей", callback_data=f"checkgroup_{chat_id}_2000_10000"))
    builder.row(InlineKeyboardButton("📊 Полный скан (без лимита)", callback_data=f"checkgroup_{chat_id}_0_1000000"))
    builder.row(InlineKeyboardButton("🔙 Отмена", callback_data="worker_menu"))
    return builder.as_markup()

def get_report_keyboard(task_key: str, user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📄 Файлом .txt", callback_data=f"getreport_{task_key}"),
        InlineKeyboardButton(text="🗑️ Удалить Отчёт", callback_data=f"deletereport_{task_key}")
    )
    return builder.as_markup()

# =========================================================================
# IV. HANDLERS (USER)
# =========================================================================

# --- START, PROFILE, PROMO (логика как ранее, только с новыми клавиатурами) ---

@user_router.message(Command('start'))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await user_router.db.get_user(user_id) 
    is_subscribed = await user_router.db.check_subscription(user_id)
    session_exists = await asyncio.to_thread(os.path.exists, os.path.join(SESSION_DIR, f'session_{user_id}.session'))

    await message.answer("👋 Добро пожаловать в систему STATPRO.", reply_markup=get_main_menu_keyboard(user_id, is_subscribed, session_exists))

@user_router.callback_query(F.data == "profile_menu")
async def profile_menu(call: types.CallbackQuery, state: FSMContext):
    # ... (логика профиля)
    user_id = call.from_user.id
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
        f"✅ **Подписка:** {'🟢 Активна' if is_subscribed else '🔴 Не активна'}\n"
        f"🗓️ **До:** `{end_date_info}`\n"
        f"🔗 **Статус авторизации:** {auth_status}\n"
        f"🚀 **Worker запущен:** {active_status}"
    )
    
    await call.message.edit_text(text, reply_markup=get_main_menu_keyboard(user_id, is_subscribed, session_exists))

@user_router.callback_query(F.data == "worker_menu")
async def account_menu(call: types.CallbackQuery, state: FSMContext): 
    # ... (логика управления аккаунтом)
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

    status_text = "✅ **Worker запущен**." if is_worker_active else "❌ **Worker остановлен**."
    
    text = f"⚙️ **Управление аккаунтом**\n\n{status_text}"
    
    await message_to_edit.edit_text(text, reply_markup=get_account_menu_keyboard(is_worker_active, session_exists))

# --- WORKER ACTIONS ---

@user_router.callback_query(F.data == "delete_session")
async def delete_session_handler(call: types.CallbackQuery, state: FSMContext):
    await call.answer("Удаление сессии...", show_alert=False)
    await user_router.tm.delete_session_file(call.from_user.id)
    await user_router.db.set_telethon_status(call.from_user.id, False)
    await call.message.edit_text("🗑️ **Сессия удалена.** Теперь вы можете привязать новый аккаунт.", reply_markup=get_auth_method_keyboard())

# --- AUTH FLOW ---
@user_router.callback_query(F.data == "auth_method_menu")
async def auth_method_menu(call: types.CallbackQuery, state: FSMContext):
    await user_router.tm.stop_worker(call.from_user.id) 
    await state.clear()
    await call.message.edit_text("🚪 **Выберите способ авторизации**:", reply_markup=get_auth_method_keyboard())
    await call.answer()
    
# QR
@user_router.callback_query(F.data == "auth_by_qr")
async def auth_by_qr_step1(call: types.CallbackQuery, state: FSMContext):
    await call.answer("Запуск QR-сессии...", show_alert=False)
    await user_router.tm.start_qr_login(call.from_user.id, state, call.message)

@user_router.message(TelethonAuth.QR_WAIT, F.text)
async def auth_by_qr_password(message: types.Message, state: FSMContext):
    # Этот хендлер ловит только пароль (2FA), если он понадобился после сканирования
    user_id = message.from_user.id
    password = message.text.strip()
    data = await state.get_data()
    client = user_router.tm.store.temp_auth_clients.get(user_id)
    original_message = data.get('original_message')
    
    try:
        if not client.is_connected(): await client.connect()
        user_info = await client.sign_in(password=password)
        await user_router.tm._finalize_auth(user_id, original_message, state, user_info)
    except PasswordHashInvalidError:
        await message.answer("❌ Неверный пароль.")
    except Exception as e:
        logger.error(f"Sign-in Password Error (QR 2FA): {e}")
        await user_router.tm.store.temp_auth_clients.pop(user_id, None)
        await state.clear()
        if client: await client.disconnect()
        await message.answer("❌ Ошибка входа.")


# --- CHECK GROUP REPORTS ---

@user_router.callback_query(F.data.startswith("checkgroup_"))
async def start_checkgroup(call: CallbackQuery, state: FSMContext):
    # checkgroup_<chat_id>_<min>_<max>
    parts = call.data.split("_")
    chat_id = int(parts[1])
    min_users = int(parts[2])
    max_users = int(parts[3])
    user_id = call.from_user.id

    # 1. Стопаем старую задачу, если есть
    await user_router.tm.stop_task(user_id, f"checkgroup_{user_id}") 

    await call.message.edit_text(f"🔄 **Сканирование начато.** Пожалуйста, ожидайте отчета в ЛС. (Цель: {min_users}-{max_users})")
    
    # 2. Запуск задачи
    task_key = f"checkgroup_{user_id}" 
    client = user_router.tm.store.active_workers.get(user_id)
    
    if not client:
        await call.answer("❌ Worker не запущен. Запустите его сначала.", show_alert=True)
        return await update_menu_after_action(user_id, state, call.bot, callback_data="worker_menu", edit_message=call.message)

    try:
        task = asyncio.create_task(user_router.tm.check_group_task(client, chat_id, min_users, max_users, user_id, task_key, call.message))
        async with user_router.store.lock:
            user_router.store.worker_tasks.setdefault(user_id, {})[task_key] = task
            user_router.store.process_progress[user_id] = {
                'type': 'checkgroup', 
                'task_key': task_key, 
                'min_users': min_users, 
                'max_users': max_users,
                'chat_id': chat_id,
                'processed_messages': 0
            }
        await call.answer("Сканирование запущено!", show_alert=False)
    except Exception as e:
        await call.answer(f"❌ Не удалось запустить сканирование: {e.__class__.__name__}", show_alert=True)

@user_router.callback_query(F.data.startswith("getreport_"))
async def get_report_file(call: CallbackQuery):
    await call.answer("Подготовка файла...", show_alert=False)
    user_id = call.from_user.id
    task_key = call.data.split("_")[-1]
    
    progress = user_router.tm.store.process_progress.get(user_id)
    
    # Проверяем, что это тот же отчет (task_key)
    if not progress or progress.get('task_key') != task_key:
        # Пробуем найти в кэше завершенных, если такой логики нет, то:
        return await call.message.edit_text("❌ Отчет не найден или срок хранения истек.")

    report_data = progress.get('report_data')
    if not report_data:
        return await call.message.edit_text("❌ Отчет пуст.")
        
    txt_content = format_report_txt(progress)
    
    # Отправка файла
    file = BufferedInputFile(txt_content.encode('utf-8'), filename=f"report_{progress['peer_name']}_{progress['count']}.txt")
    await call.bot.send_document(user_id, file, caption=f"📄 Отчет по группе: **{progress['peer_name']}**")
    await call.message.answer("✅ Файл отправлен.")

@user_router.callback_query(F.data.startswith("deletereport_"))
async def delete_report(call: CallbackQuery):
    await call.answer("Отчет удален.", show_alert=False)
    user_id = call.from_user.id
    task_key = call.data.split("_")[-1]
    
    async with user_router.store.lock:
        # Удаляем данные прогресса
        if user_router.store.process_progress.get(user_id, {}).get('task_key') == task_key:
             user_router.store.process_progress.pop(user_id, None)

    await call.message.delete()
    await call.message.answer("🗑️ Отчет удален. Вернитесь в меню.", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton("🔙 Управление Worker", callback_data="worker_menu")).as_markup())


# =========================================================================
# V. HANDLERS (DROP SYSTEM)
# =========================================================================

# --- DROP COMMANDS (ИСПОЛЬЗУЮТСЯ В ЛЮБОМ ЧАТЕ/ТОПИКЕ) ---

@drop_router.message(Command('пкстарт', prefix="."))
async def start_pc_session_handler(message: types.Message, state: FSMContext):
    pc_name = message.text.split()[1].strip().upper() if len(message.text.split()) > 1 else None
    if not pc_name:
        return await message.reply("❌ **Формат:** `.пкстарт ПК1`")
        
    chat_id = message.chat.id
    thread_id = message.message_thread_id 

    # Проверка, нет ли уже активной сессии в этом топике/чате
    current_session = await drop_router.db.get_current_pc_session(chat_id, thread_id)
    if current_session and current_session['status'] not in ['завершен', 'slet', 'error']:
         return await message.reply(f"❌ **Сессия для {current_session['pc_name']} уже активна** (`{current_session['status']}`). Используйте `/report` или `/slet` для завершения.")

    # Создание новой сессии
    drop_id = await drop_router.db.start_pc_session(pc_name, chat_id, thread_id)
    
    await message.reply(f"✅ **ПК Сессия {pc_name} запущена.** Статус: `дайте номер`.\n**ID Сессии:** `{drop_id}`. Жду `/numb`.")

@drop_router.message(Command('numb', prefix="/"))
async def wait_for_number_handler(message: types.Message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id 
    
    session = await drop_router.db.get_current_pc_session(chat_id, thread_id)
    if not session or session['status'] not in ['дайте номер', 'error', 'slet', 'замена', 'повтор', 'в работе']:
        return await message.reply("❌ **Нет активной сессии ПК** в этом чате/топике. Начните с `.пкстарт`.")
        
    await message.reply("✅ **Статус обновлен:** `дайте номер`.")
    await drop_router.db.update_drop_status(session['drop_id'], "дайте номер")

@drop_router.message(Command('num', prefix="/"))
async def set_number_handler(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2: return await message.reply("❌ **Формат:** `/num +79001234567`")
    phone = parts[1].strip()
    
    session = await drop_router.db.get_current_pc_session(message.chat.id, message.message_thread_id)
    if not session:
        return await message.reply("❌ **Нет активной сессии ПК**.")
        
    # Обновляем статус на "в работе" и устанавливаем номер
    await drop_router.db.update_drop_status(session['drop_id'], "в работе", phone=phone)
    await message.reply(f"✅ **Номер `{phone}` принят.** Статус: `в работе`.")

@drop_router.message(Command('vstal', 'error', 'slet', 'povt', prefix="/"))
async def update_simple_status_handler(message: types.Message):
    cmd_to_status = {'/vstal': 'в работе', '/error': 'error', '/slet': 'slet', '/povt': 'повтор'}
    status = cmd_to_status.get(message.text.split()[0].lower())
    
    session = await drop_router.db.get_current_pc_session(message.chat.id, message.message_thread_id)
    if not session:
        return await message.reply("❌ **Нет активной сессии ПК**.")

    updated_session = await drop_router.db.update_drop_status(session['drop_id'], status)
    
    if status == 'в работе':
        # Пересчитываем время и сообщаем о простое
        times = drop_router.db.calculate_times(updated_session)
        await message.reply(f"✅ **Статус: `в работе`.**\n⏳ Простой за сессию: `{times['prosto_time']}`")
    else:
        await message.reply(f"✅ **Статус обновлен:** `{status}`.")

@drop_router.message(Command('zm', prefix="/"))
async def zamena_handler(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2: return await message.reply("❌ **Формат:** `/zm +79001234567`")
    new_phone = parts[1].strip()
    
    session = await drop_router.db.get_current_pc_session(message.chat.id, message.message_thread_id)
    if not session:
        return await message.reply("❌ **Нет активной сессии ПК**.")
        
    # Обновляем статус на "замена" и устанавливаем новый номер
    await drop_router.db.update_drop_status(session['drop_id'], "замена", phone=new_phone)
    await message.reply(f"✅ **Номер заменен на `{new_phone}`.** Статус: `замена`.")

@drop_router.message(Command('report', prefix="/"))
async def report_handler(message: types.Message):
    session = await drop_router.db.get_current_pc_session(message.chat.id, message.message_thread_id)
    if not session:
        return await message.reply("❌ **Нет активной сессии ПК**.")
        
    # Закрываем сессию, если она не закрыта
    if session['status'] not in ['завершен', 'slet', 'error', 'замена']:
         session = await drop_router.db.update_drop_status(session['drop_id'], "завершен")

    times = drop_router.db.calculate_times(session)
    
    start_time_msk = drop_router.db.to_msk_aware(session['start_time']).strftime('%H:%M:%S')

    report_text = (
        f"📊 **ОТЧЁТ ПО {session['pc_name']}** (ID: `{session['drop_id']}`)\n"
        f"📱 **Номер:** `{session['phone'] or 'Не указан'}`\n"
        f"⏰ **Начало:** `{start_time_msk}`\n"
        f"🌟 **Финальный статус:** `{session['status']}`\n"
        f"----------------------------------------\n"
        f"⏳ **Общее время:** `{times['total_time']}`\n"
        f"💼 **Время работы:** `{times['work_time']}`\n"
        f"☕ **Время простоя:** `{times['prosto_time']}`"
    )
    
    await message.reply(report_text)
