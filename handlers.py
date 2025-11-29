import logging
import asyncio
import os
import qrcode
from io import BytesIO
import textwrap
from dateutil import parser

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# УБРАНЫ ТОЧКИ ПЕРЕД ИМЕНАМИ МОДУЛЕЙ
from config import ADMIN_ID, SUPPORT_BOT_USERNAME, TARGET_CHANNEL_URL, TEMP_DIR, QR_TIMEOUT, MOSCOW_TZ
from utils import TelethonAuth, AdminState, UserState, DropUserState, GlobalStorage, check_valid_phone, format_drop_report
from db import AsyncDatabase
from telethon_manager import TelethonManager

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
    if not is_subscribed and not is_admin:
        kb.append([InlineKeyboardButton(text="Доступ к боту закрыт (Подписка)", callback_data="info_sub")])
    else:
        if not is_telethon_active:
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
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def send_start_menu(chat_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, is_initial_check: bool = False):
    uid = chat_id
    is_subscribed_bool, sub_status_text = await db.get_subscription_status(uid, ADMIN_ID)
    user_data = await db.get_user(uid)
    is_telethon_active = user_data['telethon_active'] if user_data else False
    is_worker_running = uid in tm.store.active_workers
    has_progress = uid in tm.store.process_progress
    is_admin = uid == ADMIN_ID
    
    if is_initial_check and not is_subscribed_bool and not is_admin:
        try:
            member = await bot.get_chat_member(TARGET_CHANNEL_URL, uid)
            if member.status not in ['member', 'creator', 'administrator']:
                return await bot.send_message(chat_id, f"⚠️ Подпишитесь на: **{TARGET_CHANNEL_URL}**", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{TARGET_CHANNEL_URL.lstrip('@')}")], [InlineKeyboardButton(text="Я подписался", callback_data="check_subscription")]]), parse_mode='Markdown')
        except Exception: pass

    await bot.send_message(chat_id, f"🤖 Привет!\n**Подписка:** {sub_status_text}", parse_mode='Markdown', reply_markup=get_main_menu_kb(is_subscribed_bool, is_telethon_active, is_worker_running, has_progress, is_admin))

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
    await callback.message.edit_text("📞 Введите номер телефона (`+7...`):", parse_mode='Markdown')

@user_router.message(StateFilter(TelethonAuth.PHONE))
async def auth_get_phone(message: Message, state: FSMContext, tm: TelethonManager, **kwargs):
    phone = check_valid_phone(message.text)
    if not phone: return await message.answer("❌ Неверный формат.")
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
    try:
        url, img = await tm.start_qr_login(callback.from_user.id)
    except Exception as e: return await callback.message.answer(f"Ошибка: {e}")
    
    bio = BytesIO()
    if img: img.save(bio, 'PNG')
    else: qrcode.make(url).save(bio, 'PNG')
    bio.seek(0)
    
    await callback.message.answer_photo(bio, caption=f"Отсканируйте код. Действует {QR_TIMEOUT}с.")
    await state.set_state(TelethonAuth.WAITING_FOR_QR_LOGIN)
    
    data = tm.store.temp_data.get(callback.from_user.id)
    success, msg = await asyncio.wait_for(tm.check_qr_login(callback.from_user.id, data['qr_login_data'], data['client']), timeout=QR_TIMEOUT+10)
    
    if success:
        await state.clear()
        await send_start_menu(callback.from_user.id, bot, db, tm)
    elif "⚠️" in msg:
        await state.set_state(TelethonAuth.QR_PASSWORD)
        await callback.message.answer(msg)
    else:
        await state.clear()
        await callback.message.answer(msg)

# --- Worker ---
@user_router.callback_query(F.data == "worker_start")
async def cb_work_start(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    if await tm.start_client_task(callback.from_user.id): await callback.answer("Запущен.")
    else: await callback.answer("Ошибка.", show_alert=True)
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "worker_stop")
async def cb_work_stop(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await tm.stop_worker(callback.from_user.id)
    await callback.answer("Остановлен.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "auth_logout")
async def cb_logout(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await tm.stop_worker(callback.from_user.id, delete_session=True)
    await callback.answer("Сессия удалена.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

# --- Promo ---
@user_router.callback_query(F.data == "user_promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.set_state(UserState.WAITING_FOR_PROMO_CODE)
    await callback.message.edit_text("Введи промокод:")

@user_router.message(StateFilter(UserState.WAITING_FOR_PROMO_CODE))
async def promo_proc(message: Message, state: FSMContext, db: AsyncDatabase, bot: Bot, tm: TelethonManager, **kwargs):
    success, msg = await db.apply_promo_code(message.from_user.id, message.text.strip())
    await message.answer(msg, parse_mode='Markdown')
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)

# --- Admin ---
@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery, **kwargs):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer("Админ: `/create_promo`", parse_mode='Markdown')
    await callback.answer()

@admin_router.message(Command("create_promo"))
async def cmd_mk_promo(message: Message, state: FSMContext, **kwargs):
    if message.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.CREATING_PROMO_CODE)
    await message.answer("Формат: `КОД ДНИ МАКС_ЮЗЕРОВ` (пример: `TEST 30 10`)", parse_mode='Markdown')

@admin_router.message(StateFilter(AdminState.CREATING_PROMO_CODE))
async def proc_mk_promo(message: Message, state: FSMContext, db: AsyncDatabase, **kwargs):
    parts = message.text.split()
    if len(parts) != 3: return await message.answer("Неверный формат.")
    if await db.create_promo_code(parts[0], int(parts[1]), int(parts[2])):
        await message.answer("✅ Создан.")
        await state.clear()
    else: await message.answer("Ошибка (код существует?).")

# --- Drop System ---
@drop_router.message(Command("numb"))
async def d_numb(message: Message, db: AsyncDatabase, store: GlobalStorage, **kwargs):
    pc = store.drop_mapping.get((message.chat.id, message.message_thread_id or 0))
    if not pc: return await message.answer("ПК не привязан (.пкстарт).")
    ph = await db.create_drop_session(pc, message.from_user.id)
    await message.answer(f"✅ **{pc}**: Жду номер. `{ph}`", parse_mode='Markdown')

@drop_router.message(Command("num"))
async def d_num(message: Message, db: AsyncDatabase, **kwargs):
    args = message.text.split()
    if len(args) < 2: return
    sess = await db.get_latest_drop_session(message.from_user.id)
    if sess:
        await db.update_drop_session(sess['phone'], new_phone=args[1], status="дайте номер")
        await message.answer(f"✅ Номер {args[1]} для {sess['pc_name']}")

async def d_status(msg, st, db):
    sess = await db.get_latest_drop_session(msg.from_user.id)
    if sess:
        await db.update_drop_session(sess['phone'], status=st)
        await msg.answer(f"✅ {sess['pc_name']}: {st}")

@drop_router.message(Command("vstal"))
async def d_vstal(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "в работе", db)
@drop_router.message(Command("error"))
async def d_error(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "error", db)
@drop_router.message(Command("slet"))
async def d_slet(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "slet", db)
@drop_router.message(Command("povt"))
async def d_povt(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "повтор", db)

@drop_router.message(Command("report_last"))
async def d_rep(message: Message, db: AsyncDatabase, **kwargs):
    sess = await db.get_latest_drop_session(message.from_user.id)
    if sess: await message.answer(format_drop_report(sess), parse_mode='Markdown')
