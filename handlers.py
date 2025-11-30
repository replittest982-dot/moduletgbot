import asyncio
import logging
import random
import string
import qrcode
from io import BytesIO
import os # ✅ ДОБАВЛЕН ДЛЯ РАБОТЫ С ФАЙЛАМИ СЕССИЙ
from typing import Any

from aiogram import Router, F, Bot
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
    # Состояния для входа по номеру/QR, включая 2FA
    waiting_phone_auth = State()
    waiting_auth_code = State()
    waiting_2fa_password = State() 

class AdminState(StatesGroup):
    waiting_give_sub = State()
    waiting_promo_params = State()

class TelethonAuth(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State() # Используется для 2FA (Облачный пароль)
    waiting_for_qr = State()
    qr_password = State() 

# --- ФУНКЦИИ КЛАВИАТУР ---

# НОВАЯ УПРОЩЕННАЯ КЛАВИАТУРА ДЛЯ ТЕСТИРОВАНИЯ
def get_test_auth_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой 'Вход'."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Вход (Начать)", callback_data="start_auth_choice")],
    ])

# КЛАВИАТУРА ВЫБОРА МЕТОДА
def get_auth_method_kb() -> InlineKeyboardMarkup:
    """Клавиатура для выбора метода авторизации."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📲 Вход по QR-коду", callback_data="auth_qr")],
        [InlineKeyboardButton(text="📞 Вход по Номеру", callback_data="auth_phone")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="show_main_menu_test")],
    ])

# --- ФУНКЦИЯ ОТПРАВКИ МЕНЮ ---

async def send_start_menu(user_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, 
                          admin_id: int, force_main: bool = False):
    """Отправляет главное меню пользователю (УПРОЩЕННЫЙ РЕЖИМ)."""
    
    text = "Нажмите 'Вход', чтобы начать процесс авторизации и проверить 2FA."
    keyboard = get_test_auth_menu_kb()

    try:
        await bot.send_message(user_id, text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error sending start menu to {user_id}: {e}")


# --- ОБРАБОТЧИК /START И ВЫБОР МЕТОДА ---

@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, **kwargs):
    bot: Bot = kwargs["bot"]
    db: AsyncDatabase = kwargs["db"]  
    tm: TelethonManager = kwargs["tm"]  
    admin_id: int = kwargs["admin_id"]
    
    await state.clear() 
    await send_start_menu(message.from_user.id, bot, db, tm, admin_id=admin_id)

@user_router.callback_query(F.data == "start_auth_choice")
@user_router.callback_query(F.data == "show_main_menu_test")
async def cb_start_auth_choice(callback: CallbackQuery):
    """
    Показывает пользователю выбор метода авторизации (QR или Номер).
    """
    await callback.message.edit_text(
        "Выберите метод входа в аккаунт:",
        reply_markup=get_auth_method_kb()
    )
    await callback.answer()

# --- ВХОД ПО НОМЕРУ (ПОЛНАЯ FSM С 2FA) ---

@user_router.callback_query(F.data == "auth_phone")
async def cb_auth_phone_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TelethonAuth.waiting_phone)
    await callback.message.edit_text("📞 Введите номер телефона (+7...):")
    await callback.answer()
    
@user_router.message(TelethonAuth.waiting_phone)
async def auth_phone_proc_phone(message: Message, state: FSMContext, **kwargs):
    tm: TelethonManager = kwargs["tm"]
    user_id = message.from_user.id
    
    phone = message.text.strip()
    
    try:
        send_code_hash = await tm.send_code(user_id, phone)
        await state.update_data(phone=phone, send_code_hash=send_code_hash)
        await state.set_state(TelethonAuth.waiting_code)
        await message.answer("🔑 Введите код, который пришел вам в Telegram:")
    except Exception as e:
        logger.error(f"Error sending code for {user_id}: {e}")
        await message.answer("❌ Ошибка при отправке кода. Проверьте номер и повторите.")
        await state.clear()
        # Возвращаемся к выбору метода
        await message.answer("Нажмите 'Вход (Начать)' еще раз.", reply_markup=get_test_auth_menu_kb())
    
@user_router.message(TelethonAuth.waiting_code)
async def auth_phone_proc_code(message: Message, state: FSMContext, **kwargs):
    tm: TelethonManager = kwargs["tm"]
    user_id = message.from_user.id
    code = message.text.strip()
    data = await state.get_data()
    
    phone = data.get("phone")
    send_code_hash = data.get("send_code_hash")
    
    try:
        result = await tm.sign_in(user_id, phone, send_code_hash, code)
        
        if result == "password_required":
            await state.set_state(TelethonAuth.waiting_password)
            await message.answer("🔒 На вашем аккаунте установлен Облачный пароль (2FA). Введите его:")
        elif result == "success":
            await state.clear()
            await message.answer("✅ Авторизация успешна! Аккаунт готов к работе.")
            await message.answer("Нажмите 'Вход (Начать)', чтобы вернуться в меню.", reply_markup=get_test_auth_menu_kb())
        else:
            raise Exception("Unknown sign_in result")

    except Exception as e:
        logger.error(f"Error signing in for {user_id} with code: {e}")
        await message.answer("❌ Неверный код или ошибка сервера. Повторите попытку.")
        
@user_router.message(TelethonAuth.waiting_password)
async def auth_phone_proc_password(message: Message, state: FSMContext, **kwargs):
    tm: TelethonManager = kwargs["tm"]
    user_id = message.from_user.id
    password = message.text.strip()
    
    try:
        await tm.check_password(user_id, password)
        await state.clear()
        await message.answer("✅ Авторизация успешна, 2FA пароль принят! Аккаунт готов к работе.")
        await message.answer("Нажмите 'Вход (Начать)', чтобы вернуться в меню.", reply_markup=get_test_auth_menu_kb())
        
    except Exception as e:
        logger.error(f"Error checking password for {user_id}: {e}")
        await message.answer("❌ Неверный облачный пароль (2FA). Повторите ввод:")


# --- QR АВТОРИЗАЦИЯ (с 2FA) ---

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, **kwargs):
    """
    Инициация QR-авторизации с агрессивным сбросом сессии.
    """
    tm: TelethonManager = kwargs["tm"]  
    bot: Bot = kwargs["bot"]
    qr_timeout: int = kwargs.get("qr_timeout", 120)  
    user_id = callback.from_user.id

    await callback.answer("🔄 Генерация QR-кода...")
    
    # 1. АГРЕССИВНЫЙ ФИКС: Сброс зависших сессий и рабочих процессов
    try:
        if user_id in getattr(tm.store, 'active_workers', {}):
            await tm.store.delete_worker(user_id)
        session_file = f"sessions/{user_id}.session"
        if os.path.exists(session_file): 
            os.remove(session_file)
            
        await callback.message.edit_text("⚠️ Предыдущие данные сессии сброшены. Генерирую новый QR-код...")
    except Exception:
        pass

    # 2. Попытка начать новую QR-сессию
    url, qr_obj = None, None
    try:
        url, qr_obj = await tm.start_qr_login(user_id)
    except Exception as e:
        return await callback.message.answer(f"❌ Критическая ошибка QR-авторизации: {e}")
    
    # 3. QR ГЕНЕРАЦИЯ
    bio = BytesIO()
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, 
                           box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(bio, 'PNG')
    except Exception as e:
        return await callback.message.answer("❌ Не удалось сгенерировать QR-код.")
    
    # 4. Отправка QR-кода
    await callback.message.answer_photo(
        photo=BufferedInputFile(bio.getvalue(), filename="qr.png"),
        caption=f"📱 Отсканируйте QR-код!\n⏰ Действует **{qr_timeout}с**"
    )
    
    # ПЕРЕХОД В СОСТОЯНИЕ ОЖИДАНИЯ QR/2FA
    await state.set_state(TelethonAuth.waiting_for_qr)
    # Здесь мы ждем либо успешного входа (который должен быть обработан внутри tm.py), либо ввода 2FA пароля.


# --- ОБРАБОТЧИК ПОСЛЕ QR-СКАНИРОВАНИЯ (2FA) ---

@user_router.message(TelethonAuth.waiting_for_qr)
async def cb_auth_qr_post_scan(message: Message, state: FSMContext, **kwargs):
    """
    Этот обработчик ловит любое сообщение, предполагая, что это 2FA пароль, если QR-сканирование прошло успешно,
    но аккаунт защищен.
    """
    tm: TelethonManager = kwargs["tm"]
    user_id = message.from_user.id
    
    password = message.text.strip()
    
    if password:
        try:
            # Пытаемся завершить вход (он мог зависнуть в ожидании 2FA)
            await tm.check_password(user_id, password) 
            await state.clear()
            await message.answer("✅ Авторизация успешна, 2FA пароль принят! Аккаунт готов к работе.")
            await message.answer("Нажмите 'Вход (Начать)', чтобы вернуться в меню.", reply_markup=get_test_auth_menu_kb())
            return
        except Exception:
            # Это не 2FA пароль или неверный пароль, или QR-сессия истекла
            pass 

    await message.answer("❌ Неверный пароль, или QR-сессия истекла. Повторите попытку, нажав 'Вход (Начать)'.")
    await state.clear()
    await message.answer("Выберите метод входа в аккаунт:", reply_markup=get_auth_method_kb())
    
# --- АДМИН-ПАНЕЛЬ (УКОРОЧЕНЫ, НО ОСТАВЛЕНЫ, ТАК КАК ЭТО ФУНКЦИОНАЛ) ---

def get_admin_panel_kb() -> InlineKeyboardMarkup:
    """Клавиатура для Админ-Панели."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="⭐ Выдать подписку", callback_data="admin_give_sub")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="show_main_menu_test")]
    ])

# Здесь сохранены только заглушки для Admin-Panel, чтобы не усложнять код.
@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery, **kwargs):
    admin_id: int = kwargs["admin_id"]  
    if callback.from_user.id != admin_id: return await callback.answer("❌ Доступ запрещён", show_alert=True)
    await callback.message.edit_text("👑 Админ-Панель", reply_markup=get_admin_panel_kb())
    await callback.answer()

@admin_router.callback_query(F.data == "admin_give_sub")
async def cb_admin_give_sub_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.message.edit_text("⭐ Введите ID/USERNAME и ДНИ.")
    await state.set_state(AdminState.waiting_give_sub)
    await callback.answer()

@admin_router.message(AdminState.waiting_give_sub)
async def admin_give_sub_proc(message: Message, state: FSMContext, **kwargs):
    await message.answer("✅ Подписка выдана (заглушка).")
    await state.clear()
    await cb_start_auth_choice(message.as_callback_query(F.data.in_({"start_auth_choice"})))

@admin_router.callback_query(F.data == "admin_create_promo")
async def cb_admin_create_promo_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.message.edit_text("🎁 Введите `ДНИ МАКС_ЮЗЕРОВ`.")
    await state.set_state(AdminState.waiting_promo_params)
    await callback.answer()

@admin_router.message(AdminState.waiting_promo_params)
async def admin_create_promo_proc(message: Message, state: FSMContext, **kwargs):
    await message.answer("✅ Промокод создан (заглушка).")
    await state.clear()
    await cb_start_auth_choice(message.as_callback_query(F.data.in_({"start_auth_choice"})))

# --- ЕДИНЫЙ ERROR HANDLER ---

@router.errors()
async def errors_handler(event: ErrorEvent):
    # Стандартный обработчик ошибок
    exc = event.exception
    user_id = getattr(getattr(event.update, 'effective_user', None), 'id', 'неизвестно')
    
    if isinstance(exc, TelegramForbiddenError):
        logger.warning(f"🚫 Forbidden {user_id}: {exc}")
    elif isinstance(exc, TelegramBadRequest):
        logger.info(f"⚠️ BadRequest {user_id}: {exc}")
    elif isinstance(exc, TelegramAPIError):
        logger.error(f"🌐 API {user_id}: {exc}")
    else:
        logger.error(f"💥 UNKNOWN ERROR {user_id}: {exc}", exc_info=True)
        
    return True
