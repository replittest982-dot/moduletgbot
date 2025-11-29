import logging
import asyncio
import os
import qrcode
import re
from io import BytesIO

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.fsm.state import StatesGroup, State 
from db import AsyncDatabase
from telethon_manager import TelethonManager
from config import ADMIN_ID, SUPPORT_BOT_USERNAME, TARGET_CHANNEL_URL, QR_TIMEOUT

logger = logging.getLogger(__name__)

user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router") # Оставлен для совместимости с TM, но не используется в main.py

# --- 💡 FSM Состояния (StatesGroup) ---
class TelethonAuth(StatesGroup): 
    phone = State()
    code = State()
    password = State()
    waiting_for_qr = State()
    qr_password = State()

class UserState(StatesGroup): 
    waiting_promo = State()
# --------------------------------------------------------

# --- УТИЛИТА ---
def check_valid_phone(phone: str) -> bool:
    """Проверяет, соответствует ли строка формату телефона +7..."""
    return bool(re.match(r'^\+\d{10,15}$', phone.replace(' ', '')))

# --- MENUS & START ---
def get_main_menu_kb(is_subscribed: bool, is_telethon_active: int, is_worker_running: bool, has_progress: bool, is_admin: bool) -> InlineKeyboardMarkup:
    kb = []
    status_text = "🟢 Активна" if is_subscribed else "🔴 Не активна"
    kb.append([
        InlineKeyboardButton(text=f"Подписка: {status_text}", callback_data="info_sub"), 
        InlineKeyboardButton(text="Справка", callback_data="info_help"),
        InlineKeyboardButton(text="Задать вопрос", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")
    ])
    
    if is_subscribed or is_admin:
        # is_telethon_active: 0-inactive, 1-ready (authorized but not running), 2-running
        if is_telethon_active == 0:
            kb.append([
                InlineKeyboardButton(text="📱 Вход по QR-коду", callback_data="auth_qr"),
                InlineKeyboardButton(text="🔑 Вход по Номеру", callback_data="auth_phone")
            ])
            kb.append([InlineKeyboardButton(text="🎁 Активировать Промокод", callback_data="user_promo")])
        else:
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
        kb.append([
            InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{TARGET_CHANNEL_URL.lstrip('@')}"),
            InlineKeyboardButton(text="Я подписался", callback_data="check_subscription")
        ])

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
        try:
            member = await bot.get_chat_member(TARGET_CHANNEL_URL, uid)
            if member.status not in ['member', 'creator', 'administrator']:
                return await bot.send_message(chat_id, f"⚠️ Доступ ограничен. Подпишитесь на: **{TARGET_CHANNEL_URL}**", 
                                              reply_markup=get_main_menu_kb(False, is_telethon_active, False, False, is_admin))
        except Exception: 
            return await bot.send_message(chat_id, f"⚠️ Не удалось проверить подписку. Убедитесь, что вы подписаны на **{TARGET_CHANNEL_URL}**.", 
                                          reply_markup=get_main_menu_kb(False, is_telethon_active, False, False, is_admin))

    await bot.send_message(chat_id, f"🤖 Привет!\n**Подписка:** {sub_status_text}", 
                           reply_markup=get_main_menu_kb(is_subscribed_bool, is_telethon_active, is_worker_running, has_progress, is_admin))

@user_router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, db: AsyncDatabase, tm: TelethonManager, state: FSMContext):
    await state.clear() 
    await send_start_menu(message.from_user.id, bot, db, tm, is_initial_check=True)

@user_router.callback_query(F.data == "check_subscription")
async def cb_check_sub(callback: CallbackQuery, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await callback.answer()
    await send_start_menu(callback.from_user.id, bot, db, tm, is_initial_check=True)
    try: await callback.message.delete()
    except Exception: pass

@user_router.callback_query(F.data == "info_sub")
async def cb_info_sub(callback: CallbackQuery, db: AsyncDatabase, **kwargs):
    is_subscribed, sub_status_text = await db.get_subscription_status(callback.from_user.id, ADMIN_ID)
    await callback.answer(f"Статус подписки: {sub_status_text}", show_alert=True)
    
# --- AUTH (Phone) ---
@user_router.callback_query(F.data == "auth_phone")
async def cb_auth_phone(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    await state.set_state(TelethonAuth.phone)
    await callback.message.edit_text("📞 Введите номер телефона (`+7...`):")

@user_router.message(TelethonAuth.phone)
async def auth_get_phone(message: Message, state: FSMContext, tm: TelethonManager, **kwargs):
    phone = message.text 
    if not check_valid_phone(phone): return await message.answer("❌ Неверный формат.")
    
    if message.from_user.id in tm.store.active_workers:
        return await message.answer("⚠️ Уже есть активная сессия. Сначала выполните выход.")

    msg = await tm.send_code(message.from_user.id, phone)
    if "❌" in msg: return await message.answer(msg)
    
    await state.set_state(TelethonAuth.code)
    await message.answer(f"{msg} Введите код:")

@user_router.message(TelethonAuth.code)
async def auth_get_code(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg, _ = await tm.sign_in(message.from_user.id, message.text.strip())
    
    if success:
        await state.clear()
        await message.answer(msg)
        await send_start_menu(message.from_user.id, bot, db, tm)
    elif "⚠️" in msg:
        await state.set_state(TelethonAuth.password)
        await message.answer(msg)
    else:
        await message.answer(msg)

@user_router.message(TelethonAuth.password)
async def auth_get_pass(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    await message.answer(msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)

# --- AUTH (QR) ---
@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await callback.answer("Генерация...")
    
    if callback.from_user.id in tm.store.active_workers:
        return await callback.message.answer("⚠️ Уже есть активная сессия. Сначала выполните выход.")
        
    try:
        url, qr_login_object = await tm.start_qr_login(callback.from_user.id) 
    except Exception as e: 
        logger.error(f"QR Login start error: {e}")
        return await callback.message.answer(f"❌ Ошибка: {e}")
    
    bio = BytesIO()
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(bio, 'PNG')
        bio.seek(0)
    except Exception as e:
        logger.error(f"Error generating QR code image: {e}")
        pass 
    
    photo_msg = None
    if bio.getbuffer().nbytes > 0:
        photo_msg = await callback.message.answer_photo(
            photo=bio, 
            caption=f"Отсканируйте код. Действует **{QR_TIMEOUT}с**."
        ) 
    else:
         await callback.message.answer(f"❌ QR-код не был сгенерирован. Используйте вход по номеру. **{QR_TIMEOUT}с**.")

    await state.set_state(TelethonAuth.waiting_for_qr)
    
    data = tm.store.temp_data.get(callback.from_user.id)
    if not data:
        await state.clear()
        if photo_msg: 
            try: await photo_msg.delete() 
            except Exception: pass
        return await callback.message.answer("❌ Сессия QR-авторизации утеряна. Попробуйте снова.")

    success = False
    msg = "❌ Неизвестная ошибка QR-авторизации."
    qr_check_task = None
    
    try:
        qr_check_task = asyncio.create_task(
            tm.check_qr_login(callback.from_user.id, qr_login_object, data['client'])
        )
        success, msg = await asyncio.wait_for(
            qr_check_task, 
            timeout=QR_TIMEOUT + 5
        )
    except asyncio.TimeoutError:
        await state.clear()
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        msg = "❌ Время ожидания QR-кода истекло."
        if qr_check_task: qr_check_task.cancel()
    except Exception as e:
        await state.clear()
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        msg = f"❌ Произошла ошибка при проверке QR: {e}"
        if qr_check_task and not qr_check_task.done():
            qr_check_task.cancel()

    if photo_msg: 
        try: await photo_msg.delete() 
        except Exception: pass

    if success:
        await state.clear()
        await callback.message.answer(msg)
        await send_start_menu(callback.from_user.id, bot, db, tm)
    elif "⚠️" in msg:
        await state.set_state(TelethonAuth.qr_password)
        await callback.message.answer(msg)
    else:
        await state.clear()
        await callback.message.answer(msg)

@user_router.message(TelethonAuth.qr_password)
async def auth_get_qr_pass(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    await message.answer(msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)

# --- WORKER COMMANDS ---
@user_router.callback_query(F.data == "worker_start")
async def cb_work_start(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    # ✅ ИСПРАВЛЕНИЕ: Добавлен await
    await callback.answer("Запуск...")
    if await tm.start_client_task(callback.from_user.id): 
        await callback.message.answer("✅ Worker запущен в фоновом режиме.")
    else: 
        await callback.message.answer("❌ Ошибка запуска. Сессия устарела или недействительна. Попробуйте войти заново.", show_alert=True)
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "worker_stop")
async def cb_work_stop(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await tm.stop_worker(callback.from_user.id, delete_session=False) 
    await callback.answer("Остановлен.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "auth_logout")
async def cb_logout(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, state: FSMContext, **kwargs):
    await state.clear()
    await tm.stop_worker(callback.from_user.id, delete_session=True)
    await callback.answer("Сессия удалена.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

# --- PROMO ---
@user_router.callback_query(F.data == "user_promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.set_state(UserState.waiting_promo)
    await callback.message.edit_text("Введи промокод:")

@user_router.message(UserState.waiting_promo)
async def promo_proc(message: Message, state: FSMContext, db: AsyncDatabase, bot: Bot, tm: TelethonManager, **kwargs):
    success, msg = await db.apply_promo_code(message.from_user.id, message.text.strip())
    await message.answer(msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm) 
    else:
        await message.answer("Попробуйте другой промокод или нажмите /start для выхода в меню.")

# --- ADMIN ---
@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery, **kwargs):
    if callback.from_user.id != ADMIN_ID: return
    text = (
        "👑 **Админ-Панель**\n"
        "Создание промокода: /create_promo\n" 
        "Формат: `КОД ДНИ МАКС_ЮЗЕРОВ`\n"
        "Пример: `/create_promo TEST 30 10`"
    )
    await callback.message.answer(text)

@admin_router.message(Command("create_promo"))
async def cmd_mk_promo(message: Message, db: AsyncDatabase, **kwargs):
    if message.from_user.id != ADMIN_ID: return
    
    parts = message.text.split()
    if len(parts) != 4: 
        return await message.answer("❌ Неверный формат. Ожидался: `/create_promo КОД ДНИ МАКС_ЮЗЕРОВ` (пример: `/create_promo TEST 30 10`)")
    
    try:
        code = parts[1].strip().upper()
        days = int(parts[2])
        max_uses = int(parts[3])
    except ValueError:
        return await message.answer("❌ Дни и Макс_юзеров должны быть числами.")

    if await db.create_promo_code(code, days, max_uses):
        await message.answer(f"✅ Промокод **{code}** создан: {days} дней, {max_uses} использований.")
    else: 
        await message.answer(f"❌ Ошибка: промокод **{code}** уже существует?")
