import asyncio
import logging
import random
import string
import qrcode
from io import BytesIO
from typing import Any

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

# ✅ ИМПОРТЫ ПРОЕКТА (раскомментируй когда подключишь)
from config import ADMIN_ID, QR_TIMEOUT
from telethon_manager import TelethonManager
from db import AsyncDatabase

# Настройка роутеров
user_router = Router()
admin_router = Router()
router = Router() # Главный роутер для errors_handler
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

# --- АДМИН-ПАНЕЛЬ ---

@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery):
    """Отображает Админ-Панель."""
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("❌ Доступ запрещён", show_alert=True)
    
    # Используем Markdown V2 для лучшего форматирования, если нужно.
    # Ваш текущий текст без специальных символов Markdown V2 (как **)
    text = """👑 Админ-Панель
Выберите действие:"""
    
    await callback.message.edit_text(text, reply_markup=get_admin_panel_kb())
    await callback.answer()

# --- ВЫДАЧА ПОДПИСКИ ---

@admin_router.callback_query(F.data == "admin_give_sub")
async def cb_admin_give_sub_start(callback: CallbackQuery, state: FSMContext):
    """Начало FSM для выдачи подписки."""
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.waiting_give_sub)
    await callback.message.edit_text("⭐ Введите ID/USERNAME и ДНИ.\nПример: `1234567 30`")
    await callback.answer()

@admin_router.message(AdminState.waiting_give_sub)
async def admin_give_sub_proc(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager):
    """Обработка ввода данных для выдачи подписки."""
    if message.from_user.id != ADMIN_ID: return
    
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
        
        # Уведомление пользователя
        try:
            await bot.send_message(user_id, f"🌟 **Вам выдана подписка на {days} дней!**")
        except TelegramForbiddenError:
            pass # Пользователь заблокировал бота
            
    except Exception as e:
        await message.answer(f"❌ Ошибка при выдаче подписки: {e}")
        
    await send_start_menu(message.from_user.id, bot, db, tm)

# --- ПРОМОКОДЫ ---

@admin_router.callback_query(F.data == "admin_create_promo")
async def cb_admin_create_promo_start(callback: CallbackQuery, state: FSMContext):
    """Начало FSM для создания промокода."""
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.waiting_promo_params)
    await callback.message.edit_text("🎁 Введите `ДНИ МАКС_ЮЗЕРОВ`.\nПример: `30 10`")
    await callback.answer()

@admin_router.message(AdminState.waiting_promo_params)
async def admin_create_promo_proc(message: Message, state: FSMContext, db: AsyncDatabase, bot: Bot, tm: TelethonManager):
    """Обработка ввода параметров промокода."""
    if message.from_user.id != ADMIN_ID: return
    
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
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, tm: TelethonManager, bot: Bot, db: AsyncDatabase):
    """Инициация QR-авторизации и генерация QR-кода."""
    await callback.answer("🔄 Генерация QR-кода...")
    
    if callback.from_user.id in tm.store.active_workers:
        return await callback.message.answer("⚠️ Уже есть активная сессия!")
    
    url, qr_obj = None, None
    try:
        # Получаем URL и объект сессии от TelethonManager
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
    
    # Отправка изображения
    await callback.message.answer_photo(
        photo=InputFile.from_bytes(bio.getvalue(), filename="qr.png"),
        caption=f"📱 Отсканируйте QR-код!\n⏰ Действует **{QR_TIMEOUT}с**"
    )
    
    await state.set_state(TelethonAuth.waiting_for_qr)
    # Здесь должна быть логика сохранения qr_obj в БД и запуск фонового процесса ожидания:
    # await db.set_temp_session(callback.from_user.id, qr_data=qr_obj)
    # asyncio.create_task(tm.check_qr_login(callback.from_user.id, qr_obj, bot, db, state))
    logger.info(f"QR started for {callback.from_user.id}")


# --- ЕДИНЫЙ ERROR HANDLER (Aiogram 3.x) ---

@router.errors()
async def errors_handler(event: ErrorEvent):
    """
    Обработчик всех исключений.
    Использует event.exception и event.update для определения контекста и логгирования.
    """
    exc = event.exception
    
    # Безопасное получение ID пользователя
    user_id = getattr(getattr(event.update, 'effective_user', None), 'id', 'неизвестно')
    
    # Логгируем в зависимости от типа ошибки
    if isinstance(exc, TelegramForbiddenError):
        # Включает BotBlocked, ChatNotFound, UserDeactivated
        logger.warning(f"🚫 Forbidden {user_id}: {exc}")
    elif isinstance(exc, TelegramBadRequest):
        # Ошибки, связанные с удаленными/измененными сообщениями
        logger.info(f"⚠️ BadRequest {user_id}: {exc}")
    elif isinstance(exc, TelegramAPIError):
        # Любые другие API ошибки
        logger.error(f"🌐 API {user_id}: {exc}")
    elif "sqlite" in str(exc).lower():
        # Ошибки базы данных (aiosqlite)
        logger.error(f"🗄️ DB {user_id}: {exc}")
    else:
        # Неизвестные ошибки (важно логгировать)
        logger.error(f"💥 UNKNOWN ERROR {user_id}: {exc}", exc_info=True)
        
    # Всегда возвращаем True, чтобы остановить распространение ошибки в Aiogram.
    return True

# --- ИНКЛЮД РОУТЕРОВ ---

# ✅ Для использования в main.py, включите роутеры в ваш Dispatcher:
# dp.include_router(user_router)
# dp.include_router(admin_router)
# dp.include_router(router) # Не забудьте включить роутер с обработчиком ошибок
