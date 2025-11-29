import asyncio
import logging
import random
import string
import qrcode
from io import BytesIO
from typing import Any

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.filters import Command, CommandStart # ✅ ДОБАВЛЕН ИМПОРТ CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

# ✅ ИМПОРТЫ ПРОЕКТА (константы будут взяты из kwargs)
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

class AdminState(StatesGroup):
    waiting_give_sub = State()
    waiting_promo_params = State()

class TelethonAuth(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()
    waiting_for_qr = State()
    qr_password = State()

# --- ФУНКЦИИ ---

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

async def send_start_menu(user_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, force_main: bool = False):
    """ЗАМЕНИ НА СВОЮ ЛОГИКУ МЕНЮ"""
    await bot.send_message(user_id, "✅ Главное меню (замени эту функцию!)")

# --- ОБРАБОТЧИК /START (КРИТИЧЕСКИ ВАЖНЫЙ ФИКС) ---

@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, **kwargs):
    """Обрабатывает команду /start и вызывает главное меню."""
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["dp"]["db"]
    tm: TelethonManager = kwargs["dp"]["tm"]
    
    # Очищаем состояние
    await state.clear() 
    
    # Вызываем функцию для отображения меню
    await send_start_menu(message.from_user.id, bot, db, tm)


# --- АДМИН-ПАНЕЛЬ ---

@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery, **kwargs):
    """Отображает Админ-Панель."""
    admin_id: int = kwargs["dp"]["admin_id"]
    if callback.from_user.id != admin_id:
        return await callback.answer("❌ Доступ запрещён", show_alert=True)
    
    text = """👑 Админ-Панель
Выберите действие:"""
    
    await callback.message.edit_text(text, reply_markup=get_admin_panel_kb())
    await callback.answer()

@admin_router.callback_query(F.data == "admin_give_sub")
async def cb_admin_give_sub_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Начало FSM для выдачи подписки."""
    admin_id: int = kwargs["dp"]["admin_id"]
    if callback.from_user.id != admin_id: return
    
    await state.set_state(AdminState.waiting_give_sub)
    await callback.message.edit_text("⭐ Введите ID/USERNAME и ДНИ.\nПример: `1234567 30`")
    await callback.answer()

@admin_router.message(AdminState.waiting_give_sub)
async def admin_give_sub_proc(message: Message, state: FSMContext, **kwargs):
    """Обработка ввода данных для выдачи подписки."""
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["dp"]["db"]
    tm: TelethonManager = kwargs["dp"]["tm"]
    admin_id: int = kwargs["dp"]["admin_id"]
    
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
        
    await send_start_menu(message.from_user.id, bot, db, tm)

@admin_router.callback_query(F.data == "admin_create_promo")
async def cb_admin_create_promo_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Начало FSM для создания промокода."""
    admin_id: int = kwargs["dp"]["admin_id"]
    if callback.from_user.id != admin_id: return
    
    await state.set_state(AdminState.waiting_promo_params)
    await callback.message.edit_text("🎁 Введите `ДНИ МАКС_ЮЗЕРОВ`.\nПример: `30 10`")
    await callback.answer()

@admin_router.message(AdminState.waiting_promo_params)
async def admin_create_promo_proc(message: Message, state: FSMContext, **kwargs):
    """Обработка ввода параметров промокода."""
    db: AsyncDatabase = kwargs["dp"]["db"]
    bot: Bot = kwargs["bot"]
    tm: TelethonManager = kwargs["dp"]["tm"]
    admin_id: int = kwargs["dp"]["admin_id"]
    
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
    await send_start_menu(message.from_user.id, bot, db, tm)

# --- QR АВТОРИЗАЦИЯ ---

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Инициация QR-авторизации и генерация QR-кода."""
    tm: TelethonManager = kwargs["dp"]["tm"]
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["dp"]["db"]
    qr_timeout: int = kwargs["dp"].get("qr_timeout", 120) 

    await callback.answer("🔄 Генерация QR-кода...")
    
    if callback.from_user.id in getattr(tm.store, 'active_workers', {}):
        return await callback.message.answer("⚠️ Уже есть активная сессия!")
    
    url, qr_obj = None, None
    try:
        url, qr_obj = await tm.start_qr_login(callback.from_user.id)
    except Exception as e:
        logger.error(f"Telethon QR Login start error: {e}")
        return await callback.message.answer(f"❌ Ошибка QR-авторизации: {e}")
    
    # ✅ QR ГЕНЕРАЦИЯ
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
    
    await callback.message.answer_photo(
        photo=InputFile.from_bytes(bio.getvalue(), filename="qr.png"),
        caption=f"📱 Отсканируйте QR-код!\n⏰ Действует **{qr_timeout}с**"
    )
    
    await state.set_state(TelethonAuth.waiting_for_qr)
    logger.info(f"QR started for {callback.from_user.id}")


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
