import asyncio
import logging
import random
import string
import qrcode
from io import BytesIO
from typing import Any

from aiogram import Router, F, Bot
# ✅ ИСПРАВЛЕН ИМПОРТ: InputFile заменен на BufferedInputFile
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command, CommandStart 
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

# ✅ ИМПОРТЫ ПРОЕКТА
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
    waiting_phone_auth = State() # Добавлено для входа по номеру
    waiting_auth_code = State()  # Добавлено для входа по номеру

class AdminState(StatesGroup):
    waiting_give_sub = State()
    waiting_promo_params = State()
    # ... (остальные)

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

# ✅ ПЕРЕРАБОТАНО: Клавиатура для пользователя, полностью соответствует скриншоту
def get_user_menu_kb(is_admin: bool, is_active: bool) -> InlineKeyboardMarkup:
    # Кнопки в первом ряду (Подписка: Активна / Справка / Задать вопрос)
    row1_text = "Подписка: Активна" if is_active else "Подписка: Неактивна"
    kb = [
        [
            InlineKeyboardButton(text=row1_text, callback_data="user_subscription_status"),
            InlineKeyboardButton(text="Справка", callback_data="user_help"),
            InlineKeyboardButton(text="Задать в...", callback_data="user_ask_question"),
        ],
        # Кнопки авторизации
        [
            InlineKeyboardButton(text="📲 Вход по QR-коду", callback_data="auth_qr"),
            InlineKeyboardButton(text="🔑 Вход по Номеру", callback_data="auth_phone"),
        ],
        # Промокод и Админ
        [
            InlineKeyboardButton(text="🎁 Активировать Промокод", callback_data="activate_promo"),
        ]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="👑 Админ-Панель", callback_data="admin_panel")])
        
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def send_start_menu(user_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, force_main: bool = False):
    """
    ✅ ФИКС: Рабочая логика для отображения меню.
    """
    # Предполагаем, что admin_id доступен в dp, но в этой функции мы его не берем,
    # поэтому используем хардкод (вам нужно заменить на ваш admin_id)
    ADMIN_ID_HACK = 7868097991 # Замените на фактический ADMIN_ID
    
    is_admin = user_id == ADMIN_ID_HACK
    
    # ⚠️ ТУТ ДОЛЖНА БЫТЬ ПРОВЕРКА ПОДПИСКИ ИЗ DB
    is_active = True # Замените на await db.check_subscription(user_id)
    
    text = "Привет!\n"
    keyboard = get_user_menu_kb(is_admin, is_active)

    try:
        await bot.send_message(user_id, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error sending start menu to {user_id}: {e}")


# --- ОБРАБОТЧИК /START ---

@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, **kwargs):
    """
    ✅ ФИКС ИЗВЛЕЧЕНИЯ: db и tm берутся напрямую из kwargs.
    Обрабатывает команду /start и вызывает главное меню.
    """
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["db"]  
    tm: TelethonManager = kwargs["tm"]  
    
    await state.clear() 
    await send_start_menu(message.from_user.id, bot, db, tm)


# --- НОВЫЕ ОБРАБОТЧИКИ КНОПОК МЕНЮ (ЗАГЛУШКИ) ---

@user_router.callback_query(F.data == "user_subscription_status")
@user_router.callback_query(F.data == "user_help")
@user_router.callback_query(F.data == "user_ask_question")
async def cb_user_info_handlers(callback: CallbackQuery):
    await callback.answer("⚙️ Эта функция пока не реализована.", show_alert=True)
    
@user_router.callback_query(F.data == "activate_promo")
async def cb_activate_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_promo)
    await callback.message.answer("🎁 Введите промокод:")
    await callback.answer()

@user_router.message(UserState.waiting_promo)
async def activate_promo_proc(message: Message, state: FSMContext, **kwargs):
    # Тут будет логика проверки промокода
    await message.answer(f"❌ Промокод `{message.text}` недействителен.")
    await state.clear()
    
# --- ВХОД ПО НОМЕРУ (ЗАГЛУШКИ) ---
@user_router.callback_query(F.data == "auth_phone")
async def cb_auth_phone_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.waiting_phone_auth)
    await callback.message.answer("📞 Введите номер телефона (+7...):")
    await callback.answer()
    
@user_router.message(UserState.waiting_phone_auth)
async def auth_phone_proc(message: Message, state: FSMContext):
    # Тут будет логика отправки кода через Telethon
    await state.set_state(UserState.waiting_auth_code)
    await message.answer("🔑 Введите код, который пришел вам в Telegram:")


# --- АДМИН-ПАНЕЛЬ ---
# ... (остальной код для admin_router)

@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery, **kwargs):
    """
    ✅ ФИКС ИЗВЛЕЧЕНИЯ: admin_id берется напрямую из kwargs.
    Отображает Админ-Панель.
    """
    admin_id: int = kwargs["admin_id"]  # <-- ИСПРАВЛЕНО
    if callback.from_user.id != admin_id:
        return await callback.answer("❌ Доступ запрещён", show_alert=True)
    
    text = """👑 Админ-Панель
Выберите действие:"""
    
    await callback.message.edit_text(text, reply_markup=get_admin_panel_kb())
    await callback.answer()

# ... (остальной код для admin_router и FSM) ...

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
    # ... (логика выдачи подписки) ...
    
    # Placeholder:
    await message.answer("✅ Подписка выдана (заглушка).")
    await state.clear()
    await send_start_menu(message.from_user.id, bot, db, tm)
    
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
    # ... (логика создания промокода) ...
    
    # Placeholder:
    await message.answer("✅ Промокод создан (заглушка).")
    await state.clear()
    await send_start_menu(message.from_user.id, bot, db, tm)
    

# --- QR АВТОРИЗАЦИЯ ---

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, **kwargs):
    """
    ✅ ФИКС: tm, db, qr_timeout берутся напрямую из kwargs.
    ✅ ФИКС: Использование BufferedInputFile для отправки QR-кода.
    Инициация QR-авторизации и генерация QR-кода.
    """
    tm: TelethonManager = kwargs["tm"]  
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["db"]  
    qr_timeout: int = kwargs.get("qr_timeout", 120)  

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
    
    # ✅ ИСПРАВЛЕНО: Использование BufferedInputFile
    await callback.message.answer_photo(
        photo=BufferedInputFile(bio.getvalue(), filename="qr.png"),
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
