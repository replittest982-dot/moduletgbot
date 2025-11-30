import asyncio
import logging
import random
import string
import qrcode
from io import BytesIO
from typing import Any

from aiogram import Router, F, Bot
# ✅ ФИКС ИМПОРТА: InputFile заменен на BufferedInputFile
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command, CommandStart 
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

# ✅ ИМПОРТЫ ПРОЕКТА (ПРЕДПОЛАГАЕТСЯ, ЧТО telethon_manager И db СУЩЕСТВУЮТ)
from telethon_manager import TelethonManager
from db import AsyncDatabase

# Настройка роутеров
user_router = Router()
admin_router = Router()
router = Router() 
logger = logging.getLogger(__name__)

# --- FSM СОСТОЯНИЯ ---

class UserState(StatesGroup):
    waiting_promo = State()
    waiting_phone_auth = State()
    waiting_auth_code = State()

class AdminState(StatesGroup):
    waiting_give_sub = State()
    waiting_promo_params = State()

class TelethonAuth(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()
    waiting_for_qr = State()
    qr_password = State()

# --- ФУНКЦИИ КЛАВИАТУР ---

def generate_random_code(length=10) -> str:
    """Генерирует случайный код из букв и цифр."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def get_admin_panel_kb() -> InlineKeyboardMarkup:
    """Клавиатура для Админ-Панели."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="⭐ Выдать подписку", callback_data="admin_give_sub")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="check_subscription")]
    ])

def get_user_menu_kb(is_admin: bool, is_active: bool) -> InlineKeyboardMarkup:
    """Клавиатура для главного меню (как на скриншоте)."""
    row1_text = "Подписка: Активна" if is_active else "Подписка: Неактивна"
    kb = [
        [
            InlineKeyboardButton(text=row1_text, callback_data="user_subscription_status"),
            InlineKeyboardButton(text="Справка", callback_data="user_help"),
            InlineKeyboardButton(text="Задать в...", callback_data="user_ask_question"),
        ],
        [
            InlineKeyboardButton(text="📲 Вход по QR-коду", callback_data="auth_qr"),
            InlineKeyboardButton(text="🔑 Вход по Номеру", callback_data="auth_phone"),
        ],
        [
            InlineKeyboardButton(text="🎁 Активировать Промокод", callback_data="activate_promo"),
        ]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="👑 Админ-Панель", callback_data="admin_panel")])
        
    return InlineKeyboardMarkup(inline_keyboard=kb)


# --- ФУНКЦИЯ ОТПРАВКИ МЕНЮ ---

async def send_start_menu(user_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, 
                          admin_id: int, force_main: bool = False): # ✅ admin_id ДОБАВЛЕН
    """Отправляет главное меню пользователю."""
    
    # ✅ ФИКС: Проверка администратора
    is_admin = user_id == admin_id 
    
    # ⚠️ ТУТ ДОЛЖНА БЫТЬ РЕАЛЬНАЯ ПРОВЕРКА ПОДПИСКИ ИЗ DB
    is_active = True # Замените на await db.check_subscription(user_id)
    
    text = "Привет!\n"
    if is_active:
        text += "Подписка: Активна" # Это только для текста, статус на кнопке.

    keyboard = get_user_menu_kb(is_admin, is_active)

    try:
        await bot.send_message(user_id, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error sending start menu to {user_id}: {e}")


# --- ОБРАБОТЧИК /START ---

@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, **kwargs):
    """
    ✅ ФИКС ИЗВЛЕЧЕНИЯ: db, tm, admin_id берутся напрямую из kwargs.
    Обрабатывает команду /start и вызывает главное меню.
    """
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["db"]  
    tm: TelethonManager = kwargs["tm"]  
    admin_id: int = kwargs["admin_id"] # ✅ ИСПРАВЛЕНО
    
    await state.clear() 
    # ✅ admin_id ПЕРЕДАН В ФУНКЦИЮ МЕНЮ
    await send_start_menu(message.from_user.id, bot, db, tm, admin_id=admin_id)


# --- ОБРАБОТЧИКИ КНОПОК МЕНЮ (ЗАГЛУШКИ) ---

@user_router.callback_query(F.data.in_({"user_subscription_status", "user_help", "user_ask_question", "check_subscription"}))
async def cb_user_info_handlers(callback: CallbackQuery):
    """
    Заглушка для кнопок, чтобы избежать "Update is not handled".
    """
    await callback.answer("⚙️ Эта функция пока не реализована.", show_alert=True)
    
# --- АКТИВАЦИЯ ПРОМОКОДА ---

@user_router.callback_query(F.data == "activate_promo")
async def cb_activate_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_promo)
    await callback.message.answer("🎁 Введите промокод:")
    await callback.answer()

@user_router.message(UserState.waiting_promo)
async def activate_promo_proc(message: Message, state: FSMContext, **kwargs):
    db: AsyncDatabase = kwargs["db"]
    bot: Bot = kwargs["bot"]
    tm: TelethonManager = kwargs["tm"]
    admin_id: int = kwargs["admin_id"]

    # ⚠️ ТУТ ДОЛЖНА БЫТЬ ЛОГИКА ПРОВЕРКИ И АКТИВАЦИИ ПРОМОКОДА
    # Пример:
    # promo_data = await db.get_promo_code(message.text)
    # if promo_data:
    #    await db.use_promo_code(...)
    #    await message.answer(f"✅ Промокод активирован!")
    # else:
    
    await message.answer(f"❌ Промокод `{message.text}` недействителен.")
    await state.clear()
    await send_start_menu(message.from_user.id, bot, db, tm, admin_id=admin_id)

# --- ВХОД ПО НОМЕРУ (FSM) ---

@user_router.callback_query(F.data == "auth_phone")
async def cb_auth_phone_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_phone_auth)
    await callback.message.answer("📞 Введите номер телефона (+7...):")
    await callback.answer()
    
@user_router.message(UserState.waiting_phone_auth)
async def auth_phone_proc(message: Message, state: FSMContext):
    # ⚠️ ТУТ ДОЛЖНА БЫТЬ ЛОГИКА ОТПРАВКИ КОДА ЧЕРЕЗ Telethon
    await state.set_state(UserState.waiting_auth_code)
    await message.answer("🔑 Введите код, который пришел вам в Telegram:")
    
# --- QR АВТОРИЗАЦИЯ ---

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, **kwargs):
    """
    ✅ ФИКС: Сброс зависшей сессии и использование BufferedInputFile.
    Инициация QR-авторизации и генерация QR-кода.
    """
    tm: TelethonManager = kwargs["tm"]  
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["db"]  
    qr_timeout: int = kwargs.get("qr_timeout", 120)  

    await callback.answer("🔄 Генерация QR-кода...")
    
    # ✅ ФИКС: Сброс зависшей сессии
    if callback.from_user.id in getattr(tm.store, 'active_workers', {}):
        try:
            await tm.store.delete_worker(callback.from_user.id)
            logger.warning(f"Forced termination of stuck QR session for {callback.from_user.id}")
            await callback.message.answer("⚠️ Предыдущая сессия была сброшена. Запускаю новую...")
        except Exception:
            pass # Если не удалось сбросить, продолжим.

    url, qr_obj = None, None
    try:
        url, qr_obj = await tm.start_qr_login(callback.from_user.id)
    except Exception as e:
        logger.error(f"Telethon QR Login start error: {e}")
        return await callback.message.answer(f"❌ Ошибка QR-авторизации: {e}")
    
    # QR ГЕНЕРАЦИЯ
    bio = BytesIO()
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, 
                           box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(bio, 'PNG')
    except Exception as e:
        logger.error(f"Error generating QR code image: {e}")
        return await callback.message.answer("❌ Не удалось сгенерировать QR-код.")
    
    # ✅ ФИКС: Использование BufferedInputFile
    await callback.message.answer_photo(
        photo=BufferedInputFile(bio.getvalue(), filename="qr.png"),
        caption=f"📱 Отсканируйте QR-код!\n⏰ Действует **{qr_timeout}с**"
    )
    
    await state.set_state(TelethonAuth.waiting_for_qr)
    logger.info(f"QR started for {callback.from_user.id}")


# --- АДМИН-ПАНЕЛЬ ---

@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery, **kwargs):
    """Отображает Админ-Панель."""
    admin_id: int = kwargs["admin_id"]  # ✅ ИСПРАВЛЕНО
    if callback.from_user.id != admin_id:
        return await callback.answer("❌ Доступ запрещён", show_alert=True)
    
    text = """👑 Админ-Панель
Выберите действие:"""
    
    await callback.message.edit_text(text, reply_markup=get_admin_panel_kb())
    await callback.answer()

@admin_router.callback_query(F.data == "admin_give_sub")
async def cb_admin_give_sub_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    admin_id: int = kwargs["admin_id"]  
    if callback.from_user.id != admin_id: return
    
    await state.set_state(AdminState.waiting_give_sub)
    await callback.message.edit_text("⭐ Введите ID/USERNAME и ДНИ.\nПример: `1234567 30`")
    await callback.answer()

@admin_router.message(AdminState.waiting_give_sub)
async def admin_give_sub_proc(message: Message, state: FSMContext, **kwargs):
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["db"]  
    tm: TelethonManager = kwargs["tm"]  
    admin_id: int = kwargs["admin_id"]  
    
    if message.from_user.id != admin_id: return
    
    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("❌ Неверный формат. Ожидается `ID/USERNAME ДНИ`.")
    
    identifier, days_str = parts
    try:
        days = int(days_str)
        if days <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Количество дней должно быть положительным числом.")
    
    user_id = None
    try:
        if identifier.startswith('@'):
            user_info = await bot.get_chat(identifier)
            user_id = user_info.id
        else:
            user_id = int(identifier)
    except Exception:
        return await message.answer(f"❌ Пользователь `{identifier}` не найден или ID неверный.")
    
    try:
        await db.add_subscription(user_id, days, is_admin_sub=True)
        await state.clear()
        await message.answer(f"✅ Пользователю с ID **{user_id}** выдана подписка на **{days}** дней.")
        
        try:
            await bot.send_message(user_id, f"🌟 **Вам выдана подписка на {days} дней!**")
        except TelegramForbiddenError:
            pass
            
    except Exception as e:
        await message.answer(f"❌ Ошибка при выдаче подписки: {e}")
        
    await send_start_menu(message.from_user.id, bot, db, tm, admin_id=admin_id)

@admin_router.callback_query(F.data == "admin_create_promo")
async def cb_admin_create_promo_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    admin_id: int = kwargs["admin_id"]  
    if callback.from_user.id != admin_id: return
    
    await state.set_state(AdminState.waiting_promo_params)
    await callback.message.edit_text("🎁 Введите `ДНИ МАКС_ЮЗЕРОВ`.\nПример: `30 10`")
    await callback.answer()

@admin_router.message(AdminState.waiting_promo_params)
async def admin_create_promo_proc(message: Message, state: FSMContext, **kwargs):
    db: AsyncDatabase = kwargs["db"]  
    bot: Bot = kwargs["bot"]
    tm: TelethonManager = kwargs["tm"]  
    admin_id: int = kwargs["admin_id"]  
    
    if message.from_user.id != admin_id: return
    
    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("❌ Неверный формат. Ожидается `ДНИ МАКС_ЮЗЕРОВ`.")
    
    try:
        days, max_uses = int(parts[0]), int(parts[1])
        if days <= 0 or max_uses < 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Дни должны быть > 0, Макс. юзеров >= 0.")
    
    code = generate_random_code(10)
    if await db.create_promo_code(code, days, max_uses):
        await message.answer(f"✅ Промокод: `{code}`\nСрок: {days} дней\nЛимит: {max_uses} юзеров")
    else:
        await message.answer(f"❌ Ошибка при создании промокода. Код `{code}` уже существует.")
    
    await state.clear()
    await send_start_menu(message.from_user.id, bot, db, tm, admin_id=admin_id)


# --- ЕДИНЫЙ ERROR HANDLER ---

@router.errors()
async def errors_handler(event: ErrorEvent):
    """Обработчик всех исключений."""
    exc = event.exception
    
    user_id = getattr(getattr(event.update, 'effective_user', None), 'id', 'неизвестно')
    
    if isinstance(exc, TelegramForbiddenError):
        logger.warning(f"🚫 Forbidden {user_id}: {exc}")
    elif isinstance(exc, TelegramBadRequest):
        logger.info(f"⚠️ BadRequest {user_id}: {exc}")
    elif isinstance(exc, TelegramAPIError):
        logger.error(f"🌐 API {user_id}: {exc}")
    elif "sqlite" in str(exc).lower():
        logger.error(f"🗄️ DB {user_id}: {exc}")
    else:
        logger.error(f"💥 UNKNOWN ERROR {user_id}: {exc}", exc_info=True)
        
    return True
