import logging
import asyncio
import os
import qrcode
from io import BytesIO

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto

# ВАЖНО: Убедитесь, что эти импорты корректны для вашей структуры проекта
from config import ADMIN_ID, SUPPORT_BOT_USERNAME, TARGET_CHANNEL_URL, QR_TIMEOUT
# Импортируйте ваши классы из других файлов:
# from utils import TelethonAuth, UserState, GlobalStorage, check_valid_phone
# from db import AsyncDatabase
# from telethon_manager import TelethonManager

# --- Заглушки для типов (УДАЛИТЕ И ЗАМЕНИТЕ НА РЕАЛЬНЫЕ ИМПОРТЫ!) ---
class AsyncDatabase: pass
class TelethonManager: pass
class TelethonAuth: # 💡 Пример состояний для FSM
    PHONE = 'TelethonAuth:phone'
    CODE = 'TelethonAuth:code'
    PASSWORD = 'TelethonAuth:password'
    WAITING_FOR_QR_LOGIN = 'TelethonAuth:waiting_for_qr'
    QR_PASSWORD = 'TelethonAuth:qr_password'
class UserState: 
    WAITING_FOR_PROMO_CODE = 'UserState:waiting_promo'
class GlobalStorage: pass
def check_valid_phone(phone): # Упрощенная заглушка проверки телефона
    return len(phone) > 10 and phone.startswith('+')
# --------------------------------------------------------

logger = logging.getLogger(__name__)

user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router")

# --- MENUS ---
def get_main_menu_kb(is_subscribed: bool, is_telethon_active: bool, is_worker_running: bool, has_progress: bool, is_admin: bool) -> InlineKeyboardMarkup:
    kb = []
    status_text = "🟢 Активна" if is_subscribed else "🔴 Не активна"
    kb.append([
        InlineKeyboardButton(text=f"Подписка: {status_text}", callback_data="info_sub"),
        InlineKeyboardButton(text="Справка", callback_data="info_help"),
        InlineKeyboardButton(text="Задать вопрос", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")
    ])
    
    if is_subscribed or is_admin:
        
        if not is_telethon_active:
            # Кнопки входа
            kb.append([
                InlineKeyboardButton(text="📱 Вход по QR-коду", callback_data="auth_qr"),
                InlineKeyboardButton(text="🔑 Вход по Номеру", callback_data="auth_phone")
            ])
            kb.append([InlineKeyboardButton(text="🎁 Активировать Промокод", callback_data="user_promo")])
        else:
            # Если есть активный Telethon аккаунт:
            worker_row = []
            if is_worker_running:
                worker_row.append(InlineKeyboardButton(text="Worker Активен", callback_data="info_worker"))
                worker_row.append(InlineKeyboardButton(text="🛑 Остановить", callback_data="worker_stop"))
                if has_progress:
                    worker_row.append(InlineKeyboardButton(text="Прогресс", callback_data="worker_status"))
            else:
                worker_row.append(InlineKeyboardButton(text="Запустить Worker", callback_data="worker_start"))
                worker_row.append(InlineKeyboardButton(text="Worker Остановлен", callback_data="info_worker"))
            
            kb.append(worker_row)
            kb.append([InlineKeyboardButton(text="🎁 Промокод", callback_data="user_promo"), InlineKeyboardButton(text="❌ Выход", callback_data="auth_logout")])
        
        if is_admin: kb.append([InlineKeyboardButton(text="👑 Админ-Панель", callback_data="admin_panel")])
    
    else:
        kb.append([InlineKeyboardButton(text="Доступ к боту закрыт (Подписка)", callback_data="info_sub")])

    return InlineKeyboardMarkup(inline_keyboard=kb)

async def send_start_menu(chat_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, is_initial_check: bool = False):
    uid = chat_id
    is_subscribed_bool, sub_status_text = await db.get_subscription_status(uid, ADMIN_ID)
    user_data = await db.get_user(uid) 
    is_telethon_active = user_data.get('telethon_active', 0)
    is_worker_running = uid in tm.store.active_workers
    has_progress = uid in tm.store.process_progress
    is_admin = uid == ADMIN_ID
    
    if is_initial_check and not is_subscribed_bool and not is_admin:
        # Проверка подписки
        try:
            member = await bot.get_chat_member(TARGET_CHANNEL_URL, uid)
            if member.status not in ['member', 'creator', 'administrator']:
                return await bot.send_message(chat_id, f"⚠️ Подпишитесь на: **{TARGET_CHANNEL_URL}**", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{TARGET_CHANNEL_URL.lstrip('@')}")], [InlineKeyboardButton(text="Я подписался", callback_data="check_subscription")]]))
        except Exception: 
            pass

    # Note: parse_mode='Markdown' используется по умолчанию благодаря исправлению в main.py
    await bot.send_message(chat_id, f"🤖 Привет!\n**Подписка:** {sub_status_text}", reply_markup=get_main_menu_kb(is_subscribed_bool, is_telethon_active, is_worker_running, has_progress, is_admin))

@user_router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await send_start_menu(message.from_user.id, bot, db, tm, is_initial_check=True)

@user_router.callback_query(F.data == "check_subscription")
async def cb_check_sub(callback: CallbackQuery, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await callback.answer()
    await send_start_menu(callback.from_user.id, bot, db, tm, is_initial_check=True)
    await callback.message.delete()

# --- Auth ---
@user_router.callback_query(F.data == "auth_phone")
async def cb_auth_phone(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    await state.set_state(TelethonAuth.PHONE)
    await callback.message.edit_text("📞 Введите номер телефона (`+7...`):")

@user_router.message(StateFilter(TelethonAuth.PHONE))
async def auth_get_phone(message: Message, state: FSMContext, tm: TelethonManager, **kwargs):
    phone = check_valid_phone(message.text)
    if not phone: return await message.answer("❌ Неверный формат.")
    
    if message.from_user.id in tm.store.active_workers:
        return await message.answer("⚠️ Уже есть активная сессия. Сначала выполните выход.")

    msg = await tm.send_code(message.from_user.id, phone)
    if "❌" in msg: return await message.answer(msg)
    
    await state.set_state(TelethonAuth.CODE)
    await message.answer(f"{msg} Введите код:")

@user_router.message(StateFilter(TelethonAuth.CODE))
async def auth_get_code(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg, _ = await tm.sign_in(message.from_user.id, message.text.strip())
    
    if success:
        await state.clear()
        await message.answer(msg)
        await send_start_menu(message.from_user.id, bot, db, tm)
    elif "⚠️" in msg:
        await state.set_state(TelethonAuth.PASSWORD)
        await message.answer(msg)
    else:
        await message.answer(msg)

@user_router.message(StateFilter(TelethonAuth.PASSWORD))
async def auth_get_pass(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    await message.answer(msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await callback.answer("Генерация...")
    
    if callback.from_user.id in tm.store.active_workers:
        return await callback.message.answer("⚠️ Уже есть активная сессия. Сначала выполните выход.")
        
    try:
        # 💡 ИСПРАВЛЕНИЕ: Получаем только URL, избегая .image
        url = await tm.start_qr_login(callback.from_user.id) 
    except Exception as e: 
        logger.error(f"QR Login start error: {e}")
        return await callback.message.answer(f"❌ Ошибка: {e}")
    
    bio = BytesIO()
    try:
        # Генерация QR-кода из URL
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(bio, 'PNG')
        bio.seek(0)
    except Exception as e:
        logger.error(f"Error generating QR code image: {e}")
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        return await callback.message.answer("❌ Ошибка при создании QR-кода. Попробуйте войти по номеру.")
    
    # Отправляем QR-код
    photo_msg = await callback.message.answer_photo(bio, caption=f"Отсканируйте код. Действует **{QR_TIMEOUT}с**.")
    await state.set_state(TelethonAuth.WAITING_FOR_QR_LOGIN)
    
    data = tm.store.temp_data.get(callback.from_user.id)
    if not data:
        await state.clear()
        try: await photo_msg.delete() 
        except Exception: pass
        return await callback.message.answer("❌ Сессия QR-авторизации утеряна. Попробуйте снова.")

    # Ожидаем завершения QR-авторизации с таймаутом
    try:
        success, msg = await asyncio.wait_for(
            tm.check_qr_login(callback.from_user.id, data['qr_login_data'], data['client']), 
            timeout=QR_TIMEOUT + 5
        )
    except asyncio.TimeoutError:
        await state.clear()
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        msg = "❌ Время ожидания QR-кода истекло."
    except Exception as e:
        await state.clear()
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        msg = f"❌ Произошла ошибка при проверке QR: {e}"

    # Удаляем сообщение с QR-кодом
    try: await photo_msg.delete() 
    except Exception: pass

    if success:
        await state.clear()
        await callback.message.answer(msg)
        await send_start_menu(callback.from_user.id, bot, db, tm)
    elif "⚠️" in msg:
        await state.set_state(TelethonAuth.QR_PASSWORD)
        await callback.message.answer(msg)
    else:
        await state.clear()
        await callback.message.answer(msg)

@user_router.message(StateFilter(TelethonAuth.QR_PASSWORD))
async def auth_get_qr_pass(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    await message.answer(msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)


# --- Worker и Logout ---
@user_router.callback_query(F.data == "worker_start")
async def cb_work_start(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    if await tm.start_client_task(callback.from_user.id): 
        await callback.answer("Запущен.")
    else: 
        await callback.answer("Ошибка запуска. Попробуйте войти заново.", show_alert=True)
        await db.update_user(callback.from_user.id, telethon_active=0)
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "worker_stop")
async def cb_work_stop(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await tm.stop_worker(callback.from_user.id)
    await callback.answer("Остановлен.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "auth_logout")
async def cb_logout(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, state: FSMContext, **kwargs):
    await state.clear()
    await tm.stop_worker(callback.from_user.id, delete_session=True)
    await callback.answer("Сессия удалена.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

# --- Promo и Admin (сокращены для экономии места) ---
# ... (Остальная логика Promo и Admin)
